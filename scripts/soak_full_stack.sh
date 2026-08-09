#!/usr/bin/env bash
# Long soak against the FULL stack -- the configuration the segfault was seen in.
#                                                                        (SIM-04 soak, arm C)
# SITL only. GPU 0 only; GPU 1 is deliberately untouched.
#
# WHY THIS ARM EXISTS. The isolated capture soak (scripts/soak_capture.py) survived 6000
# compress=true calls without incident, which does NOT clear the capture path -- it says the
# capture path ALONE is not enough. The observed crash carried a MAVLink `hil` EPIPE, so PX4
# was connected, and the wrapper was polling Scene + DepthPlanar + GPU-LiDAR concurrently.
# That concurrency is the part this reproduces: several consumers pulling from the render
# path while MAVLink runs alongside it.
#
# 2026-08-08, SIM-23 -- WHAT THIS SOAK GOT WRONG, and what was added.
# The crash was root-caused to ALidarCamera::ProcessCapturedBuffers: a GPU-LiDAR readback that
# comes back EMPTY while upstream reports the frame ready. `simGetImages` never drives that
# path, so this soak's 74,253-call survival could not have reproduced it and should not have
# been read as evidence against the hypothesis -- the arm was pointed at the wrong path, not
# at a wrong idea. A GPU-LiDAR arm (LIDAR_ARM=1, default on) now runs alongside the image load.
#
# The pass condition changed too. With patches/cosys-airsim/0006 an empty readback is survivable,
# so "the simulator is still up" no longer distinguishes "the fault never happened" from "the
# fault happened and was handled". The loop counts `readback incomplete` and reports it either
# way; a soak that ends with drops > 0 and the simulator alive is the strongest result available.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${OUT:-$REPO/out/soak}"; mkdir -p "$OUT"
MAX_SECONDS="${MAX_SECONDS:-5400}"      # 90 min -- the observed failure was at ~57
RPC_COMPRESS="${RPC_COMPRESS:-true}"    # also drive the indexing path from a client
log(){ printf '\033[36m[soak]\033[0m %s\n' "$*"; }

log "bringing up the full stack (GPU 0)"
bash "$REPO/scripts/sim_up.sh" > "$OUT/fullstack.up.log" 2>&1 || { log "bring-up FAILED"; exit 1; }
bash "$REPO/scripts/build_airsim_wrapper.sh" > "$OUT/fullstack.build.log" 2>&1 || { log "wrapper build FAILED"; exit 1; }
docker exec -d sim-ros2 bash -lc '
  source /opt/ros/jazzy/setup.bash
  source /airsim_root/ros2/install/setup.bash
  source /ros2_ws/install/setup.bash 2>/dev/null
  ros2 launch bringup perception.launch.py > /tmp/perception.log 2>&1'
sleep 25
log "perception up; adding an RPC capture load (compress=$RPC_COMPRESS)"

docker run -d --name soak-rpc \
  --network container:sim-unreal --ipc container:sim-unreal \
  -v "$REPO/vendor/Cosys-AirSim/PythonClient:/client:ro" \
  -v "$REPO/scripts:/scripts:ro" -v "$OUT:/out" \
  drone-sim/airsim-client:1 python3 /scripts/_soak_capture.py \
    --progress /out/fullstack_rpc.jsonl --compress "$RPC_COMPRESS" \
    --vehicle PX4 --camera front_center \
    --max-calls 2000000 --max-seconds "$MAX_SECONDS" --stats-every 25 >/dev/null

