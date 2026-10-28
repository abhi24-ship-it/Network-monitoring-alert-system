#!/usr/bin/env python3
"""Labelled experiment scenarios for the threshold / false-positive analysis.

Unlike topology.py's demo scenarios, every scenario here records GROUND TRUTH:
which alerts SHOULD fire for which host during which time window. analysis/
evaluate.py compares that truth with what the controller actually did.

  sudo python3 scenarios.py --list
  sudo python3 scenarios.py --scenario flood --out results/runs/flood_1

The controller must already be running (and, to get the per-poll log, started
with NETMON_POLL_LOG=<out>/polls.csv). run_experiments.sh does all of this and
restarts the controller for every run, so state never leaks between runs.
"""
import argparse
import json
import os
import time

from mininet.log import setLogLevel, info
from topology import build, start_telemetry, BASE, COLLECTOR_IP, PY

HOSTS = {f'h{i}': f'10.0.0.{i}' for i in range(1, 5)}
CPU_OK = {'h1': 20, 'h2': 25, 'h3': 10, 'h4': 15}     # staged CPU, as in topology.py


class Recorder:
    """Collects phases (time windows) with the alerts expected in each."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.phases = []

    def phase(self, name, secs, expect=None, allow=None, scored=True, action=None):
        """expect/allow: {host_name: [rule, ...]}. Unlisted hosts expect nothing."""
        expect, allow = expect or {}, allow or {}
        t0 = time.time()
        info(f'*** phase {name} ({secs}s, scored={scored})\n')
        if action:
            action()
        time.sleep(secs)
        self.phases.append({
            'name': name, 't0': t0, 't1': time.time(), 'scored': scored,
            'hosts': {ip: {'expected': expect.get(h, []), 'allowed': allow.get(h, [])}
                      for h, ip in HOSTS.items()}})

    def write(self, path):
        with open(path, 'w') as f:
            json.dump({'scenario': self.scenario, 'phases': self.phases}, f, indent=1)


# ------------------------------------------------------------- helpers ------
def setup(net, cpu=None):
    start_telemetry(net, cpu or CPU_OK)
    net.get('h4').cmd('iperf -s -u -p 5001 > /tmp/h4_iperf.log 2>&1 &')
    time.sleep(1)


def flow(net, src, rate, secs, dst='h4'):
    net.get(src).cmd(f'iperf -c {HOSTS[dst]} -u -p 5001 -b {rate} -t {secs} '
                     '> /dev/null 2>&1 &')


def kill_telemetry(net, name):
    net.get(name).cmd(f'pkill -f "telemetry_client.py.*--host-id {name}"')


def restart_telemetry(net, name, cpu):
    kill_telemetry(net, name)
    net.get(name).cmd(f'{PY} {BASE}/telemetry_client.py --server {COLLECTOR_IP} '
                      f'--interval 2 --host-id {name} --cpu-override {cpu} '
                      f'> /tmp/{name}_telemetry.log 2>&1 &')


def light_background(net, secs):
    flow(net, 'h1', '1M', secs)
    flow(net, 'h2', '1M', secs)


WARM = 15      # unscored settling time at the start of every scenario
TAIL = 8       # unscored time after the event so late alerts do not leak into scoring


# ----------------------------------------------------------- scenarios ------
def normal(net, rec):
    """h1,h2 -> h4 at 1 Mbps. Nothing is wrong: any alert is a false positive."""
    setup(net)
    light_background(net, 60)
    rec.phase('warmup', WARM, scored=False)
    rec.phase('steady', 30)


def flood(net, rec):
    """The project's headline attack: h3 floods h4 at 9 Mbps and reports 92% CPU."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)

    def attack():
        restart_telemetry(net, 'h3', 92)
        flow(net, 'h3', '9M', 30)
    rec.phase('attack', 30, expect={'h3': ['HOST_FLOOD'], 'h4': ['LINK_CONGESTION']},
              action=attack)
    rec.phase('tail', TAIL, scored=False)


def benign_bulk(net, rec):
    """Legitimate large transfer h3 -> h4 at 8 Mbps, host not stressed (CPU 10%).
    Nothing is wrong, but a volume-based rule cannot know that."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)
    rec.phase('bulk', 30, action=lambda: flow(net, 'h3', '8M', 30))
    rec.phase('tail', TAIL, scored=False)


def backup_busy_cpu(net, rec):
    """Legitimate 8 Mbps backup while the host compresses (CPU 85%). Nothing is
    wrong, but both HOST_FLOOD conditions are met."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)

    def backup():
        restart_telemetry(net, 'h3', 85)
        flow(net, 'h3', '8M', 30)
    rec.phase('backup', 30, action=backup)
    rec.phase('tail', TAIL, scored=False)


