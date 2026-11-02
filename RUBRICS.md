# Rubric Compliance Matrix

Project: **Network Monitoring and Alert System** (Mininet + Ryu + UDP telemetry sockets)

This file maps each rubric component of the SDN-Socket Programming mini project to
the code, documentation and measured evidence in this repository. Demo and viva
components are out of scope here (Initial Testing & Demo, and the demo/viva part of
Documentation).

Status key: **Met** = fully shown by the evidence listed. **Partly met** = implemented,
with a stated gap. Gaps are listed openly in section 3.

## 1. Summary

| Rubric component | Marks | Status | Evidence |
|---|---|---|---|
| Problem Understanding & Architecture | 2 | Met | README sections 1-2; Concepts & Guide docx |
| TCP/UDP Socket Implementation | 4 | Met | `telemetry_client.py`, `monitor_controller.py` `_telemetry_loop`, `telemetry_server.py` |
| Mininet + SDN Implementation | 4 | Met | `topology.py`, `monitor_controller.py` |
| Socket-SDN Integration | 3 | Met | `monitor_controller.py` `_evaluate` |
| Complete Application Functionality | 4 | Met | all four main files; `normal` scenario results |
| Dynamic SDN Functionality | 6 | Partly met | `monitor_controller.py` (monitoring and alerting only) |
| Challenging Scenario / Robustness | 4 | Met (detect and alert) | `telemetry_kill` and `abnormal` scenarios, `results/summary.md` |
| Performance Evaluation & Comparison | 5 | Met | `run_experiments.sh`, `evaluate.py`, `results/summary.md` |
| Documentation (written part) | - | Met | `README.md`, `results/`, this file |

## 2. Detail per component

### 2.1 Problem Understanding & Architecture (2)

Rubric: clear problem, objectives, architecture, components, communication flow,
expected network behaviour.

- Problem: a single signal can mislead (a busy link may be a download, a high CPU may be
  a heavy job), so the system fuses **network-layer** counters from the switch with
  **application-layer** telemetry from the hosts. README "What the project does".
- Architecture and communication flow: diagram and step-by-step flow in README
  "How it works" (polling, rate calculation, utilisation, correlation, debouncing).
- Components: four Mininet hosts, one Open vSwitch, Ryu controller, UDP collector,
  telemetry clients. README "Repository contents".
- Expected behaviour: README "Expected output" and the scenario table in "Evaluation".

### 2.2 TCP/UDP Socket Implementation (4)

- **Protocol: UDP datagram sockets**, low-level `socket.socket(AF_INET, SOCK_DGRAM)`.
- Client: `telemetry_client.py` sends one JSON datagram every 2 s per host with
  `host, seq, ts, cpu_pct, mem_pct, tx_bps, rx_bps`; send errors are caught (`OSError`).
