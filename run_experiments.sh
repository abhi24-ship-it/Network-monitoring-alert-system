#!/usr/bin/env bash
# Run labelled scenarios, each in a FRESH controller, and store one folder per run:
#   results/runs/<scenario>_<n>/{polls.csv, alerts.jsonl, truth.json}
#
#   ./run_experiments.sh                      # all scenarios, 5 repeats each
#   ./run_experiments.sh flood normal         # chosen scenarios
#   REPEATS=3 ./run_experiments.sh flood      # fewer repeats
#
# Needs: Ubuntu VM with Mininet/OVS/iperf (see README setup) and the Ryu venv.
# Run as your normal user; sudo is used only for Mininet. ~75 s per run.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
RYU="${RYU_MANAGER:-$HOME/ryu-venv/bin/ryu-manager}"
REPEATS="${REPEATS:-5}"
OUT="$ROOT/results/runs"

if [ ! -x "$RYU" ]; then
  echo "ryu-manager not found at $RYU (set RYU_MANAGER=/path/to/ryu-manager)"; exit 1
fi

if [ "$#" -gt 0 ]; then SCEN="$*"; else SCEN="$(python3 "$ROOT/scenarios.py" --list | awk '{print $1}')"; fi

for s in $SCEN; do
  for n in $(seq 1 "$REPEATS"); do
    run="$OUT/${s}_${n}"
    rm -rf "$run"; mkdir -p "$run"
    sudo mn -c >/dev/null 2>&1
    echo "=== $s run $n -> $run"
    # fresh controller per run; alerts.jsonl lands in the run folder (its cwd)
    ( cd "$run" && NETMON_POLL_LOG="$run/polls.csv" \
        "$RYU" "$ROOT/monitor_controller.py" > controller.log 2>&1 ) &
    RYU_PID=$!
    sleep 4
    ( cd "$ROOT" && sudo python3 scenarios.py --scenario "$s" --out "$run" ) \
        > "$run/mininet.log" 2>&1
    kill "$RYU_PID" 2>/dev/null; pkill -f ryu-manager 2>/dev/null
    sudo chown -R "$(id -un)":"$(id -gn)" "$run" 2>/dev/null
    sleep 2
  done
done
sudo mn -c >/dev/null 2>&1
echo "done. next: python3 analysis/evaluate.py"
