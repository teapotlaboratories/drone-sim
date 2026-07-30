#!/usr/bin/env bash
# Lane A container smoke test — the D-01 acceptance criterion.
#
# Runs INSIDE the image and reproduces the native P0-07 result, so the two can be
# compared directly rather than settling for "it started":
#
#   native (2026-07-28, bare container):
#     0 Accel/Mag TIMEOUT over 300 s · 0 ERROR lines · RTF 1.000/1.000/1.000
#     24 /fmu/out/* topics · vehicle_local_position at 100.02 Hz (std dev 0.002 s)
#
# Usage (from the repo root):
#   docker run --rm -v "$PWD/tests/lane-a-smoke.sh:/smoke.sh:ro" \
#     drone-sim/lane-a:v1.16.0 bash /smoke.sh
#
# DURATION defaults to 300 s to match P0-07; override for a quick check.
set +u
DURATION=${DURATION:-300}
PX4=${PX4_DIR:-/opt/px4}
# OUTDIR can be bind-mounted so logs SURVIVE --rm. Without this, a failing run destroys
# its own evidence — which is exactly what happened on 2026-07-29: failures were counted
# but could never be located in time.
LOGDIR=${OUTDIR:-$(mktemp -d)}
mkdir -p "$LOGDIR"

echo "### pins baked into this image"
cat /etc/drone-sim-versions 2>/dev/null | sed 's/^/  /'

# The [b]racket trick is load-bearing, not style: `pkill -f 'gz sim'` also matches the
# command line of the shell running this script (which contains that literal string), so
# the cleanup kills its own parent. Symptom is an inexplicable SIGTERM/exit 144 seconds
# after start. Do not "simplify" the brackets away.
cleanup(){ screen -S px4sitl -X quit 2>/dev/null; pkill -f '[b]in/px4' 2>/dev/null; pkill -f '[g]z sim' 2>/dev/null; pkill -f '[M]icroXRCEAgent' 2>/dev/null; sleep 2; }
[ "${SMOKE_ATTACH:-0}" = "1" ] || trap cleanup EXIT   # never tear down a stack we only attached to

# Two modes, kept as functions so neither branch is a long unindented block:
#   default      — start our own PX4 + agent (single-container use)
#   SMOKE_ATTACH — a stack is already running (docker compose); verify THAT, rather than
#                  starting a second PX4/agent which would fight for ports 8888/14550 and
#                  prove nothing about the deployment under test.

# Sets BOOT_WAIT_S so callers can report it without depending on a leaked loop variable.
wait_for_boot() {
  local tries=$1 i
  for i in $(seq 1 "$tries"); do
    if grep -qa 'Startup script returned successfully' "$LOGDIR/px4.log" 2>/dev/null; then
      BOOT_WAIT_S=$((i * 2)); return 0
    fi
    sleep 2
  done
  return 1
}

start_stack() {
  echo "### starting XRCE agent"
  MicroXRCEAgent udp4 -p 8888 > "$LOGDIR/agent.log" 2>&1 &

  echo "### starting PX4 SITL headless (HEADLESS=$HEADLESS GZ_IP=$GZ_IP)"
  # --- Why this is launched under `screen` with `stty min 1` ----------------------------
  # PX4's pxh shell (platforms/posix/src/px4/common/px4_daemon/pxh.cpp) clears ICANON to
  # enter non-canonical mode but NEVER sets c_cc[VMIN]. POSIX requires setting VMIN/VTIME
  # when doing that. Most terminals happen to leave VMIN=1, so reads block and PX4 behaves.
  # But a piped stdin returns EOF immediately and screen's pty presents VMIN=0, so
  # `case EOF: break;` falls through to _clear_line() + _print_prompt() and the shell
  # BUSY-SPINS: ~1.45 MILLION writes/second, 4.1 GB per 300 s run, one CPU core consumed.
  # `stty min 1` restores the blocking read PX4 assumes: ~28 KB per 300 s.
  # `screen` additionally keeps the console attachable:  screen -r px4sitl
  screen -dmS px4sitl -L -Logfile "$LOGDIR/px4.log" \
    bash -c "stty min 1 time 0; cd $PX4 && HEADLESS=${HEADLESS:-1} GZ_IP=${GZ_IP:-127.0.0.1} make px4_sitl gz_x500"

  wait_for_boot 90 || { echo "FAIL: PX4 did not boot"; tail -20 "$LOGDIR/px4.log"; exit 1; }
  echo "  booted after ~${BOOT_WAIT_S}s"
}

