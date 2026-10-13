# Understand the project by following the data

## Explain it in about 30 seconds

> We built a virtual network with four computers using Mininet. Each computer sends regular status reports over UDP, while a controller independently reads traffic measurements from the switch. It combines both to detect unusual conditions, such as heavy traffic with high reported CPU usage, and logs alerts. Our demo uses real network traffic and preset CPU values.

The central idea is **what a host reports + what the switch measures**. The controller matches these by IP address and applies fixed rules. This is rule-based detection, not machine learning or a statistical correlation calculation.

## Read the files in this order

| File | Main job | Input → output |
|---|---|---|
| `topology.py` | Organise the experiment | Scenario choice → virtual hosts, switch, links, and traffic |
| `telemetry_client.py` | Report a host's status | Interface counters and CPU setting → UDP JSON reports |
| `monitor_controller.py` | Forward, measure, and decide | Packets, telemetry, switch counters → forwarding rules and alerts |
| `telemetry_server.py` | Test UDP communication alone | Reports → printed readings and `telemetry.jsonl` |
| `monitor_utils.py` | Validate reports and calculate rates | Raw messages/counters → safe reports/rates |
| `process_manager.py` | Track the experiment's processes | Launch requests → tracked processes, errors with log paths, cleanup |

## 1. Build the experiment: topology.py

`build()` creates h1–h4 and an Open vSwitch named s1. `TCLink` allows each host link to be configured for 10 Mbps. `RemoteController` tells s1 to contact Ryu at `127.0.0.1:6653`; it does not start Ryu for you.

The extra `root` node uses the main machine's network space (`inNamespace=False`). Its address, `10.0.0.254`, is how the virtual hosts reach the collector inside Ryu.

`start_host_telemetry()` assembles an argument list and starts a client inside one host. `start_telemetry()` does this for all four hosts. A CPU override sets the number the client reports; it does not make the host consume that amount of CPU.

`start_traffic()` starts h4's UDP iperf server and the h1/h2 senders. `run_normal()` uses 1 Mbps per sender. `run_abnormal()` adds h3 at 9 Mbps after roughly 15 seconds of baseline traffic and restarts only h3's tracked client with a 92% CPU report.

`main()` chooses the scenario, requires the initial connectivity test to pass, and uses `finally` blocks to stop managed processes, the network, and the root node.

**Why process tracking matters:** `ProcessManager` keeps each process handle returned by `host.popen()`. It can stop exactly that process, detect an unexpected exit, and tell you where its log is. It does not use broad `pkill` patterns. Finite traffic clients may exit successfully; reporting clients and the receiver are expected to stay running. A process being alive does not prove its packets were delivered.

## 2. Send the report: telemetry_client.py

`read_iface_bytes()` reads `/proc/net/dev` in the host's network namespace. It skips `lo`, the interface for talking to itself, then returns the first other interface's received and transmitted byte counters.

Each loop waits two seconds, reads the new counters, and computes:

```text
sending speed = (new transmitted bytes - previous transmitted bytes) × 8 / seconds
```

The multiplication by eight converts bytes to bits. `time.monotonic()` measures elapsed time without being affected by adjustments to the wall clock.

The report includes `host`, `seq`, `ts`, `iface`, `cpu_pct`, `mem_pct`, `tx_bps`, and `rx_bps`. `seq` numbers the messages. CPU and memory obtained through psutil are shared machine measurements, not isolated host measurements; the scenarios explicitly override CPU. Memory is reported but unused by the detector.

```python
socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
```

means “create an IPv4 UDP socket.” `json.dumps(msg).encode()` changes the dictionary into bytes, and `sendto()` sends those bytes to `10.0.0.254:9999`.

UDP has no built-in delivery guarantee. A later reading can replace a lost report, but that later reading can also be lost.

## 3. Control and monitor: monitor_controller.py

This is event-driven code: Ryu calls handlers when something happens. It is not one straight sequence that runs top to bottom.

| Function | Plain meaning |
|---|---|
| `__init__()` | Prepare the controller's memory and start the receiving/polling loops |
| `_features()` | Install a fallback rule: ask the controller about unmatched packets |
| `_state_change()` | Track connected switches and discard switch state on disconnect |
| `_add_flow()` | Send a forwarding rule to the switch |
| `_packet_in()` | Learn sender locations, choose an output port, install IPv4 rules when the destination is known, and forward the current packet |
| `_telemetry_loop()` | Receive, validate, timestamp, and retain the latest report per source IP |
| `_poll_loop()` | Ask for flow counters every two seconds |
| `_flow_stats_reply()` | Assemble a complete reply, calculate rates, and total them per host |
| `_evaluate()` | Apply the six conditions |
| `_debounce()` | Require two consecutive findings and enforce a ten-second repeat cooldown |
| `_alert()` | Print an alert and append it to `alerts.jsonl` |

### The controller's memory

