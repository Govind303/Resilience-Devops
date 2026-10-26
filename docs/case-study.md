# Case study: one failure detected, one automated recovery

The project brief asks for at least one failure scenario detected and one successful automated
recovery. Both were produced by running the chaos gate (`monitoring/resilience_gate.py`) against
the same application code in two configurations.

**Environment:** local demo (`scripts/local_demo.py`) on the development laptop (Windows 11,
Java 26 runtime, Python 3.10). A small supervisor plays the role of systemd's `Restart=always`.
Probes every 0.5 s. The staging run through Jenkins and Ansible uses the same gate and thresholds
with the Ansible chaos playbooks; record its results below once it has been run on the VM.

## Scenario A: automated recovery (resilient build)

Command: `python scripts/local_demo.py`

| Experiment | What was done | What the gate measured | Result |
|---|---|---|---|
| kill-process | App process killed | Instance `7d1b2bdf` replaced by `30e12bfd`; longest outage **2.0 s** (limit 15 s); 97.3 % of probes OK | PASS |
| cpu-spike | Every CPU core busy-looped for 15 s | **100 %** of 36 requests OK; p95 latency **47 ms** (limit 1500 ms) | PASS |
| network-failure | Dependency black-holed for 12 s | **100 %** of 23 requests OK; **15** fallback responses served; back to UP afterwards | PASS |

**Gate passed: promotion allowed.** The kill test shows self-healing (the 2 s outage is the
restart delay). The network test shows graceful degradation: users kept getting answers, slower
(p95 828 ms, because each request waits for the dependency's timeout before falling back), while
`/health` reported DEGRADED.

## Scenario B: failure detected (fragile build)

Command: `python scripts/local_demo.py --fragile` (no auto-restart, no fallback). Same code,
same tests; only the resilience configuration differs.

| Experiment | What the gate measured | Result |
|---|---|---|
| kill-process | Process not replaced; **never recovered** within the 20 s window; 9.09 % of probes OK | FAIL |
| network-failure (separate run) | **0** fallback responses; only **34.78 %** of 23 requests OK (limit 95 %); outage 12.2 s | FAIL |
| cpu-spike (separate run) | 100 % OK, p95 47 ms | PASS |

**Gate failed: promotion blocked.** In the pipeline this failure makes Jenkins run
`ansible/rollback_staging.yml` (restore the last known-good build) and `scripts/alert.py`. The
CPU test still passes for the fragile build, which is correct: CPU load does not depend on restart
or fallback logic, so the gate does not flag it.

## What this shows

- All unit tests passed for both builds; only the chaos gate told them apart. Unit tests check
  that code is correct; the gate checks that the deployed service survives failure.
- Each fault is verified to have happened (a new process ID, fallback responses seen), so a chaos
  playbook that silently does nothing cannot make a fragile build pass.

## Staging results (Jenkins + Ansible)

To be filled in after the first pipeline run on the staging VM: build number, gate report
(`reports/chaos/*.json` archived by Jenkins), rollback log, and alert screenshot.
