#!/usr/bin/env python3
"""Mininet topology + scenario runner.

  sudo python3 topology.py --scenario normal
  sudo python3 topology.py --scenario abnormal
  sudo python3 topology.py --scenario cli        (build and drop to Mininet CLI)

Layout:  h1 h2 h3 h4 -- s1 -- root namespace (10.0.0.254, the collector side)
Start the controller first:  PYTHONPATH=. ryu-manager monitor_controller.py
"""
import argparse
import os

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.net import Mininet
from mininet.node import Node, OVSSwitch, RemoteController
from process_manager import ProcessManager

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
    try:
        net.start()
        root.setIP(f'{COLLECTOR_IP}/24', intf=lnk.intf1)
    except Exception:
        try:
            net.stop()
        finally:
            root.terminate()
        raise
    return net, root


def start_host_telemetry(net, jobs, name, cpu=None):
    argv = [PY, os.path.join(BASE, 'telemetry_client.py'),
            '--server', COLLECTOR_IP, '--interval', '2', '--host-id', name]
    if cpu is not None:
        argv.extend(['--cpu-override', str(cpu)])
    jobs.start(f'{name}-telemetry', net.get(name), argv,
               f'/tmp/{name}_telemetry.log')


def start_telemetry(net, jobs, cpu_overrides=None):
    cpu_overrides = cpu_overrides or {}
    for name in ('h1', 'h2', 'h3', 'h4'):    # every host reports; h4 is also the sink
        start_host_telemetry(net, jobs, name, cpu_overrides.get(name))


def start_traffic(net, jobs, seconds):
    jobs.start('h4-iperf', net.get('h4'), ['iperf', '-s', '-u', '-p', '5001'],
               '/tmp/h4_iperf.log')
    jobs.wait(1)
    for name in ('h1', 'h2'):
        start_sender(net, jobs, name, '1M', seconds)


def start_sender(net, jobs, name, rate, seconds):
    jobs.start(f'{name}-iperf', net.get(name),
               ['iperf', '-c', '10.0.0.4', '-u', '-p', '5001',
                '-b', rate, '-t', str(seconds)],
               f'/tmp/{name}_iperf.log', persistent=False)


def run_normal(net, jobs):
    start_telemetry(net, jobs, {'h1': 20, 'h2': 25, 'h3': 10, 'h4': 15})
    start_traffic(net, jobs, 40)
    info('*** NORMAL: h1,h2 -> h4 at 1 Mbps each for 40s\n')
    jobs.wait(45)


def run_abnormal(net, jobs):
    start_telemetry(net, jobs, {'h1': 20, 'h2': 25, 'h3': 10, 'h4': 15})
    start_traffic(net, jobs, 60)
    info('*** 15s of baseline traffic\n')
    jobs.wait(15)

    info('*** ATTACK: h3 sends at 9 Mbps, h3 telemetry reports 92% CPU\n')
    jobs.stop('h3-telemetry')
    start_host_telemetry(net, jobs, 'h3', 92)
    start_sender(net, jobs, 'h3', '9M', 30)
    jobs.wait(35)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', choices=['normal', 'abnormal', 'cli'], default='cli')
    args = ap.parse_args()

    setLogLevel('info')
    net, root = build()
    jobs = ProcessManager()
    try:
        if net.pingAll() != 0:
            raise RuntimeError('Connectivity check failed; check Ryu before running a scenario')
        if args.scenario == 'normal':
            run_normal(net, jobs)
        elif args.scenario == 'abnormal':
            run_abnormal(net, jobs)
        else:
            start_telemetry(net, jobs)
            CLI(net)
            jobs.check()
    finally:
        try:
            jobs.close()
        finally:
            try:
                net.stop()
            finally:
                root.terminate()


if __name__ == '__main__':
    main()
