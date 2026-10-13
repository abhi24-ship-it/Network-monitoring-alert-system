#!/usr/bin/env python3
"""Ryu app: learning switch + flow-stats poller + UDP telemetry collector +
cross-layer anomaly engine.

Run:  PYTHONPATH=. ryu-manager monitor_controller.py

Layers fused here:
  network layer     -> per-host tx/rx rate from OpenFlow flow byte counters
  application layer -> per-host CPU / self-reported tx rate from UDP telemetry
"""
import json
import socket
import time
from collections import defaultdict

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER,
                                    DEAD_DISPATCHER, set_ev_cls)
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ipv4, udp, ether_types
from ryu.ofproto import ofproto_v1_3
from monitor_utils import decode_telemetry, flow_rate

# ---------------------------------------------------------------- config ---
TELEMETRY_PORT = 9999
ROOT_IP = '10.0.0.254'            # root-namespace interface (collector side)
LINK_CAPACITY_BPS = 10_000_000    # must match bw= in topology.py
POLL_INTERVAL = 2.0               # seconds between flow-stats requests
STALE_AFTER = 3 * 2.0             # telemetry older than this = stale
MIN_BPS = 50_000                  # ignore anything below 50 kbps
CONFIRM_CYCLES = 2                # consecutive violations before alerting
COOLDOWN = 10.0                   # seconds between repeats of the same alert
ALERT_FILE = 'alerts.jsonl'

T = {
    'util_high': 0.70,       # >=70% of link capacity
    'util_low': 0.20,
    'cpu_high': 80.0,        # percent
    'mismatch_ratio': 0.50,  # |switch - host| / max(...) beyond this = mismatch
    'mismatch_min_bps': 500_000,
}


