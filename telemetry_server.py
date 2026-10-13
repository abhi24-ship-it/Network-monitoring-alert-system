#!/usr/bin/env python3
"""Standalone UDP collector for Phase 2 (verify the telemetry path BEFORE
involving the controller). Do not run it together with monitor_controller.py:
the controller binds the same port.

Run in the root namespace:  python3 telemetry_server.py
Hosts send to 10.0.0.254:9999 (the root-namespace link on s1).
"""
import json
import socket
import time
from monitor_utils import decode_telemetry

PORT = 9999


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))
    last_seq = {}
    print(f'[collector] listening on UDP :{PORT}')
    with open('telemetry.jsonl', 'a') as log:
        while True:
            data, (ip, _) = sock.recvfrom(4096)
            try:
                msg = decode_telemetry(data)
            except ValueError:
                print(f'[collector] malformed datagram from {ip}')
                continue
            msg['src_ip'] = ip
            msg['rx_ts'] = time.time()
            prev = last_seq.get(ip)
            lost = (msg['seq'] - prev - 1) if prev is not None else 0
            last_seq[ip] = msg['seq']
            log.write(json.dumps(msg) + '\n')
            log.flush()
            print(f"[{ip:>10}] seq={msg['seq']:<4} cpu={msg['cpu_pct']:5.1f}% "
                  f"tx={msg['tx_bps']/1e6:6.2f} Mbps lost_since_last={max(lost, 0)}")


if __name__ == '__main__':
    main()
