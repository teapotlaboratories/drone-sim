# 2026-07-29 — D-01: containerized Lane A vs native, and the PX4 prompt-spin defect

**Task.** Close `D-01` — prove the Lane A Docker image reproduces the native `P0-07` result,
per the reproducible-as-Docker project goal.

**Outcome.** `D-01` closed. On a normal container runtime the image is **native-equivalent**
(aggregate RTF 0.9967 vs 1.0000). The apparent flakiness chased for most of two sessions was
caused by **one missing line in PX4 upstream** plus **three of my own instrumentation bugs**.

**Headline numbers:**

| Configuration | Aggregate RTF | Topic rate | Sensor TIMEOUTs | Instantaneous dips <0.95 |
|---|---|---|---|---|
| Native (no container runtime) | **1.0000** | 100.02 Hz | 1 in 3 runs | 1 of 8,791 |
| **Host podman** (no nesting) | **0.9967** | 99.74 Hz | 0 | **0 of 2,930** |
| Nested Docker (this dev box) | 0.9767 | 97.2 Hz | 0 in 5 runs | 655 of 2,907 |

---

## 1. The root cause of almost everything: PX4 busy-spins its prompt

**`platforms/posix/src/px4/common/px4_daemon/pxh.cpp`** enters non-canonical terminal mode
without setting `VMIN`:

```c
term.c_lflag &= ~ICANON;        // non-canonical mode...
term.c_lflag &= ~ECHO;
tcsetattr(0, TCSANOW, &term);   // ...but c_cc[VMIN] is never set
...
int c = getchar();
bool update_prompt = true;
switch (c) { case EOF: break; }          // no exit, no sleep
if (update_prompt) { _clear_line(); _print_prompt(); }
```

POSIX requires setting `VMIN`/`VTIME` when clearing `ICANON`. PX4 does not, so whether
`getchar()` blocks depends entirely on what stdin happens to be:

| stdin | `VMIN` | Behaviour |
|---|---|---|
| normal terminal | 1 | blocks — PX4 behaves (why humans never see this) |
| pipe / redirect | n/a | `getchar()` returns `EOF` immediately → **spin** |
| `screen` pty | **0** | `read()` returns 0 bytes immediately → **spin** |

Measured: the 10-byte unit `pxh> ` + `ESC[2K` + `\r` emitted **31,895,659 times in 22
seconds** — ~1.45 M/s, ~14.5 MB/s, **4.1 GB per 300 s run**, plus one CPU core fully
consumed.

Verified `screen`'s pty presents `min = 0; time = 0` while `script`'s presents `min = 1`, and
that this is unconditional (same with or without a parent tty). **So the defect is PX4's;
screen is behaving legally and merely fails to mask it.**

### What that one missing line caused

- 32 GB tmpfs filled → **RTF measurements silently lost mid-run** (native runs 3–5 recorded
  zero samples and were scored FAIL)
- Agent shell tooling broke with ENOSPC — every command returning exit 1, including `true`
- Background jobs appeared "killed" at ~23–25 s — output writes were failing
- Container runs pushed ~4 GB through `fuse-overlayfs`, producing the **sensor TIMEOUTs and
  RTF collapse that were initially blamed on containerization**

### Fix — launch layer, no source patch

```bash
screen -dmS px4sitl -L -Logfile <log> bash -c "stty min 1 time 0; make px4_sitl gz_x500"
```

`stty min 1` restores the blocking read PX4 assumes: **4.1 GB → ~28 KB per 300 s
(~46,000× less)**, no wasted core. `screen` additionally keeps the console attachable and
interactive — `screen -r px4sitl`, and `screen -X stuff "commander status\r"` works.

**Upstream-worthy.** Two one-line fixes: set `VMIN=1` when clearing `ICANON`, and treat
repeated `EOF` as "no interactive console" rather than spinning. Any non-interactive PX4 SITL
launch hits this — i.e. every CI system.

---

## 2. The metric was wrong: instantaneous vs aggregate RTF

Gazebo's `real_time_factor` field is an **instantaneous, short-window estimate** and is
extremely noisy. On the same run it swung 0.14 → 1.01 while the true ratio was 0.977.

I built the acceptance test on that field, then investigated its noise as though it were a
defect — reporting "sustained multi-second stalls" that did not exist.

**The correct metric was in the same message all along.** `sim_time` and `real_time` are both
published; their ratio over the run is the aggregate RTF:

```awk
/^sim_time \{/{st=1; next}  st && /sec:/{sim=$2; st=0}
/^real_time \{/{rt=1; next} rt && /sec:/{real=$2; rt=0}
END{ printf "AGGREGATE RTF = %.4f\n", (simN-sim0)/(realN-real0) }
```

**Cross-check that should have caught it earlier:** the `/fmu/out` publish rate tracked the
aggregate ratio exactly (97.2 Hz ≈ 97.7% of 100 Hz), not the instantaneous minimum. A sim
genuinely running at 0.25× for seconds would have collapsed the topic rate. It never did.