attach_stack() {
  echo "### ATTACH mode — verifying the already-running stack (not starting PX4/agent)"
  wait_for_boot 60 || { echo "FAIL: no running PX4 found (is the stack up?)"; exit 1; }
  echo "  found a booted PX4 in $LOGDIR/px4.log"
}

if [ "${SMOKE_ATTACH:-0}" = "1" ]; then attach_stack; else start_stack; fi

sleep 10

echo "### /fmu/out topics"
N_TOPICS=$(ros2 topic list 2>/dev/null | grep -c '^/fmu/out/')
echo "  count: $N_TOPICS"

echo "### is the data MOVING? (two samples ~20 s apart)"
S1=$(timeout 15 ros2 topic echo --once /fmu/out/vehicle_local_position 2>/dev/null | grep -E '^timestamp:' | head -1)
sleep 20
S2=$(timeout 15 ros2 topic echo --once /fmu/out/vehicle_local_position 2>/dev/null | grep -E '^timestamp:' | head -1)
echo "  sample1: ${S1:-<none>}"
echo "  sample2: ${S2:-<none>}"

echo "### publish rate"
timeout 15 ros2 topic hz /fmu/out/vehicle_local_position 2>/dev/null | head -2 | sed 's/^/  /'

echo "### sampling real-time factor for ${DURATION}s"
# ONE long-lived subscriber, not a process per sample.
#
# The previous version ran `gz topic -e -n 1` every 20 s. Inside the container that is a
# fork+exec on fuse-overlayfs at the exact moment the reading is taken — i.e. the sampler
# perturbs the quantity it measures. Symptom: dips landing precisely on the 20 s sample
# boundaries (t=0,20,40) with `max` above 1.0, indicating a noisy instantaneous estimate.
# Native did the same thing on a much faster filesystem and read 0.9998 flat, which is
# exactly what you would expect if the spawn — not the sim — caused the dips.
# Dump the raw stats stream, parse afterwards. A chained
# `grep --line-buffered -A1 | grep -oE | awk` pipeline silently produced ZERO lines
# (verified empirically 2026-07-29) — the -A context grep eats the stream. Dumping is
# also cheap: ~16 KB per 10 s, i.e. <500 KB for a 300 s run, and ~9.5 samples/second
# instead of one every 20 s.
timeout "$DURATION" gz topic -e -t /world/default/stats > "$LOGDIR/stats.raw" 2>/dev/null

# Each message carries its own real_time, so use that as the timestamp rather than
# wall-clock sampling. `inrt` guards against matching sim_time's sec field.
awk '/^real_time \{/{inrt=1; next}
     inrt && /sec:/{rt=$2; inrt=0}
     /^real_time_factor:/{print rt, $2}' "$LOGDIR/stats.raw" > "$LOGDIR/rtf.log" 2>/dev/null