- `mac_to_port`: which switch port leads to a learned MAC address.
- `datapaths`: the currently connected switches.
- `telemetry`: latest validated report for each sender IP.
- `last_counter`: the previous byte count, flow duration, and observation time.
- `pending_stats`: pieces of an unfinished OpenFlow statistics reply.
- `streak`: consecutive findings for each host/rule.
- `last_alert`: when that host/rule last alerted.

### Example: the first packet from h3 to h4

If no forwarding rule matches, s1 asks the controller through PacketIn. The controller learns the sender's MAC location. If h4's location is known, it installs a rule matching source IP, destination IP, IP protocol, and UDP destination port. Otherwise, it floods the packet while learning continues. PacketOut forwards the current packet. Later packets matching the installed rule can be handled by the switch itself.

Priority 10 forwarding rules beat the priority 0 fallback. An installed forwarding rule expires after ten idle seconds. These matches group traffic; they are not a complete five-tuple identifying every possible application connection.

### Example: calculate speed

If an entry grows by 500,000 bytes over two seconds, its speed is `500000 × 8 / 2 = 2 Mbps`. On a configured 10 Mbps link, that is 20% load.

The switch supplies each entry's duration. The rate helper detects common reset/recreation cases and uses the new lifetime as its baseline. The controller drops remembered entries missing from a complete reply. Telemetry flows with UDP destination port 9999 are excluded from the load totals.

### Example: trigger an alert

Suppose h3's switch-measured sending load is 90% and its latest reported CPU is 92%. Both exceed the 70% and 80% thresholds, so `HOST_FLOOD` becomes a finding. If it appears again in the next evaluation, the controller alerts. If the condition disappears, its streak resets. The same alert cannot repeat more often than every ten seconds.

| Rule | Condition |
|---|---|
| `HOST_FLOOD` | Outgoing load ≥70% and reported CPU ≥80% |
| `HIGH_VOLUME_LOW_CPU` | Outgoing load ≥70% and reported CPU <80% |
| `CPU_SATURATION` | Reported CPU ≥80% and outgoing load <20% |
| `TELEMETRY_MISMATCH` | Rate difference exceeds 50% of the larger value, and that value exceeds 0.5 Mbps |
| `LINK_CONGESTION` | Offered incoming load ≥70%; the name does not mean congestion is proven |
| `TELEMETRY_STALE` | Latest report is over six seconds old, or an uninstrumented host has measured traffic above 50 kbps |

Freshness, rate observation timing, and cooldown use monotonic time. Saved alert timestamps use Unix wall-clock time so people can interpret the logs.

## 4. Validate before trusting: monitor_utils.py

`decode_telemetry()` rejects non-object JSON, missing consumed fields, negative rates, invalid sequence numbers, nonnumeric values, NaN/infinity, and CPU outside 0–100. Both collectors use it. Extra report fields remain allowed. Validation does not authenticate the sender.

`flow_rate()` uses the current byte counter and flow duration together with the previous sample. A counter decrease, duration decrease, or lifetime younger than the observation gap starts a fresh baseline. This prevents the previous flow's idle gap from diluting the new flow's initial speed.

## 5. Test the path alone: telemetry_server.py

This standalone collector receives validated reports, prints their CPU/rate values, estimates sequence gaps, and writes `telemetry.jsonl`. It does not control the switch or run the anomaly detector. Run it with the basic Ryu switch for Stage 2, not alongside the full monitoring controller: both need UDP port 9999.

## Viva answers and corrections to the original DOCX guide

- **Why two sources?** The switch measures traffic independently; host reports add context. Neither proves malicious intent.
- **Why UDP?** Reports are small and periodic. UDP avoids connection setup and built-in retransmission, accepting possible loss.
- **Which path carries what?** Host reports are ordinary UDP data-plane traffic. OpenFlow carries controller/switch instructions and statistics. An unmatched host packet may also be carried inside PacketIn/PacketOut during forwarding setup.
- **What is distributed?** Data collection happens at multiple hosts and the switch; analysis is centralized in one controller.
- **Do all alerts combine both sources?** No. High incoming load can be flagged without fresh telemetry.
- **Is congestion confirmed?** No. Flow counters estimate offered load; receiver throughput, queue measurements, or loss evidence are needed to establish the actual bottleneck behavior.
- **Does the controller count lost telemetry messages?** No. It checks freshness; sequence-gap estimates belong to the standalone collector and can be confused by reordering or restarts.
- **Are APIs used?** There are no external web/REST API calls in this implementation. Sockets, Ryu, and Mininet still expose programming APIs.
- **What does it prevent?** Nothing automatically; it detects and reports only.
- **What is verified?** Local regression tests cover logic. Follow TESTING.md to verify the current complete network on Ubuntu.

The original DOCX describes the earlier four-file layout and predates validation, process tracking, and the rate fixes. Use this guide and the source code for current implementation details.