**CI consequence:** never assert on minimum instantaneous RTF. A healthy *native* run
produced a single transient at 0.503 out of 2,931 samples and would fail a
`min >= 0.95` gate. Assert on **aggregate RTF**, or on a percentile / N-consecutive-samples
rule.

---

## 3. Host podman test — the 2.3% is nesting, not containerization

Getting host execution working took several attempts; recording all of it because each
obstacle is environment-specific and will recur.

**`host-spawn` needs the HOST session bus.** This was the whole blocker:

```bash
# WRONG — the container's own bus; host-spawn silently no-ops (rc=0, no output, no execution)
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
# RIGHT
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/host/run/user/1000/bus
host-spawn /usr/bin/podman --version   # -> podman version 5.8.4
```

`distrobox enter` sets this for interactive sessions, which is why it works by hand and not
from a non-interactive agent shell. I concluded "the plumbing is dead" after seven failed
variations; it was misconfiguration.

**Other obstacles, in order hit:**

| Obstacle | Detail |
|---|---|
| Host has no `docker` | Bazzite 44 ships **podman 5.8.4** only |
| Host binary unusable in-container | `/run/host/usr/bin/podman` → `libsubid.so.5: cannot open shared object file` (Fedora binary, Ubuntu userspace) — and would run in the *container's* namespaces anyway |
| `runroot` length limit | podman: *"the specified runroot is longer than 50 characters"* (Unix socket path limit) → store on the external drive, runroot at `/tmp/pmrr` |
| Nested rootless `podman load` fails | *"insufficient UIDs or GIDs available in user namespace (requested 0:42 for /etc/gshadow)"* — the container is already in a userns; the **host** must do the load |
| SELinux | `bash: /smoke.sh: Permission denied` (rc=126) → bind mounts need `:z`. Staged the script on the external drive rather than relabel repo files |
| Backgrounded host process dies | `nohup ... &` via host-spawn is killed when host-spawn returns; run synchronously instead |

**Deliberately avoided:** the host's *live* podman store at
`~/.local/share/containers/storage` is shared via `/home/deck` and is what runs this
distrobox. Concurrent writes from two podman instances risk corrupting it, which would break
the container. Used an isolated `--root` on the external drive instead.

**Result:** host podman ran at **aggregate RTF 0.9967, 99.74 Hz, 0 TIMEOUTs, 0 errors, and
0 instantaneous dips out of 2,930** — versus 655 dips under nested Docker.

**Conclusion: the containerized stack is native-equivalent.** The 2.3% deficit belongs to
*this dev box's* nested-Docker-in-rootless-podman arrangement, not to containerization. For a
project whose goal is Docker reproducibility, that is the distinction that matters — I had
argued this test "changes no decision", which was wrong.

---

## 4. My own instrumentation bugs — three of them, same family

None broke the stack; all broke the ability to *observe* it, which is worse because it looks
like data.

| Bug | Effect |
|---|---|
| `grep 'fail: TIMEOUT'` (single space) | PX4 prints `fail:  TIMEOUT!` with a **double** space, and `failed:` for MAG. The assertion silently reported **0** while TIMEOUTs were occurring |
| Shell chain ending in `tail` | A **failed** Docker build reported exit 0; `docker images` was empty. Caught only by `ldd` showing an unresolved library and a stale binary mtime |
| `pkill -f 'gz sim'` | Matched the command line of the shell running it → cleanup killed its own parent. Symptom: inexplicable SIGTERM ~25 s after start. Fixed with the `[g]z sim` bracket trick |
| Logs in a container `mktemp` dir | `--rm` destroyed the evidence of every failing run. Now bind-mounted via `OUTDIR` |
| Sparse RTF sampling (1 / 20 s) | A 5 s event had ~25% chance of detection. Native's "5/5 clean" was sampling luck — with dense sampling native shows 1 TIMEOUT in 3 runs |

**Pattern:** I repeatedly changed the measurement on one arm of a comparison and forgot the
other, then drew conclusions from the mismatch. The native/container comparison was invalid
three separate times for this reason.

---

## 5. State at end

- `drone-sim/lane-a:v1.16.0`, 11.6 GB, all pins SHA-verified at build time, readable at
  `/etc/drone-sim-versions`
- Smoke harness launches PX4 under `screen` + `stty min 1`, logs survive `--rm` via `OUTDIR`,
  RTF sampled densely (~9.5 Hz) and asserted on the **aggregate** ratio
- Isolated host podman store at
  `/var/mnt/<uuid>/Developments/projects/drone-sim/podman-store` (12 GB) with the image
  loaded — reusable for future host-side runs without another transfer
- **Nothing committed.** Two days of work in the working tree; the commit window
  (Mon–Fri 08:00–18:00 PT) has been closed for most of the session

## 6. Next

1. Commit after 18:00 PT
2. `P0-13` — serve a VLM model, hello-VLM call (closes the 5th Phase 0 exit criterion)
3. `P0-14` / `P0-15` — finalize the lock, coupling-assertion script
4. Lane C `C-01` — pin a known-good Cosys-AirSim commit for UE5.5
5. Report the PX4 `VMIN` defect upstream
