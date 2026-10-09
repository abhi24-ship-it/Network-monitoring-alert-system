#!/usr/bin/env python3
"""Mininet topology + scenario runner.

  sudo python3 topology.py --scenario normal
  sudo python3 topology.py --scenario abnormal
  sudo python3 topology.py --scenario cli        (build and drop to Mininet CLI)

Layout:  h1 h2 h3 h4 -- s1 -- root namespace (10.0.0.254, the collector side)
Start the controller first:  ryu-manager monitor_controller.py
"""
import argparse
import os
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.net import Mininet
from mininet.node import Node, OVSSwitch, RemoteController

BASE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR_IP = '10.0.0.254'
PY = 'python3'


def build():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)
    s1 = net.addSwitch('s1', protocols='OpenFlow13')
    hosts = [net.addHost(f'h{i}', ip=f'10.0.0.{i}/24') for i in range(1, 5)]
    for h in hosts:
        net.addLink(h, s1, bw=10)           # 10 Mbps -> must match controller

    # Root-namespace attachment so the controller process can receive UDP.
    root = Node('root', inNamespace=False)
    lnk = net.addLink(root, s1)
    net.start()
    root.setIP(f'{COLLECTOR_IP}/24', intf=lnk.intf1)
    return net, root


def start_telemetry(net, cpu_overrides=None):
    cpu_overrides = cpu_overrides or {}
    for name in ('h1', 'h2', 'h3', 'h4'):    # every host reports; h4 is also the sink
        h = net.get(name)
        extra = f'--cpu-override {cpu_overrides[name]}' if name in cpu_overrides else ''
        h.cmd(f'{PY} {BASE}/telemetry_client.py --server {COLLECTOR_IP} '
              f'--interval 2 --host-id {name} {extra} '
              f'> /tmp/{name}_telemetry.log 2>&1 &')


def run_normal(net):
    h1, h2, h4 = net.get('h1', 'h2', 'h4')
    start_telemetry(net, {'h1': 20, 'h2': 25, 'h3': 10, 'h4': 15})
    h4.cmd('iperf -s -u -p 5001 > /tmp/h4_iperf.log 2>&1 &')
    time.sleep(1)
    h1.cmd('iperf -c 10.0.0.4 -u -p 5001 -b 1M -t 40 > /dev/null 2>&1 &')
    h2.cmd('iperf -c 10.0.0.4 -u -p 5001 -b 1M -t 40 > /dev/null 2>&1 &')
    info('*** NORMAL: h1,h2 -> h4 at 1 Mbps each for 40s\n')
    time.sleep(45)


def run_abnormal(net):
    h1, h2, h4 = net.get('h1', 'h2', 'h4')
    start_telemetry(net, {'h1': 20, 'h2': 25, 'h3': 10, 'h4': 15})  # h3 starts normal too
    h4.cmd('iperf -s -u -p 5001 > /tmp/h4_iperf.log 2>&1 &')
    time.sleep(1)
    h1.cmd('iperf -c 10.0.0.4 -u -p 5001 -b 1M -t 60 > /dev/null 2>&1 &')
    h2.cmd('iperf -c 10.0.0.4 -u -p 5001 -b 1M -t 60 > /dev/null 2>&1 &')
    info('*** 15s of baseline traffic\n')
    time.sleep(15)

    info('*** ATTACK: h3 floods h4 at 9 Mbps, h3 telemetry reports 92%% CPU\n')
    h3 = net.get('h3')
    h3.cmd('pkill -f "telemetry_client.py.*--host-id h3"')
    h3.cmd(f'{PY} {BASE}/telemetry_client.py --server {COLLECTOR_IP} --interval 2 '
           f'--host-id h3 --cpu-override 92 > /tmp/h3_telemetry.log 2>&1 &')
    h3.cmd('iperf -c 10.0.0.4 -u -p 5001 -b 9M -t 30 > /dev/null 2>&1 &')
    time.sleep(35)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', choices=['normal', 'abnormal', 'cli'], default='cli')
    args = ap.parse_args()

    setLogLevel('info')
    net, root = build()
    try:
        net.pingAll()
        if args.scenario == 'normal':
            run_normal(net)
        elif args.scenario == 'abnormal':
            run_abnormal(net)
        else:
            start_telemetry(net)
            CLI(net)
    finally:
        net.stop()


if __name__ == '__main__':
    main()