# The GPU-LiDAR arm.                                                          (added SIM-23)
# The image load above is NOT where the segfault lives. It was aimed there in 2026-08-03, this
# soak survived 74,253 calls, and that was read as evidence against the hypothesis -- but the
# crash is on ALidarCamera::ProcessCapturedBuffers, a path `simGetImages` never drives. Pulling
# getGPULidarData keeps that path as hot as the sensor allows.
if [ "${LIDAR_ARM:-1}" = "1" ]; then
  log "adding a GPU-LiDAR load (the path the crash is actually on)"
  for f in _soak_gpulidar.py airsim_rpc_client.py; do
    docker cp "$REPO/scripts/$f" sim-ros2:/tmp/ >/dev/null \
      || { log "FATAL: could not copy $f into sim-ros2"; exit 1; }
  done
  docker exec -d sim-ros2 bash -lc \
    "cd /tmp && python3 /tmp/_soak_gpulidar.py --progress /tmp/soak_gpulidar.jsonl \
       --max-seconds $MAX_SECONDS --stats-every 50 > /tmp/soak_gpulidar.log 2>&1"

  # PROVE it started. `docker exec -d` returns 0 whenever the CONTAINER exists, even when the
  # command cannot run at all -- the same trap collision_witness.py documents. A silently absent
  # LiDAR arm leaves this soak driving only the image path, which is EXACTLY the configuration
  # whose result was misread in 2026-08-03. Failing loudly here is the entire point of the arm.
  armed=""
  for _ in $(seq 1 15); do
    docker exec sim-ros2 test -s /tmp/soak_gpulidar.jsonl 2>/dev/null && { armed=1; break; }
    sleep 1
  done
  if [ -z "$armed" ]; then
    log "FATAL: the GPU-LiDAR arm produced no output within 15 s -- it is not running."
    docker exec sim-ros2 sh -c 'tail -20 /tmp/soak_gpulidar.log 2>/dev/null' 2>&1 | sed 's/^/    /'
    log "       Re-run with LIDAR_ARM=0 to soak the image path only, knowing that arm cannot"
    log "       reproduce the ProcessCapturedBuffers crash."
    docker rm -f soak-rpc >/dev/null 2>&1
    exit 1
  fi
  log "  GPU-LiDAR arm confirmed producing samples"
fi

START=$(date +%s)
DROPS=0
log "soaking for up to $((MAX_SECONDS/60)) min; sampling every 60 s"
while :; do
  NOW=$(date +%s); EL=$((NOW-START))

  # The 0006 signal.                                                        (added SIM-23)
  # An empty readback is no longer fatal, so "still running" stopped being the whole answer: the
  # question is whether the CONDITION occurred and was survived. Without this counter, a soak
  # that caught one would look identical to a soak that caught none -- which is exactly how the
  # image-path arm was misread in 2026-08-03.
  #
  # Counted BEFORE the liveness check, and deliberately so. It used to be counted after, which
  # meant the death branch below reported a number up to 60 s stale -- or 0, straight from the
  # initialiser, if the simulator died in the first minute. Crash-WITH-drops is the single most
  # informative outcome this counter exists to capture, and that was the one case it got wrong.
  # `docker logs` still works on a dead container, so there is no reason to read it late.
  # scripts/lidar_drops.py owns the marker string and the container name; see its header.
  DROPS=$(python3 "$REPO/scripts/lidar_drops.py" 2>/dev/null || echo -1)

  ALIVE=$(docker inspect -f '{{.State.Running}}' sim-unreal 2>/dev/null || echo missing)
  if [ "$ALIVE" != "true" ]; then
    log "SIMULATOR DIED after ${EL}s ($((EL/60)) min); readback drops: $DROPS"
    docker logs --tail 80 sim-unreal 2>&1 | grep -iE 'assertion|out of bounds|signal|fatal|EPIPE|error: 32' \
      | tail -10 | sed 's/^/    /'
    docker logs --tail 200 sim-unreal > "$OUT/fullstack.crash.log" 2>&1
    docker logs sim-unreal 2>&1 | grep -i 'readback incomplete' | tail -3 | sed 's/^/    /'
    echo "{\"died_after_s\": $EL, \"rpc_compress\": \"$RPC_COMPRESS\", \"readback_drops\": $DROPS}" \
      > "$OUT/fullstack.result.json"
    break
  fi

  [ "$EL" -ge "$MAX_SECONDS" ] && { log "survived ${EL}s with no crash; readback drops: $DROPS"; \
    echo "{\"survived_s\": $EL, \"rpc_compress\": \"$RPC_COMPRESS\", \"readback_drops\": $DROPS}" \
      > "$OUT/fullstack.result.json"; break; }
  if [ $((EL % 300)) -lt 61 ]; then
    N=$(tail -1 "$OUT/fullstack_rpc.jsonl" 2>/dev/null | python3 -c 'import json,sys;print(json.loads(sys.stdin.read() or "{}").get("n","?"))' 2>/dev/null || echo "?")
    L=$(docker exec sim-ros2 sh -c 'tail -1 /tmp/soak_gpulidar.jsonl 2>/dev/null' 2>/dev/null \
          | python3 -c 'import json,sys;d=json.loads(sys.stdin.read() or "{}");print(str(d.get("n","?"))+" calls/"+str(d.get("short","?"))+" short")' 2>/dev/null || echo "?")
    log "  t=$((EL/60))m  rpc_calls=$N  lidar=$L  drops=$DROPS  sim=up"
  fi
  sleep 60
done
docker rm -f soak-rpc >/dev/null 2>&1
log "done; artifacts in $OUT"