- Server: `monitor_controller.py` `_telemetry_loop` binds UDP port 9999, receives with
  `recvfrom`, ignores undecodable datagrams, and keys state by the real source IP rather
  than a payload field (a host cannot claim another host's identity in the message).
- Multiple nodes and concurrency: four clients report simultaneously; the collector runs
  as a cooperative green thread (`hub.spawn`) next to the OpenFlow poller.
- Loss detection: sequence numbers; `telemetry_server.py` reports `lost_since_last`.
- Application-level message format: JSON with a sequence number and timestamp.
- Gap: the exchange is one-way (no acknowledgement or controller-to-host command), and
  the collector has known robustness gaps (see section 3).

### 2.3 Mininet + SDN Implementation (4)

- Topology (`topology.py`): four hosts and one OVS switch, 10 Mbps `TCLink` per host,
  `RemoteController`, OpenFlow 1.3, plus a root-namespace interface (10.0.0.254) as the
  collector address.
- Controller (`monitor_controller.py`): table-miss rule to the controller, MAC learning
  switch, and per-flow rules (priority 10, 10 s idle timeout) whose match exposes IPv4
  source, destination, protocol and UDP destination port, so statistics can be attributed
  per host and telemetry traffic can be separated from data traffic.
- Connectivity: `pingall` check in README Stages 1-3 (expected 0% dropped).
- Meaningful SDN function: periodic `OFPFlowStatsRequest`, rate = byte delta / time.

### 2.4 Socket-SDN Integration (3)

- Application data and network data meet in one place: `_evaluate` combines the
  switch-measured rate (OpenFlow counters) with the host-reported CPU and rate (UDP
  telemetry) and raises alerts only on defined combinations (for example HOST_FLOOD =
  high uplink **and** high CPU; TELEMETRY_MISMATCH = switch and host disagree).
- Telemetry traffic is excluded from the load calculation (UDP dst port 9999) so
  monitoring does not distort what it measures.
- End to end: see the `abnormal` scenario.
- Gap: the integration is one-directional. Application events change the controller's
  **alerts**; they do not yet change any **flow rule** on the switch.

### 2.5 Complete Application Functionality (4)

- Four reporting hosts, collector, four alert rules (HOST_FLOOD,
  CPU_SATURATION, LINK_CONGESTION, TELEMETRY_STALE), debouncing
  (2 consecutive polls) and cooldown (10 s), alerts written to console and `alerts.jsonl`.
- Normal operation: the `normal` scenario raised **no alerts in 3 of 3 runs**.

### 2.6 Dynamic SDN Functionality (6)

Rubric lists mechanisms such as routing, filtering, prioritization, load balancing,
rerouting, monitoring, blocking, recovery.

- Implemented: **monitoring** with dynamic response in the form of alerts. The controller
  continuously polls the switch and re-evaluates its rules every 2 s, and the alert state
  changes with network and application conditions.
- Not implemented: automatic blocking, rate limiting, rerouting or prioritisation. The
  system detects and reports; it does not change forwarding in response.

### 2.7 Challenging Scenario / Robustness (4)

- **Congestion / flood** (`abnormal`): h3 sends 9 Mbps to h4 on top of normal traffic
  while reporting 92% CPU. HOST_FLOOD on h3 and LINK_CONGESTION on h4 were raised in
  **3 of 3 runs, mean 5.6 s after the attack began**.
- **Service failure** (`telemetry_kill`): h3's telemetry client is killed.
  TELEMETRY_STALE on h3 was raised in **3 of 3 runs, mean 7.2 s after the kill**.
- Response: the system alerts. It does not take corrective action (section 2.6).

### 2.8 Performance Evaluation & Comparison (5)

Method: `./run_experiments.sh` runs five labelled scenarios, three runs each, with a fresh
controller per run. `python3 evaluate.py` compares the alerts raised with the alerts
expected and writes `results/summary.md`.

Measured results (3 runs per scenario, one topology of 4 hosts and 1 switch):

| Scenario | Truth | Result |
|---|---|---|
| `normal` | nothing wrong | no alerts in 3/3 runs |
| `abnormal` | attack | expected alerts detected 6/6, mean 5.6 s |
| `benign_backup` | nothing wrong (legitimate 8 Mbps backup, CPU 85%) | false alarms in 3/3 runs: HOST_FLOOD, LINK_CONGESTION |
| `slow_attack` | attack at 2 Mbps, CPU 30% | missed in 3/3 runs (false negative) |
| `telemetry_kill` | fault | detected 3/3, mean 7.2 s |

CPU_SATURATION alerts on h3 were also logged in the `abnormal` (2 runs) and
`benign_backup` (3 runs) scenarios. They are an artifact of the staged CPU value, not a
detector fault (section 3, item 5).

Within the `abnormal` runs, LINK_CONGESTION fired 2.7 to 3.4 s after the attack began and
HOST_FLOOD 4.7 to 14.7 s after (`results/summary.md`).

Metrics: detection (true-positive) rate, time to detect, false alarms (false positives),
misses (false negatives). Multiple conditions: five scenarios covering normal, attack,
benign look-alike, stealthy attack and component failure. Threshold values and the
scenario that exercises each are in README "Evaluation". The runs used
`util_high = 60%` (see section 3, item 4).

- Gap: there is **no comparison against a baseline or static approach** yet.

### 2.9 Documentation (written part)

- `README.md`: purpose, architecture, setup from a clean Ubuntu VM, staged run
  instructions, expected output at each stage, alert rules and constants, evaluation
  method, glossary, troubleshooting, known limitations, team.
- Reproduction: topology (`topology.py`), source (`monitor_controller.py`,
  `telemetry_client.py`, `telemetry_server.py`), configuration (constants and threshold
  table in README), test cases (the five scenarios in `topology.py`, run by
  `run_experiments.sh` and scored by `evaluate.py`), instructions (README
  "Running the project").
- Concepts: `Network Monitoring and Alert System Concepts & Guide.docx`.

## 3. Known gaps and limitations

1. **No automatic mitigation** (sections 2.4, 2.6). Alerts only.
2. **False alarms are a measured limitation, not a bug in the test.** A legitimate backup
   (high traffic, busy CPU) is indistinguishable from a flood to the rules, and a
   low-rate attack stays under every threshold.
3. **The high-utilisation threshold was changed after the first runs.** With
   `util_high = 70%`, HOST_FLOOD was missed in the VM, because h3's measured uplink was
   only 61 to 76% for 9 Mbps offered. It was lowered to 60% and the scenarios were re-run.
   The threshold was therefore adjusted on the same scenarios used to report results;
   there is no separate held-out set.
4. **CPU is simulated.** Mininet hosts share the VM's CPUs, so each host's CPU value is
   set with `--cpu-override`. Traffic and byte counts are real. CPU-based outcomes test
   the rules, not real host load. Five of the 11 false alarms in the results are
   CPU_SATURATION warnings raised in the last seconds, after h3's traffic ended while its
   staged CPU value was still being reported; the other six are the `benign_backup`
   limitation in item 3.

## 4. How to reproduce

```bash
# Ubuntu VM with Mininet, Open vSwitch, iperf and the Ryu venv (see README Setup)
./run_experiments.sh        # 5 scenarios x 3 runs, about 20 minutes
python3 evaluate.py         # writes results/summary.md
```