def many_to_one(net, rec):
    """h1,h2,h3 each send 3 Mbps to the server h4 (9 Mbps in). A busy server is
    not an attacked server, but LINK_CONGESTION only sees the load."""
    setup(net)
    rec.phase('warmup', WARM, scored=False,
              action=lambda: [flow(net, h, '3M', 80) for h in ('h1', 'h2', 'h3')])
    rec.phase('busy_server', 30)


def cpu_busy(net, rec):
    """h2 is compute-bound (CPU 90%) with only its 1 Mbps background traffic.
    A host compute problem is what CPU_SATURATION is for, so it is expected."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)
    rec.phase('busy', 30, expect={'h2': ['CPU_SATURATION']},
              action=lambda: restart_telemetry(net, 'h2', 90))
    rec.phase('tail', TAIL, scored=False)


def slow_attack(net, rec):
    """Low-rate attack: h3 -> h4 at 2 Mbps with CPU 30%. Truth = attack, but it
    stays under every threshold, so a miss (false negative) is expected."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)

    def attack():
        restart_telemetry(net, 'h3', 30)
        flow(net, 'h3', '2M', 30)
    rec.phase('slow_attack', 30, expect={'h3': ['HOST_FLOOD']}, action=attack)
    rec.phase('tail', TAIL, scored=False)


def short_burst(net, rec):
    """Full-strength flood but only 3 s long: shorter than the two-poll
    confirmation, so a miss is expected."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)

    def attack():
        restart_telemetry(net, 'h3', 92)
        flow(net, 'h3', '9M', 3)
    rec.phase('burst', 20, expect={'h3': ['HOST_FLOOD']}, action=attack)
    rec.phase('tail', TAIL, scored=False)


def telemetry_kill(net, rec):
    """h3's telemetry client is killed; h3 is otherwise healthy. The correct
    outcome is TELEMETRY_STALE for h3 (host went quiet) and nothing else."""
    setup(net)
    light_background(net, 80)
    flow(net, 'h3', '1M', 80)
    rec.phase('warmup', WARM, scored=False)
    rec.phase('blackout', 30, expect={'h3': ['TELEMETRY_STALE']},
              action=lambda: kill_telemetry(net, 'h3'))
    rec.phase('tail', TAIL, scored=False)


def kill_then_flood(net, rec):
    """Telemetry blackout AND flood. Truth = attack on h3. HOST_FLOOD needs fresh
    telemetry, so a miss is expected; TELEMETRY_STALE/LINK_CONGESTION still can."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)

    def attack():
        kill_telemetry(net, 'h3')
        flow(net, 'h3', '9M', 30)
    rec.phase('blackout_flood', 30,
              expect={'h3': ['HOST_FLOOD'], 'h4': ['LINK_CONGESTION']},
              # while the frozen last report is still "fresh" (<6 s), the rules see
              # real flood traffic next to calm stale numbers: those side effects
              # are consequences of the blackout, not independent false alarms
              allow={'h3': ['TELEMETRY_STALE', 'HIGH_VOLUME_LOW_CPU',
                            'TELEMETRY_MISMATCH']}, action=attack)
    rec.phase('tail', TAIL, scored=False)


def forged_low_cpu(net, rec):
    """Compromised h3 floods at 9 Mbps but forges 'CPU 10%'. Truth = attack. The
    critical HOST_FLOOD is downgraded to HIGH_VOLUME_LOW_CPU (a severity miss)."""
    setup(net)
    light_background(net, 80)
    rec.phase('warmup', WARM, scored=False)

    def attack():
        restart_telemetry(net, 'h3', 10)
        flow(net, 'h3', '9M', 30)
    rec.phase('forged_flood', 30,
              expect={'h3': ['HOST_FLOOD'], 'h4': ['LINK_CONGESTION']},
              allow={'h3': ['HIGH_VOLUME_LOW_CPU']}, action=attack)
    rec.phase('tail', TAIL, scored=False)


SCENARIOS = {f.__name__: f for f in (
    normal, flood, benign_bulk, backup_busy_cpu, many_to_one, cpu_busy,
    slow_attack, short_burst, telemetry_kill, kill_then_flood, forged_low_cpu)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', choices=sorted(SCENARIOS))
    ap.add_argument('--out', help='run directory; truth.json is written here')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()
    if args.list or not args.scenario or not args.out:
        for name, fn in SCENARIOS.items():
            print(f'{name:18s} {fn.__doc__.strip().splitlines()[0]}')
        return

    os.makedirs(args.out, exist_ok=True)
    setLogLevel('info')
    net, _root = build()
    rec = Recorder(args.scenario)
    try:
        net.pingAll()
        SCENARIOS[args.scenario](net, rec)
    finally:
        rec.write(os.path.join(args.out, 'truth.json'))
        net.stop()


if __name__ == '__main__':
    main()
