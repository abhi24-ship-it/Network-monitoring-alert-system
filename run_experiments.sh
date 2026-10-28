#!/usr/bin/env bash
# Run the 5 scenarios N times each (default 3), one fresh controller per run.
# Saves results/<scenario>_<n>/alerts.jsonl and events.jsonl. About 75 s per run.
#   ./run_experiments.sh        # 3 repeats
#   ./run_experiments.sh 1      # quick pass
# Run as your normal user (sudo is used only for Mininet). Then: python3 evaluate.py
ROOT="$(cd "$(dirname "$0")" && pwd)"
RYU="${RYU_MANAGER:-$HOME/ryu-venv/bin/ryu-manager}"
N="${1:-3}"

for s in normal abnormal benign_backup slow_attack telemetry_kill; do
  for i in $(seq "$N"); do
    d="$ROOT/results/${s}_$i"
    sudo rm -rf "$d"; mkdir -p "$d"
    sudo mn -c >/dev/null 2>&1
    echo "=== $s run $i"
    (cd "$d" && exec "$RYU" "$ROOT/monitor_controller.py" > controller.log 2>&1) &
    pid=$!
    sleep 4
    (cd "$d" && sudo python3 "$ROOT/topology.py" --scenario "$s" > mininet.log 2>&1)
    kill "$pid" 2>/dev/null
    sudo chown -R "$(id -un)" "$d"
    sleep 2
  done
done
sudo mn -c >/dev/null 2>&1