class NetworkMonitor(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.telemetry = {}                 # src ip -> latest telemetry dict
        self.last_counter = {}              # flow key -> (bytes, duration, monotonic time)
        self.pending_stats = {}             # switch id -> (request id, reply parts)
        self.streak = defaultdict(int)      # (ip, rule) -> consecutive hits
        self.last_alert = {}                # (ip, rule) -> last alert time
        hub.spawn(self._telemetry_loop)
        hub.spawn(self._poll_loop)

    # ------------------------------------------------- switch plumbing ----
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(dp, 0, parser.OFPMatch(), actions)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[ev.datapath.id] = ev.datapath
        else:
            self.datapaths.pop(ev.datapath.id, None)
            self.mac_to_port.pop(ev.datapath.id, None)
            self.pending_stats.pop(ev.datapath.id, None)
            self.last_counter = {k: v for k, v in self.last_counter.items()
                                 if k[0] != ev.datapath.id}

    def _add_flow(self, dp, priority, match, actions, idle=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                      instructions=inst, idle_timeout=idle))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port
        out_port = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        # Install an IPv4 flow whose match exposes src/dst/proto (and UDP dst
        # port) so stats can be attributed per host and telemetry traffic can
        # be separated from "real" traffic.
        ip = pkt.get_protocol(ipv4.ipv4)
        if ip and out_port != ofp.OFPP_FLOOD:
            fields = dict(eth_type=ether_types.ETH_TYPE_IP,
                          ipv4_src=ip.src, ipv4_dst=ip.dst, ip_proto=ip.proto)
            u = pkt.get_protocol(udp.udp)
            if u:
                fields['udp_dst'] = u.dst_port
            self._add_flow(dp, 10, parser.OFPMatch(**fields), actions, idle=10)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions, data=data))

    # ---------------------------------------- application-layer telemetry --
    def _telemetry_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('0.0.0.0', TELEMETRY_PORT))
        self.logger.info('telemetry collector listening on UDP :%d', TELEMETRY_PORT)
        while True:  # green socket: recvfrom yields to other Ryu threads
            data, (ip, _) = sock.recvfrom(4096)
            try:
                msg = decode_telemetry(data)
            except ValueError:
                continue
            msg['_rx_ts'] = time.monotonic()
            self.telemetry[ip] = msg   # key = real source IP, not a payload field

    # ------------------------------------------------ network-layer stats --
    def _poll_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                dp.send_msg(dp.ofproto_parser.OFPFlowStatsRequest(dp))
            hub.sleep(POLL_INTERVAL)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply(self, ev):
        now = time.monotonic()
        dpid = ev.msg.datapath.id
        # Evaluate one complete snapshot, even if OpenFlow splits the reply.
        xid, parts = self.pending_stats.get(dpid, (ev.msg.xid, []))
        if xid != ev.msg.xid:
            parts = []  # abandon an incomplete older request
        parts.extend(ev.msg.body)
        if ev.msg.flags & ev.msg.datapath.ofproto.OFPMPF_REPLY_MORE:
            self.pending_stats[dpid] = (ev.msg.xid, parts)
            return
        self.pending_stats.pop(dpid, None)
        tx, rx = defaultdict(float), defaultdict(float)
        seen = set()

        for st in parts:
            m = st.match
            src, dst = m.get('ipv4_src'), m.get('ipv4_dst')
            if src is None:          # table-miss entry
                continue
            key = (dpid, src, dst, m.get('ip_proto'), m.get('udp_dst'))
            seen.add(key)
            prev = self.last_counter.get(key)
            age = st.duration_sec + st.duration_nsec / 1e9
            bps = flow_rate(prev, st.byte_count, age, now)
            self.last_counter[key] = (st.byte_count, age, now)

            if m.get('udp_dst') == TELEMETRY_PORT:
                continue             # don't count monitoring overhead as load
            tx[src] += bps
            rx[dst] += bps

        self.last_counter = {k: v for k, v in self.last_counter.items()
                             if k[0] != dpid or k in seen}
        self._evaluate(tx, rx, now)

    # ------------------------------------------------- correlation engine --
    def _evaluate(self, tx, rx, now):
        hosts = (set(self.telemetry) | set(tx) | set(rx)) - {ROOT_IP}
        for ip in sorted(hosts):
            sw_tx, sw_rx = tx.get(ip, 0.0), rx.get(ip, 0.0)
            tx_util = sw_tx / LINK_CAPACITY_BPS
            rx_util = sw_rx / LINK_CAPACITY_BPS
            tel = self.telemetry.get(ip)
            findings = {}

            if tel is None or now - tel['_rx_ts'] > STALE_AFTER:
                if tel is not None or sw_tx > MIN_BPS or sw_rx > MIN_BPS:
                    findings['TELEMETRY_STALE'] = (
                        'WARNING', 'no fresh telemetry but host is known/active '
                                   '(host down, telemetry blocked, or uninstrumented sender)')
            else:
                cpu, host_tx = tel['cpu_pct'], tel['tx_bps']
                if tx_util >= T['util_high'] and cpu >= T['cpu_high']:
                    findings['HOST_FLOOD'] = (
                        'CRITICAL', f'uplink {tx_util:.0%} AND cpu {cpu:.0f}% -> '
                                    'suspected flood or heavy application activity')
                elif tx_util >= T['util_high']:
                    findings['HIGH_VOLUME_LOW_CPU'] = (
                        'WARNING', f'uplink {tx_util:.0%} with cpu only {cpu:.0f}% -> '
                                   'bulk transfer or offloaded/non-app traffic')
                if cpu >= T['cpu_high'] and tx_util < T['util_low']:
                    findings['CPU_SATURATION'] = (
                        'WARNING', f'cpu {cpu:.0f}% but uplink {tx_util:.0%} -> '
                                   'host compute problem, not a network one')
                big = max(sw_tx, host_tx)
                if big > T['mismatch_min_bps'] and \
                        abs(sw_tx - host_tx) / big > T['mismatch_ratio']:
                    findings['TELEMETRY_MISMATCH'] = (
                        'WARNING', f'switch sees {sw_tx/1e6:.2f} Mbps, host reports '
                                   f'{host_tx/1e6:.2f} Mbps -> spoofing, loss or '
                                   'unaccounted traffic')

            if rx_util >= T['util_high']:
                findings['LINK_CONGESTION'] = (
                    'CRITICAL', f'offered downlink load {rx_util:.0%} -> '
                                'high incoming traffic; congestion not confirmed')

            self._debounce(ip, findings, now)

    def _debounce(self, ip, findings, now):
        for (kip, rule) in list(self.streak):
            if kip == ip and rule not in findings:
                self.streak[(kip, rule)] = 0
        for rule, (sev, text) in findings.items():
            k = (ip, rule)
            self.streak[k] += 1
            if self.streak[k] >= CONFIRM_CYCLES and \
                    now - self.last_alert.get(k, float('-inf')) >= COOLDOWN:
                self.last_alert[k] = now
                self._alert(ip, rule, sev, text, now)

    def _alert(self, ip, rule, sev, text, now):
        self.logger.warning('[%s] %s host=%s: %s', sev, rule, ip, text)
        try:
            with open(ALERT_FILE, 'a') as f:
                f.write(json.dumps({'ts': time.time(), 'host': ip, 'rule': rule,
                                    'severity': sev, 'detail': text}) + '\n')
        except OSError:
            self.logger.exception('Could not append alert to %s', ALERT_FILE)
