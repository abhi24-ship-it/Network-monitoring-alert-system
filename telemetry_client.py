#!/usr/bin/env python3
"""UDP telemetry client. Runs INSIDE a Mininet host (via h.cmd).

Sends one JSON datagram every --interval seconds:
  host, seq, ts, cpu_pct, mem_pct, tx_bps, rx_bps, iface

tx_bps/rx_bps come from /proc/net/dev, which is network-namespace aware, so
they really are this host's own interface counters. cpu_pct is NOT per-host
(all Mininet hosts share the VM's CPUs); use --cpu-override for a
deterministic demo, or CPULimitedHost + cgroup stats for something real.
"""
import argparse
import json
import socket
import time

import psutil


def read_iface_bytes():
    """(iface, rx_bytes, tx_bytes) of the first non-loopback iface in this netns."""
    with open('/proc/net/dev') as f:
        lines = f.readlines()[2:]
    for line in lines:
        name, data = line.split(':', 1)
        name = name.strip()
        if name == 'lo':
            continue
        cols = data.split()
        return name, int(cols[0]), int(cols[8])
    return None, 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--server', required=True, help='collector IP')
    ap.add_argument('--port', type=int, default=9999)
    ap.add_argument('--interval', type=float, default=2.0)
    ap.add_argument('--host-id', default=socket.gethostname())
    ap.add_argument('--cpu-override', type=float, default=None,
                    help='report this CPU%% instead of measuring (demo shim)')
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    psutil.cpu_percent(None)  # prime; first call always returns 0.0
    iface, rx0, tx0 = read_iface_bytes()
    t0 = time.monotonic()
    seq = 0

    while True:
        time.sleep(args.interval)
        iface, rx, tx = read_iface_bytes()
        now = time.monotonic()
        dt = max(now - t0, 1e-6)
        msg = {
            'host': args.host_id,
            'seq': seq,
            'ts': time.time(),
            'iface': iface,
            'cpu_pct': args.cpu_override if args.cpu_override is not None
                       else psutil.cpu_percent(None),
            'mem_pct': psutil.virtual_memory().percent,
            'tx_bps': (tx - tx0) * 8 / dt,
            'rx_bps': (rx - rx0) * 8 / dt,
        }
        rx0, tx0, t0 = rx, tx, now
        seq += 1
        try:
            sock.sendto(json.dumps(msg).encode(), (args.server, args.port))
        except OSError:
            pass  # network not ready yet; UDP is fire-and-forget by design


if __name__ == '__main__':
    main()
