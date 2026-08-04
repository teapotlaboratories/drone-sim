# Blocker — NVENC is unavailable on driver 610.43.03, so GPU video encode is unreachable

**Status:** `blocked` · **Established 2026-08-04** · **Owner decision required** ·
**Blocks:** `SIM-17` (1080p60 video via Pixel Streaming)

**This is the second capability lost to the same driver.** The first was Isaac Sim
([`history/isaac/driver-decision.md`](history/isaac/driver-decision.md)), which SIGSEGVs on
610.43.03 against its validated 580.65.06. A single host driver change would address both —
though Isaac Sim has since been **retired** from this project, which weakens that argument
rather than strengthening it. See §5.

---

## 1. What was being attempted, and why

Video capture from the simulator is capped at **~13–14 Hz at any resolution**. Every route
AirSim offers funnels through `RenderRequest::getScreenshot`, which waits for the next rendered
frame and then performs a **blocking GPU→CPU readback**.

Measured, two resolutions, RPC only (no ROS 2, no rosbag):

| capture | data | time per frame | rate |
|---|---|---|---|
| 960×540 | 1.56 MB | 71.1 ms | 14.1 Hz |
| 1920×1080 | 6.22 MB | 96.9 ms | 10.3 Hz |

Fitting those two points separates the costs: **~71 ms fixed, ~5 ms/MB**. Four times the data
costs only 26 ms more, so this is **latency-bound, not bandwidth-bound** — 6.22 MB over PCIe is
about a millisecond of real bandwidth. Through the ROS 2 wrapper and a rosbag it degrades
further, to **4.69 Hz**.

Real-time factor stayed **1.0** with capture running, so the engine is not struggling. The
round trip is the ceiling.

**AirSim's own recorder does not avoid it.** `FRecordingThread::Run()`
(`Recording/RecordingThread.cpp:124`) calls the same `getImages()` path. It removes the RPC and
rosbag hops, not the stall. Established by reading the source rather than by testing it.

Pixel Streaming was the proposed fix: encode the viewport with **NVENC on the GPU**, so frames
never cross PCIe uncompressed and the readback disappears entirely.

## 2. The blocker, established empirically

**NVENC cannot open an encode session on this host.**

```
$ ffmpeg -f lavfi -i testsrc2=size=1920x1080:rate=60 -frames:v 300 -c:v h264_nvenc ...
[h264_nvenc] OpenEncodeSessionEx failed: unsupported device (2): (no details)
[h264_nvenc] No capable devices found
```

### What this is NOT

Each of these was checked, because "GPU thing fails in container" has many boring causes and
this needed to be the interesting one:

| Suspected cause | Finding |
|---|---|
| Libraries not mounted | **Present** — `libnvidia-encode.so.610.43.03`, `libnvcuvid.so.610.43.03`, `libcuda.so.610.43.03`, all in `ldconfig` |
| Device nodes missing | **Present** — `/dev/nvidia0`, `nvidiactl`, `nvidia-uvm`, `nvidia-uvm-tools`, `nvidia-modeset` |
| No GPU access | **Works** — `nvidia-smi` reports the 3080 and driver 610.43.03 from inside the container |
| CUDA broken | **Works** — `ffmpeg -init_hw_device cuda=c:0` creates a context with no error |
| GPU lacks an encoder | **Has one** — `nvidia-smi` reports encoder session counters |
| Ran without `--gpus` | Caught: the **first** probe did exactly this and produced a false negative for the wrong reason. Re-run with `--gpus`; the failure persists. |

**CUDA initialises and NVENC does not.** That is a specific encoder-level refusal, not a
container plumbing problem.

### The detail that makes this frustrating

The driver **does** expose hardware H.264 encoding — through Vulkan. From the UE log:

```
LogVulkanRHI: Display:   - VK_KHR_video_encode_h264
LogVulkanRHI: Display:   - VK_KHR_video_encode_queue
LogVulkanRHI: Display:   - VK_KHR_video_encode_intra_refresh
```

**The hardware capability is present and nothing in the stack can reach it:**

- **UE 5.8** ships `NVCodecs` (NVENC), `AMFCodecs` (AMD), `VTCodecs` (Apple), `WMFCodecs`
  (Windows) and `LibVpxCodecs` (software). There is **no Vulkan-video-encode backend**, so
  PixelStreaming2's only NVIDIA path is the one that fails.
- **ffmpeg 6.1.1** (in `drone-sim/video`) offers `h264_nvenc` and VAAPI encoders, but
  **no `h264_vulkan`**. ffmpeg 7.1+ added Vulkan encoders.

## 3. Why Pixel Streaming cannot simply fall back

With NVENC unavailable, PixelStreaming2 would select `LibVpxCodecs` — **software VP8/VP9**.
That is worse than the status quo on both counts: it is CPU-bound at 1080p, and software
encoding needs the frames in system memory, **which reintroduces the readback the whole exercise
was meant to eliminate**.

So the fallback is not a degraded version of the fix. It is the original problem plus a
software encoder.

## 4. What this blocks

| Capability | State |
|---|---|
| 1080p60 video capture (`SIM-17`) | **blocked** — no reachable hardware encoder |
| Isaac Sim | **retired** — it was deferred on the **same driver** long before it was dropped |
| Perception imagery (640×480 → ROS 2) | **unaffected** — ~17–20 Hz, and it is what the flight code consumes |
| Flight, control, sensors, MCAP recording | **unaffected** |

**Nothing that flies is blocked.** This is a presentation-quality video ceiling and nothing
more.

## 5. Options

1. **Rebase the host to an R580-series driver.** *Owner-only* — the host is ostree-immutable and
   host `sudo` is unavailable here. Would very likely restore NVENC.
   **The "one change, two capabilities" argument this blocker was first written with no longer
   holds:** it also assumed the rebase would reopen Isaac Sim, and Isaac Sim has since been
   retired from the project. The rebase now buys back exactly one live capability, so it must
   be justified on video alone.
2. **Accept 960×540 at ~14 Hz** for video. Measured, works today, ~3× smoother than the 4.69 Hz
   currently produced at 1080p. **Recommended in the meantime.**
3. **ffmpeg 7.1+ with `h264_vulkan`** to reach the encode path the driver does expose. This
   would make *encoding* cheap but **does not fix the 71 ms readback**, so it cannot deliver
   60 fps capture. Useful for re-encoding, not for capture rate.
4. **Frame interpolation** to synthesise a smoother video. Honest only if the output is labelled
   as containing generated frames.

## 6. If the driver is changed, verify in this order

Cheapest disqualifying check first, so a bad driver is found in minutes rather than after a
day's work:

1. `ffmpeg -f lavfi -i testsrc2=... -c:v h264_nvenc` — encodes without
   `OpenEncodeSessionEx failed`.
2. **The whole simulator still comes up on the new driver** — `./scripts/sim_up.sh` reaches
   `stack up and origin verified`. The renderer is a Vulkan client of the same driver, so a
   driver swap can take away more than it gives; check the thing that flies before the thing
   that records.
3. Only then attempt `SIM-17`, whose remaining risks are unrelated to this blocker: headless
   viewport capture, and consuming a WebRTC stream to a file without a browser.

(An earlier version of this list had "Isaac Sim launches" as step 2, as the other half of the
trade. That step is gone: Isaac Sim is retired and no longer a reason to change anything.)
