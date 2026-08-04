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

START=$(date +%s)
log "soaking for up to $((MAX_SECONDS/60)) min; sampling every 60 s"
while :; do
  NOW=$(date +%s); EL=$((NOW-START))
  ALIVE=$(docker inspect -f '{{.State.Running}}' sim-unreal 2>/dev/null || echo missing)
  if [ "$ALIVE" != "true" ]; then
    log "SIMULATOR DIED after ${EL}s ($((EL/60)) min)"
    docker logs --tail 80 sim-unreal 2>&1 | grep -iE 'assertion|out of bounds|signal|fatal|EPIPE|error: 32' \
      | tail -10 | sed 's/^/    /'
    docker logs --tail 200 sim-unreal > "$OUT/fullstack.crash.log" 2>&1
    echo "{\"died_after_s\": $EL, \"rpc_compress\": \"$RPC_COMPRESS\"}" > "$OUT/fullstack.result.json"
    break
  fi
  [ "$EL" -ge "$MAX_SECONDS" ] && { log "survived ${EL}s with no crash"; \
    echo "{\"survived_s\": $EL, \"rpc_compress\": \"$RPC_COMPRESS\"}" > "$OUT/fullstack.result.json"; break; }
  if [ $((EL % 300)) -lt 61 ]; then
    N=$(tail -1 "$OUT/fullstack_rpc.jsonl" 2>/dev/null | python3 -c 'import json,sys;print(json.loads(sys.stdin.read() or "{}").get("n","?"))' 2>/dev/null || echo "?")
    log "  t=$((EL/60))m  rpc_calls=$N  sim=up"
  fi
  sleep 60
done
docker rm -f soak-rpc >/dev/null 2>&1
log "done; artifacts in $OUT"
