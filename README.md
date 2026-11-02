# Network Monitoring and Alert System

A small Software-Defined Networking (SDN) project that detects abnormal network behaviour by combining two kinds of evidence:

1. **Network-layer data**: how many bytes the virtual switch actually forwards for each host, read from OpenFlow flow counters.
2. **Application-layer data**: what each host *says* about itself (CPU usage and sending rate), sent as UDP telemetry messages.

A single signal can be misleading. A busy link could be a big file download. A high CPU could be a heavy calculation. When a host's link is nearly full **and** its own report says its CPU is maxed out, the combination points much more strongly to a runaway or compromised application. The controller raises an alert only when the two sources agree.

> **Status:** All stages (setup, Stage 1 to Stage 5, both scenarios) have been run end to end on an Ubuntu VM and produced the expected results. See [Verification log](#verification-log). If your run differs, check [Troubleshooting](#troubleshooting) first, then report the exact command and output.

---

## Important: where this runs

**Run this project on an Ubuntu Linux machine (a VM is fine).** Mininet and Ryu do not run on Windows or macOS. This repository is for reading, sharing and editing on any operating system. Every test run happens on Ubuntu.

---

## Table of contents

- [What the project does](#what-the-project-does)
- [How it works](#how-it-works)
- [Repository contents](#repository-contents)
- [Requirements](#requirements)
- [Setup (once, on Ubuntu)](#setup-once-on-ubuntu)
- [Running the project](#running-the-project)
- [Expected output](#expected-output)
- [Alert rules and thresholds](#alert-rules-and-thresholds)
- [Evaluation: thresholds and false positives / negatives](#evaluation-thresholds-and-false-positives--negatives)
- [Concepts glossary](#concepts-glossary)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Verification log](#verification-log)
- [Team](#team)

---

## What the project does

- Builds a virtual network in **Mininet**: four hosts (`h1`–`h4`) connected to one **Open vSwitch** switch (`s1`), with each link limited to 10 Mbps.
- Starts a **telemetry client** inside every host. Each client sends a small JSON message over **UDP** every 2 seconds containing its CPU usage, memory usage, and sending/receiving rates.
- Runs a **Ryu SDN controller** that:
  - listens for telemetry messages on UDP port 9999,
  - asks the switch for its flow counters every 2 seconds,
  - converts those counters into per-host rates and utilisation percentages,
  - compares them with the host's own telemetry,
  - and writes alerts to the console and to `alerts.jsonl`.
- Includes two scripted demonstrations:
  - **Normal traffic**: expected to produce no alerts.
  - **Abnormal traffic**: one host floods another, and the controller is expected to raise alerts.

---

## How it works

```
  h1, h2, h3, h4  ──(data traffic over UDP/iperf)──────►  h4 (receiver)
     │
     │  telemetry every 2 s (UDP port 9999, sent to 10.0.0.254)
     ▼
  ┌──────────────┐  OpenFlow 1.3   ┌────────────────────────────────────────────┐
  │ s1 (switch)  │◄───────────────►│ Ryu controller (monitor_controller.py)     │
  │ counts bytes │  flow stats     │  • polls flow counters every 2 s           │
  │ per flow     │  every 2 s      │  • receives telemetry on UDP :9999         │
  └──────────────┘                 │  • computes rates + utilisation            │
                                   │  • applies rules, debounces, raises alerts │
                                   └────────────────────────────────────────────┘
```

Step by step:

1. **Forwarding.** The switch forwards packets. For each host-to-host flow, it keeps a running byte counter. That counter only goes up.
2. **Polling.** Every 2 seconds the controller asks the switch for all flow counters.
3. **Rate calculation.** For each flow the controller computes `rate = (bytes now − bytes last time) × 8 ÷ seconds elapsed`, giving bits per second.
4. **Utilisation.** `utilisation = rate ÷ 10 Mbps`. For example, 2 Mbps on a 10 Mbps link is 20%.
5. **Telemetry.** Telemetry packets (UDP destination port 9999) are *excluded* from the load calculation, so monitoring does not count as traffic.
6. **Correlation.** For each host, the controller compares the switch-measured rate with the rate and CPU reported by the host's telemetry. It applies the rules in [Alert rules and thresholds](#alert-rules-and-thresholds).
7. **Debouncing.** A rule must fire on **2 consecutive checks** before an alert is sent. After an alert, the same alert for the same host is suppressed for **10 seconds**. This prevents one noisy measurement from causing a false alarm.

---

## Repository contents

| File | Run it? | Purpose |
|---|---|---|
| `topology.py` | **Yes**, with `sudo` | Builds the Mininet network, starts telemetry clients, and runs the five scenarios (`normal`, `abnormal`, `benign_backup`, `slow_attack`, `telemetry_kill`) or a `cli`. |
| `monitor_controller.py` | **Yes**, with `ryu-manager` | The Ryu SDN controller. Contains the switch logic, the telemetry collector, the flow-statistics poller, and the alert rules. |
| `telemetry_client.py` | No | Runs inside each Mininet host (started automatically by `topology.py`). Sends CPU/memory/rate telemetry over UDP. |
| `telemetry_server.py` | Only for testing | A standalone UDP collector used in Stage 2 to check the telemetry path before the controller is involved. Do not run it at the same time as the controller (both use UDP port 9999). |
| `run_experiments.sh` | **Yes** | Runs the 5 scenarios several times, one fresh controller per run, and saves `results/<scenario>_<n>/`. |
| `evaluate.py` | **Yes**, after the runs | Scores the saved alerts (TP / FP / FN / TN, time to detect) and writes `results/summary.md`. |
| `README.md` | No | This file. |
| `.gitignore` | No | Tells Git which generated files not to upload (logs, virtual environments, caches). |
| `Network Monitoring and Alert System Concepts & Guide.docx` | No | Project summary and explanation of the concepts involved. |

**Files created when you run the project** (not part of the repository):

| File | Created by | Contents |
|---|---|---|
| `alerts.jsonl` | Controller | One JSON line per alert: time, host, rule, severity, details. |
| `telemetry.jsonl` | `telemetry_server.py` | One JSON line per telemetry message received during the Stage 2 test. |
| `/tmp/h1_telemetry.log` … `/tmp/h4_telemetry.log` | Telemetry clients | Each client's own output, inside the Mininet hosts. Useful when debugging. |
| `/tmp/h4_iperf.log` | `topology.py` | Output of the iperf receiver on h4. |

---

## Requirements

- **Ubuntu 22.04 or 24.04** (Linux). A VM is fine.
- Internet access during setup (to download packages).
- Sudo rights on the VM.
- Python 3 (already installed on Ubuntu).
- About 2 GB of free disk space.

Software installed in the setup below:

| Software | Installed with | Used for |
|---|---|---|
| Mininet | apt | Creating the virtual hosts and switch |
| Open vSwitch | apt | The software switch (`s1`) |
| iperf | apt | Generating traffic between hosts |
| psutil | apt | Reading CPU and memory usage in the telemetry client |
| Ryu 4.34 | pip (inside a virtual environment) | The SDN controller |
| eventlet 0.30.2 | pip (inside the same virtual environment) | Required by Ryu; newer versions break it |
| uv | curl installer | Installs an older Python (3.9) for Ryu, and creates the virtual environment |

---

## Setup (once, on Ubuntu)

Follow these steps in order. Each command is explained so you know what it does.

### Step 1: Get the project onto the VM

Put the project in one folder on the VM. The examples below use `~/netmon`. Choose one option.

**Option A: clone from GitHub (recommended, gets the latest version).**
```bash
sudo apt install -y git
git clone https://github.com/<your-github-username>/network-monitoring-alert-system.git ~/netmon
cd ~/netmon
```
- `git clone` downloads the whole repository into `~/netmon`.
- The repository is private, so GitHub will ask you to log in. Use your username, and for the password use a GitHub **personal access token** (GitHub no longer accepts account passwords here). [Likely] You create one under Settings → Developer settings → Personal access tokens.

**Option B: copy the files manually.** Download the four `.py` files,`README.md` and `Network Monitoring and Alert System Concepts & Guide.docx` to Windows, then copy them to the VM (for example with a shared folder or by drag-and-drop in the VM window). Then run:
```bash
mkdir -p ~/netmon
cd ~/netmon
ls
```

`ls` should list at least: `monitor_controller.py`, `topology.py`, `telemetry_client.py`, `telemetry_server.py`.

Check that `topology.py` is the current version (it must have `h4` reporting telemetry):

```bash
grep -c "'h4': 15" ~/netmon/topology.py
```

Expected output: `2`. If you see `0`, you have an old copy of `topology.py`. Get the current one before continuing.

### Step 2: Install Mininet, Open vSwitch, iperf and psutil

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf python3-psutil python3-venv python3-pip
sudo systemctl enable --now openvswitch-switch
```

What each part does:
- `apt update` refreshes the list of available packages.
- `apt install ...` installs the tools.
- `systemctl enable --now openvswitch-switch` starts the Open vSwitch service now and at every boot.

Check the installs:

```bash
sudo ovs-vsctl show          # prints an "ovs_version" line
iperf -v                     # prints "iperf version 2.x"
python3 -c "import mininet, psutil; print('python modules ok')"
```

Expected: `ovs_version` appears, `iperf version 2.x` appears, and `python modules ok` is printed.

If the last command prints `ModuleNotFoundError`, see [Troubleshooting](#troubleshooting).

### Step 3: Install Ryu in a virtual environment

Ryu 4.34 is old and does not build on Python 3.12 or later, which Ubuntu 24.04 uses by default. So this project runs Ryu with Python 3.9. `uv` downloads that version for you.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
```

- The first line installs `uv`, a fast Python package manager.
- The second line makes the `uv` command available in this terminal.

Create the environment with Python 3.9:

```bash
uv venv --python 3.9 ~/ryu-venv
source ~/ryu-venv/bin/activate
uv pip install "setuptools==67.6.1" wheel pbr
uv pip install --no-build-isolation ryu "eventlet==0.30.2"
ryu-manager --version
```

What each part does:
- `uv venv --python 3.9 ~/ryu-venv` creates a separate, isolated Python environment in `~/ryu-venv`. Nothing it installs affects the rest of Ubuntu.
- `source ~/ryu-venv/bin/activate` switches your terminal into that environment. Your prompt will start with `(ryu-venv)`.
- `uv pip install ...` installs packages into that environment. Use `uv pip` here, not plain `pip`.
- `--no-build-isolation` lets Ryu's build use the pinned `setuptools` version. Without it, the build fails.
- `ryu-manager --version` should print a version string containing `4.34`.

**Venv rules:**
- The venv is needed **only for `ryu-manager`**.
- Mininet and the telemetry scripts run with the **normal** Python, so run them with the venv **off**.
- To turn the venv off: `deactivate`.
- A new terminal always starts with the venv off.

### Step 4: Clean up anything left from previous runs

```bash
sudo mn -c
```

This removes leftover Mininet network interfaces and processes. Run it before the first run and after any run that was interrupted.

---

## Running the project

Use **three terminal windows**. Open a new tab with **Ctrl+Shift+T** in most Ubuntu terminals.

**What the `mininet>` prompt is.** When `topology.py --scenario cli` runs, it builds the network and then leaves you in a Mininet command line. Commands you type there run *inside* the virtual network. For example, `h1 ping ...` runs ping on host h1, and `sh ...` runs a normal Linux command on the VM. Leave it with `exit`. Nothing is installed or opened for you; `mininet>` is simply where the program waits for input.

**How to tell which terminal you are in.** The prompt shows your folder and, when the venv is on, `(ryu-venv)` at the start. If a command fails with "not found" or "No module named", check that prompt first.

| Terminal | Purpose | Venv? |
|---|---|---|
| **T1: Controller** | Runs Ryu. Shows alerts. | **On** |
| **T2: Mininet** | Builds and runs the network. | **Off** |
| **T3: Spare** | Checks files and runs test listeners. | **Off** |

Rules:
- **Start the controller before the network.** Otherwise the switch has no controller and nothing is forwarded.
- **Ctrl+C stops a running program.**
- **Run every command from `~/netmon`** (except where stated), because the controller writes `alerts.jsonl` into the folder it was started from.

### Stage 1: Check Mininet works on its own

In **T2** (venv off):

```bash
cd ~/netmon
sudo mn --test pingall
```

Expected: `*** Results: 0% dropped (2/2 received)`.

If you see a message about a controller, install the test controller with `sudo apt install -y openvswitch-testcontroller` and run the command again. Then run `sudo mn -c`.

### Stage 2: Check the UDP telemetry path

This stage uses Ryu's built-in basic switch (`simple_switch_13`) instead of the project controller, because the switch forwards nothing without a controller.

**T1** (venv on):
```bash
source ~/ryu-venv/bin/activate
cd ~/netmon
ryu-manager ryu.app.simple_switch_13
```
Expected: a few `loading app ...` lines, then the program waits.

**T3** (venv off):
```bash
cd ~/netmon
python3 telemetry_server.py
```
Expected: `[collector] listening on UDP :9999`

**T2** (venv off):
```bash
cd ~/netmon
sudo python3 topology.py --scenario cli
```
Expected: Mininet's startup messages, then `*** Results: 0% dropped (12/12 received)`, then a `mininet>` prompt.

**In T3**, within a few seconds, telemetry lines appear for each host, roughly every 2 seconds:
```
[  10.0.0.1] seq=0    cpu=  3.1% tx=  0.00 Mbps lost_since_last=0
```
You should see all four hosts, `10.0.0.1` to `10.0.0.4`, with `seq` increasing and `lost_since_last=0`.

**At the `mininet>` prompt in T2:**
```
h1 ping -c 3 10.0.0.254
```
Expected: `0% packet loss`. This checks that hosts can reach the collector.

If this ping fails, run `sh ip addr | grep 10.0.0.254` at the `mininet>` prompt and keep the output for troubleshooting.

**Shut down:**
1. At `mininet>` in T2: type `exit`.
2. In T3: Ctrl+C.
3. In T1: Ctrl+C.
4. In T2: `sudo mn -c`.

### Stage 3: Run the real controller

**T1** (venv on):
```bash
cd ~/netmon
ryu-manager monitor_controller.py
```
Expected: lines including `instantiating app monitor_controller.py of NetworkMonitor` and `telemetry collector listening on UDP :9999`.

**T2** (venv off):
```bash
cd ~/netmon
sudo python3 topology.py --scenario cli
```

At `mininet>` in T2:
```
pingall
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

Expected:
- `pingall` shows `0% dropped`.
- `dump-flows` prints the switch's rules. Each line includes `n_packets=`, `n_bytes=`, and a match such as `nw_src=10.0.0.1,nw_dst=10.0.0.2`.
- Lines containing `tp_dst=9999` are telemetry flows. The controller deliberately ignores them when measuring load.

Shut down the same way as Stage 2.

### Stage 4: Measure real traffic

In T2 at the `mininet>` prompt (with T1 still running):

```
h4 iperf -s -u -p 5001 &
h1 iperf -c 10.0.0.4 -u -p 5001 -b 2M -t 20
sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

What each line does:
- `h4 iperf -s -u -p 5001 &` makes h4 listen for UDP traffic in the background.
- `h1 iperf -c 10.0.0.4 ... -b 2M -t 20` makes h1 send 2 Mbps to h4 for 20 seconds. This command waits until it finishes.
- `dump-flows` shows the counters. Run it right after the transfer, because idle flows are removed after about 10 seconds.

**Check the maths:** 2 Mbps for 20 seconds is 40 million bits, which is 5 million bytes. Find the line containing `nw_src=10.0.0.1,nw_dst=10.0.0.4` and read its `n_bytes`. Expect roughly **5.1–5.2 million**, because packet headers add a few percent.

Expected in T1: nothing. This is normal traffic and must not raise an alert.

Clean up: `exit` in T2, then `sudo mn -c`.

### Stage 5: Run the two scenarios

Before **each** scenario, reset everything:

1. **T1:** Ctrl+C, then restart the controller:
   ```bash
   cd ~/netmon
   ryu-manager monitor_controller.py
   ```
2. **T3:** remove any old alert log:
   ```bash
   cd ~/netmon
   rm -f alerts.jsonl
   ```
3. **T2:** clean up Mininet:
   ```bash
   sudo mn -c
   ```

#### Scenario A: Normal traffic (about 55 seconds)

In **T2** (venv off):
```bash
cd ~/netmon
sudo python3 topology.py --scenario normal
```

What happens: h1 and h2 each send 1 Mbps to h4. Every host reports low CPU.

Expected:
- T2 prints `*** NORMAL: h1,h2 -> h4 at 1 Mbps each for 40s`, then exits.
- **T1 prints no alerts.**
- In T3, `ls alerts.jsonl` reports "No such file or directory". That is the correct result.

#### Scenario B: Abnormal traffic (about 55 seconds)

Repeat the reset steps above, then in **T2** (venv off):
```bash
cd ~/netmon
sudo python3 topology.py --scenario abnormal
```

What happens:
1. First 15 seconds: the same calm traffic as Scenario A.
2. At 15 seconds: h3 starts sending 9 Mbps to h4 and reports 92% CPU.
3. h4's incoming link is then carrying about 11 Mbps (9 + 1 + 1) on a 10 Mbps link.

Expected: about 4–6 seconds after the attack starts, T1 prints alerts similar to:
```
[CRITICAL] HOST_FLOOD host=10.0.0.3: uplink 94% AND cpu 92% -> runaway/compromised application
[CRITICAL] LINK_CONGESTION host=10.0.0.4: downlink 109% -> possible flood victim
```
Percentages will vary slightly.

In T3, view the saved alerts:
```bash
cat alerts.jsonl
```
Each line is one alert in JSON format. An example line:
```json
{"ts": 1728000000.12, "host": "10.0.0.3", "rule": "HOST_FLOOD", "severity": "CRITICAL", "detail": "uplink 94% AND cpu 92% -> runaway/compromised application"}
```
Fields: `ts` is the Unix time of the alert, `host` is the host's **IP address** (`10.0.0.3` means h3), `rule` is the rule name from the table above, `severity` is CRITICAL or WARNING, and `detail` explains the numbers.

**Finish:** Ctrl+C in T1, `sudo mn -c` in T2, and `deactivate` in T1.

---

## Expected output

| Scenario | Telemetry (T3) | Alerts (T1) | `alerts.jsonl` |
|---|---|---|---|
| Stage 2 (telemetry test) | One line per host every 2 s | None (basic switch, no controller logic) | Not created |
| Stage 3–4 (manual traffic) | Not used | None | Not created |
| Normal scenario | Not used | **None** | Not created |
| Abnormal scenario | Not used | **HOST_FLOOD for 10.0.0.3, LINK_CONGESTION for 10.0.0.4** | Contains those alerts |

---

## Alert rules and thresholds

### Rules

| Rule | Severity | Fires when | Meaning |
|---|---|---|---|
| `HOST_FLOOD` | CRITICAL | Uplink utilisation ≥ 60% **and** host CPU ≥ 80% | A host is sending a lot while its CPU is maxed out. Likely a runaway or compromised application. |
| `HIGH_VOLUME_LOW_CPU` | WARNING | Uplink utilisation ≥ 60%, CPU < 80% | Heavy sending without high CPU. Probably a bulk transfer. |
| `CPU_SATURATION` | WARNING | CPU ≥ 80%, uplink < 20% | The host is busy but not using the network. The problem is local computation. |
| `TELEMETRY_MISMATCH` | WARNING | Switch and host disagree on sending rate by more than 50% (and the larger value is above 0.5 Mbps) | Possible spoofed telemetry, packet loss, or traffic the host is not reporting. |
| `LINK_CONGESTION` | CRITICAL | Downlink utilisation ≥ 60% | A host's incoming link is nearly full. That host may be the victim of a flood. |
| `TELEMETRY_STALE` | WARNING | No telemetry for 6 seconds, but the host is known or active | The host is down, its telemetry is blocked, or it does not send telemetry. |

### Constants

| Constant | Value | Where | Meaning |
|---|---|---|---|
| `LINK_CAPACITY_BPS` | 10,000,000 (10 Mbps) | `monitor_controller.py` | Must match `bw=10` in `topology.py`. |
| `POLL_INTERVAL` | 2.0 s | controller | How often flow counters are read. |
| `STALE_AFTER` | 6.0 s | controller | Telemetry older than this is treated as missing. |
| `MIN_BPS` | 50,000 (50 kbps) | controller | Traffic below this is ignored for stale-host detection. |
| `CONFIRM_CYCLES` | 2 | controller | Consecutive hits needed before an alert fires. |
| `COOLDOWN` | 10.0 s | controller | Minimum gap between repeats of the same alert. |
| `util_high` | 0.60 | `T` dict | 60% utilisation threshold. |
| `util_low` | 0.20 | `T` dict | 20% utilisation threshold. |
| `cpu_high` | 80.0 | `T` dict | 80% CPU threshold. |
| `mismatch_ratio` | 0.50 | `T` dict | Allowed relative difference between switch and host measurements. |
| `mismatch_min_bps` | 500,000 | `T` dict | Ignore mismatches below 0.5 Mbps. |

To change a threshold, edit the `T` dictionary (or the constant) in `monitor_controller.py`, then restart the controller.

---

## Evaluation: thresholds and false positives / negatives

Five scenarios, each with a known right answer, show how often the detector is right
or wrong. Run them from the repository folder on the Ubuntu VM:

```bash
./run_experiments.sh        # 5 scenarios x 3 runs, about 20 minutes (./run_experiments.sh 1 for a quick pass)
python3 evaluate.py         # prints the tables below and writes results/summary.md
```

Single scenario by hand: restart the controller, then `sudo python3 topology.py --scenario <name>`.

### The scenarios

| Scenario | What happens | Truth | Expected result |
|---|---|---|---|
| `normal` | h1, h2 send 1 Mbps to h4 | Nothing wrong | No alerts (true negative) |
| `abnormal` | h3 floods h4 at 9 Mbps, reports CPU 92% | Attack | HOST_FLOOD on h3, LINK_CONGESTION on h4 (true positives) |
| `benign_backup` | h3 runs a legitimate 8 Mbps backup at CPU 85% | Nothing wrong | **False alarms expected**: HOST_FLOOD and LINK_CONGESTION |
| `slow_attack` | h3 sends 2 Mbps at CPU 30% | Attack, but quiet | **Miss expected** (false negative): under every threshold |
| `telemetry_kill` | h3's telemetry client is killed | Fault | TELEMETRY_STALE on h3 after about 8 to 10 s |

"Expected result" is a prediction from reading the rules. Only `results/summary.md`
shows what actually happened.

### Terms

| | Something wrong | Nothing wrong |
|---|---|---|
| **Alert raised** | True positive (TP) | False positive (FP): a false alarm |
| **No alert** | False negative (FN): a miss | True negative (TN) |

### Why the thresholds have these values

They are demonstration values, not tuned on data: normal readings in the design are
about 10% link load and 20 to 25% CPU, and the flood is about 93% load and 92% CPU,70% missed, 60% was chosen after the runs, and there is no held-out set.

| Threshold | Value | Reasoning | Which scenario exercises it |
|---|---|---|---|
| `util_high` | 60% | Far above normal load, below a full link | `abnormal` (crossed), `normal` (not crossed), `benign_backup` (crossed by harmless traffic) |
| `cpu_high` | 80% | Far above normal CPU | `abnormal`, `benign_backup` |
| `util_low` | 20% | Separates a quiet link from a busy one | `normal` |
| `mismatch_ratio` | 50% | Host and switch rates may differ a little (headers, timing) | all scenarios (must stay silent) |
| `CONFIRM_CYCLES` | 2 | One poll can be a blip; two are not | `abnormal` (adds about 2 s of delay) |
| `STALE_AFTER` | 6 s | Three missed 2 s reports | `telemetry_kill` |

Raising a threshold removes false alarms like `benign_backup` but adds misses like
`slow_attack`; lowering it does the opposite. No setting removes both. These values
were not swept, so the table shows where each is exercised, not that it is optimal.

### Results
| scenario | runs | expected alerts detected | mean time to detect | false alarms |
|---|---|---|---|---|
| normal | 3 | - | - | - |
| abnormal | 3 | 6/6 | 5.6 s | CPU_SATURATION on 10.0.0.3 (x2) |
| benign_backup | 3 | - | - | CPU_SATURATION on 10.0.0.3 (x3); HOST_FLOOD on 10.0.0.3 (x3); LINK_CONGESTION on 10.0.0.4 (x3) |
| slow_attack | 3 | 0/3 | - | - |
| telemetry_kill | 3 | 3/3 | 7.2 s | - |

| | something wrong | nothing wrong |
|---|---|---|
| alert raised | TP = 9 | FP = 11 |
| no alert | FN = 3 | TN = 3 |

### Limits (state these in the write-up)

- CPU is staged with `--cpu-override`, so CPU-based outcomes test the rules, not real host load.
- Which alerts "should" fire is our own labelling of each scenario (`EXPECTED` in `evaluate.py`).
- One topology (4 hosts, 1 switch, 10 Mbps) and a few runs per scenario: indicative, not statistical.
- If a killed telemetry client also floods, HOST_FLOOD cannot fire (it needs fresh telemetry); only LINK_CONGESTION and TELEMETRY_STALE can. This case is not one of the five scenarios.

---

## Concepts glossary

A short reference. The project summary document (`Network Monitoring and Alert System Concepts & Guide.docx`) explains these in more depth.

| Term | Plain meaning |
|---|---|
| **SDN (Software-Defined Networking)** | Separating the network's *decisions* (the controller) from its *forwarding* (the switch). |
| **Controller** | The program that makes decisions for the switch. Here, Ryu, running `monitor_controller.py`. |
| **Switch** | The device that forwards packets. Here, Open vSwitch (`s1`), simulated by Mininet. |
| **OpenFlow** | The protocol the controller uses to talk to the switch. Version 1.3 is used here. |
| **Flow** | A set of packets that match the same pattern, such as "from 10.0.0.1 to 10.0.0.4". |
| **Flow statistics / counters** | The switch's running totals of packets and bytes per flow. |
| **Mininet** | A tool that creates a virtual network (hosts, switches, links) on one Linux machine. |
| **Host** | A virtual computer in Mininet (`h1`–`h4`). |
| **UDP** | A fast, connectionless way to send messages. There is no acknowledgement, so messages can be lost. Telemetry uses UDP. |
| **Telemetry** | Regular status reports sent from a system to a monitor. Here, JSON messages with CPU and rate data. |
| **Sequence number (`seq`)** | A counter in each telemetry message. A gap in the sequence means messages were lost. |
| **Bit / byte** | 1 byte = 8 bits. Network speeds are quoted in bits per second (bps). |
| **Mbps** | Megabits per second. 1 Mbps = 1,000,000 bits per second. |
| **Utilisation** | How full a link is, as a percentage of its capacity. |
| **Debouncing** | Requiring a condition to be true several times in a row before acting, to avoid false alarms. |
| **Cooldown** | A pause after an alert before the same alert can fire again. |
| **Downlink / uplink** | Here, "uplink" means traffic a host sends (`tx`), and "downlink" means traffic it receives (`rx`). |
| **iperf** | A tool that generates test traffic and reports its speed. |
| **Ryu** | A Python SDN controller framework. |
| **Virtual environment (venv)** | An isolated Python installation for one project, so its packages do not conflict with the system. |

---

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `ModuleNotFoundError: mininet` | The venv is active when running `topology.py` | Run `deactivate`, then run the command again. |
| `ModuleNotFoundError: ryu` or `ryu-manager: command not found` | The venv is not active in T1 | Run `source ~/ryu-venv/bin/activate`. |
| `externally-managed-environment` when running `pip install` | Ubuntu blocks system-wide pip installs | Do not use `--break-system-packages`. Use the venv steps in Step 3. |
| `Address already in use` | An old controller or collector is still running | Press Ctrl+C in that terminal, or run `pkill -f ryu-manager` and `pkill -f telemetry_server`. |
| `pingall` shows 100% dropped | The controller is not running | Start `ryu-manager` in T1 first, then restart the network. |
| Stage 2 shows no telemetry lines | Hosts cannot reach `10.0.0.254` | Run `h1 ping -c 3 10.0.0.254` at `mininet>`. Check `ip addr` in the root namespace. |
| Alerts appear during the normal scenario | Leftover state from a previous run | Restart the controller in T1, delete `alerts.jsonl`, and run `sudo mn -c`. |
| No alerts during the abnormal scenario | Telemetry is not arriving, or the controller was not restarted | Repeat Stage 2 to confirm telemetry, then run the reset steps. |
| Strange interface or "file exists" errors | Leftover Mininet state | Run `sudo mn -c`. |
| `ovs-ofctl` command not found | Open vSwitch is not installed or not running | Run `sudo systemctl status openvswitch-switch`, and reinstall if needed. |

When reporting a problem, include the exact command you ran, the terminal the output came from, and the full error text.

---

## Known limitations

- **CPU values are simulated.** All Mininet hosts share the VM's CPUs, so the CPU value each host reports is the whole VM's CPU, not its own. The demonstration uses `--cpu-override` to set a fixed CPU value. The traffic and byte counts are real. Document this in any report.
- **Single switch.** The controller assumes one switch. With more switches, traffic could be counted more than once.
- **UDP is unreliable.** Telemetry can be lost or forged. Sequence numbers detect loss but cannot prevent forgery.
- **Test traffic is simple.** The demonstration uses constant-rate iperf flows, not real application traffic.
- **Fixed thresholds.** Thresholds are set by hand. 
- **Single-run evidence.** The results in the verification log come from one set of runs. Timings (such as "4–6 seconds") and alert percentages will vary between machines.

---

## Verification log

Add one entry per person who runs the project, so results can be traced.

### Entry: 2026-10-09, author

- **Ubuntu version:** Ubuntu 26.04.1 LTS
- **Ryu Python version:** Python 3.14.4
- **Setup:** Pass
- **Stage 1 (Mininet alone):** Pass
- **Stage 2 (telemetry path):** Pass
- **Stage 3 (real controller):** Pass
- **Stage 4 (measured traffic):** Pass
- **Stage 5, normal scenario:** no alerts (Pass)
- **Stage 5, abnormal scenario:** HOST_FLOOD for 10.0.0.3 and LINK_CONGESTION for 10.0.0.4 (Pass)
- **Notes:** Run end to end;

---

## Team

| Name | GitHub username |Role|
|---|---|---|
| Abhishek Bhat | abhi24-ship-it |Network and SDN base|
| Abhimanyu Singh | abhimanyu-singh-gg |Telemetry|
| Abhimanyu Singh | cocabhishek197-tech |Flow Statistics and Detection|

Project summary and concepts: `Network Monitoring and Alert System Concepts & Guide.docx`