echo
echo "### ================ RESULTS ================"
# PX4 prints "Accel #0 fail:  TIMEOUT!" with a DOUBLE space, and "MAG #0 failed: TIMEOUT!"
# with "failed". A naive 'fail: TIMEOUT' pattern matches NEITHER and silently reports 0
# while TIMEOUTs are occurring — this assertion was broken exactly that way until
# 2026-07-29. Keep the tolerant pattern.
TIMEOUTS=$(grep -acE 'fail(ed)?: *TIMEOUT' "$LOGDIR/px4.log")
ERRORS=$(grep -aoE 'ERROR \[[a-z_]+\]' "$LOGDIR/px4.log" | wc -l)
echo "  /fmu/out topics   : $N_TOPICS          (native: 24)"
echo "  TIMEOUT failures  : $TIMEOUTS           (native: 0)"
echo "  ERROR lines       : $ERRORS           (native: 0)"
# AGGREGATE RTF is the metric that gates. Gazebo's instantaneous real_time_factor is a
# short-window estimate that swings 0.14-1.01 while the true ratio sits at ~0.98, so
# asserting on its MINIMUM fails healthy runs (a clean native run contains a lone 0.503
# sample out of 2,931). sim_time and real_time are both in the same message; their ratio
# over the run is the honest number. The instantaneous series is kept for DIAGNOSTICS only.
RTF_AGG=$(awk '/^sim_time \{/{st=1; next} st && /sec:/{sim=$2; st=0}
               /^real_time \{/{rt=1; next} rt && /sec:/{real=$2; rt=0}
               { if (sim!="" && real!="") { if (s0=="") {s0=sim; r0=real}; sN=sim; rN=real } }
               END{ if ((rN-r0) > 0) printf "%.4f", (sN-s0)/(rN-r0); else print "0" }' \
          "$LOGDIR/stats.raw" 2>/dev/null)
RTF_AGG=${RTF_AGG:-0}
echo "  RTF AGGREGATE     : $RTF_AGG            (native 1.0000 · host podman 0.9967)"
if [ -s "$LOGDIR/rtf.log" ]; then
  awk '{s+=$2; if(min==""||$2<min)min=$2; if($2>max)max=$2}
       END{printf "  RTF instantaneous : min %.3f / mean %.3f / max %.3f   (diagnostic only — NOT the gate)\n", min, s/NR, max}' "$LOGDIR/rtf.log"
  echo "  instantaneous samples below 0.95: $(awk '$2<0.95' "$LOGDIR/rtf.log" | wc -l) of $(wc -l < "$LOGDIR/rtf.log")"
else
  echo "  RTF instantaneous : NO SAMPLES"
fi
if [ "${SMOKE_ATTACH:-0}" = "1" ]; then
  # In ATTACH mode the stack runs in OTHER containers, so this container's process table
  # cannot see it. Reporting 0 here would look like a crash; the real liveness evidence is
  # the topic count and moving data above.
  echo "  alive at end      : n/a in ATTACH mode (stack runs in separate containers)"
else
  echo "  alive at end      : px4=$(pgrep -cf 'bin/px4') gz=$(pgrep -cf 'gz sim') agent=$(pgrep -cf MicroXRCEAgent)"
fi

# A count without the text is not actionable — always show what the errors actually were.
if [ "$ERRORS" != "0" ]; then
  echo "  --- ERROR lines in full ---"
  grep -aoE 'ERROR \[[a-z_]+\][^\x1b]{0,90}' "$LOGDIR/px4.log" | sed 's/^/    /' | sort -u
  echo "  --- PX4 boot-relative timestamps around the errors ---"
  grep -anE 'ERROR \[|fail(ed)?: *TIMEOUT' "$LOGDIR/px4.log" | head -8 | sed 's/^/    line /' 
fi

# Acceptance: match the native run. RTF floor 0.95 per versions.lock couplings.
PASS=1
[ "$N_TOPICS" -ge 24 ] || { echo "  ✗ fewer than 24 /fmu/out topics"; PASS=0; }
[ "$TIMEOUTS" = "0" ]  || { echo "  ✗ sensor TIMEOUTs present"; PASS=0; }
[ "$ERRORS" = "0" ]    || { echo "  ✗ ERROR lines present"; PASS=0; }
[ -n "$S1" ] && [ -n "$S2" ] && [ "$S1" != "$S2" ] || { echo "  ✗ data not moving"; PASS=0; }
awk -v m="$RTF_AGG" 'BEGIN{exit !(m>=0.95)}' || { echo "  ✗ AGGREGATE RTF $RTF_AGG below the 0.95 floor"; PASS=0; }

[ "$PASS" = "1" ] && echo "### PASS — container reproduces the native P0-07 result" \
                  || { echo "### FAIL"; exit 1; }
