# Verification and demonstration evidence

## Current evidence

The original README records author-reported successful Ubuntu runs dated 2026-10-09, but contains neither captured output nor the environment versions. Preserve that as a historical report, not proof of the current code.

The reliability changes were checked locally with Python 3.12 using standard-library unittest: **34 tests passed**. Python compilation and `git diff --check` also passed, and the corrected ignore rules matched all five sampled generated-file paths. The tests exercise real controller methods with Ryu transport/event dependencies stubbed, plus shared validation, rate calculations, and process lifecycle behavior. They do **not** run a switch, Mininet namespaces, Ryu's event loop, iperf, or the installation procedure.

The complete updated Ubuntu demonstration remains **not independently verified**. No generated examples are presented as actual demo evidence.

## Run the logic tests

From the repository root, using ordinary Python (no Ryu, psutil, Mininet, or root required):

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q monitor_controller.py monitor_utils.py process_manager.py topology.py telemetry_client.py telemetry_server.py tests
```

Coverage includes malformed telemetry, valid reports after malformed ones, threshold boundaries, all six rules, stale telemetry, consecutive-hit reset, cooldown, flow recreation, multipart statistics, telemetry exclusion, disconnect cleanup, alert-log errors, and managed process failures/cleanup.

## Capture a real Ubuntu run

Use the README setup and three-terminal instructions. Run each scenario with a fresh controller and fresh Mininet state. Preserve old logs before clearing them. Start in the repository root.

1. Record the commit with `git rev-parse HEAD`.
2. Create a new evidence directory, for example `mkdir -p evidence/run-YYYYMMDD-HHMM`, substituting the actual run date/time.
3. Record `lsb_release -a`, `uname -r`, `mn --version`, `ovs-vsctl --version`, and `iperf -v`. Record system Python and Ryu-venv Python separately. In the Ryu venv, record `ryu-manager --version` and the installed package versions (for the documented uv environment, `uv pip freeze`).
4. Capture the controller's complete terminal output for each scenario. For example, in Terminal 1 with the Ryu venv active:

   ```bash
   PYTHONPATH=. ryu-manager monitor_controller.py 2>&1 | tee evidence/run-YYYYMMDD-HHMM/normal-controller.txt
   ```

5. In Terminal 2, run the normal scenario and capture its output:

   ```bash
   sudo python3 topology.py --scenario normal 2>&1 | tee evidence/run-YYYYMMDD-HHMM/normal-topology.txt
   ```

6. Save any `alerts.jsonl` produced, the `/tmp/hN_telemetry.log` files, and the `/tmp/hN_iperf.log` files before the next run overwrites them. If no alert file was created, record that observed fact explicitly. Empty client logs alone do not prove telemetry delivery.
7. Stop the controller, clean Mininet as directed in the README, preserve/remove the previous working alert file, restart Ryu, then repeat using `abnormal` and new output filenames. Never run both UDP collectors together.
8. Record the onset of the h3 traffic burst and timestamp of the first relevant alert to measure detection delay. A screen recording or timestamped terminal capture can show the transition. Report the observed delay rather than assuming 4–6 seconds.
9. Inspect h4's iperf receiver report for actual throughput/loss; a flow-derived load above 100% is not itself a receiver measurement.

Do not commit unreviewed machine details or credentials. The root `alerts.jsonl` and `telemetry.jsonl` are ignored intentionally; reviewed evidence can use names such as `abnormal-alerts.txt` in the evidence directory.

## Acceptance checklist

| Check | Expected observation | Current independent status |
|---|---|---|
| Logic tests | All tests pass | Run locally; see command output |
| Initial connectivity | `pingall`: 0% dropped | Requires Ubuntu run |
| UDP path | Valid reports from all four hosts in Stage 2 | Requires Ubuntu run |
| Normal scenario | No persistent abnormal findings under the configured low CPU/load | Requires Ubuntu run |
| Abnormal scenario | `HOST_FLOOD` for h3 and high incoming-load `LINK_CONGESTION` for h4 | Requires Ubuntu run |
| Persistence | Actual console alerts also appear in the alert file | Requires Ubuntu run |
| Cleanup | Demo processes stopped; next clean scenario starts successfully | Requires Ubuntu run |

Other warnings can appear during transitions. h3's CPU override remains 92% after its 30-second burst, so CPU_SATURATION can appear before the abnormal scenario finishes. Investigate unexpected output rather than deleting it from evidence.

## Add a verification entry after running

Include the commit, tester, date, OS/kernel, both Python versions, Mininet/OVS/iperf/Ryu versions, exact commands, observed normal/abnormal outcomes, measured delay, and links to captured files. Record failures and deviations too. Do not fill missing measurements with expected values.
