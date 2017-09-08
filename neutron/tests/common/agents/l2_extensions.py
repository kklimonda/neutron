# Copyright (c) 2015 Red Hat, Inc.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import re

from neutron.agent.linux import async_process
from neutron.agent.linux import iptables_manager
from neutron.common import utils as common_utils


IPv4_ADDR_REGEX = r"(\d{1,3}\.){3}\d{1,3}"


class TcpdumpException(Exception):
    pass


def extract_mod_nw_tos_action(flows):
    tos_mark = None
    if flows:
        flow_list = flows.splitlines()
        for flow in flow_list:
            if 'mod_nw_tos' in flow:
                actions = flow.partition('actions=')[2]
                after_mod = actions.partition('mod_nw_tos:')[2]
                tos_mark = int(after_mod.partition(',')[0])
    return tos_mark


def extract_dscp_value_from_iptables_rules(rules):
    pattern = (r"^-A neutron-linuxbri-qos-.* -j DSCP "
               "--set-dscp (?P<dscp_value>0x[A-Fa-f0-9]+)$")
    for rule in rules:
        m = re.match(pattern, rule)
        if m:
            return int(m.group("dscp_value"), 16)


def wait_until_bandwidth_limit_rule_applied(bridge, port_vif, rule):
    def _bandwidth_limit_rule_applied():
        bw_rule = bridge.get_egress_bw_limit_for_port(port_vif)
        expected = None, None
        if rule:
            expected = rule.max_kbps, rule.max_burst_kbps
        return bw_rule == expected

    common_utils.wait_until_true(_bandwidth_limit_rule_applied)


def wait_until_dscp_marking_rule_applied_ovs(bridge, port_vif, rule):
    def _dscp_marking_rule_applied():
        port_num = bridge.get_port_ofport(port_vif)

        flows = bridge.dump_flows_for(table='0', in_port=str(port_num))
        dscp_mark = extract_mod_nw_tos_action(flows)

        expected = None
        if rule:
            expected = rule << 2
        return dscp_mark == expected

    common_utils.wait_until_true(_dscp_marking_rule_applied)


def wait_until_dscp_marking_rule_applied_linuxbridge(
    namespace, port_vif, expected_rule):

    iptables = iptables_manager.IptablesManager(
        namespace=namespace)

    def _dscp_marking_rule_applied():
        mangle_rules = iptables.get_rules_for_table("mangle")
        dscp_mark = extract_dscp_value_from_iptables_rules(mangle_rules)
        return dscp_mark == expected_rule

    common_utils.wait_until_true(_dscp_marking_rule_applied)


def wait_for_dscp_marked_packet(sender_vm, receiver_vm, dscp_mark):
    cmd = ["tcpdump", "-i", receiver_vm.port.name, "-nlt"]
    if dscp_mark:
        cmd += ["(ip[1] & 0xfc == %s)" % (dscp_mark << 2)]
    tcpdump_async = async_process.AsyncProcess(cmd, run_as_root=True,
                                               namespace=receiver_vm.namespace)
    tcpdump_async.start()
    sender_vm.block_until_ping(receiver_vm.ip)
    try:
        tcpdump_async.stop()
    except async_process.AsyncProcessException:
        # If it was already stopped than we don't care about it
        pass

    pattern = (r"IP (?P<src_ip>%(ip_addr_regex)s) > "
               "(?P<dst_ip>%(ip_addr_regex)s): ICMP .*$" % {
                   'ip_addr_regex': IPv4_ADDR_REGEX})
    for line in tcpdump_async.iter_stdout():
        m = re.match(pattern, line)
        if m and (m.group("src_ip") == sender_vm.ip and
            m.group("dst_ip") == receiver_vm.ip):
            return
    raise TcpdumpException(
        "No packets marked with DSCP = %(dscp_mark)s received from %(src)s "
        "to %(dst)s" % {'dscp_mark': dscp_mark,
                        'src': sender_vm.ip,
                        'dst': receiver_vm.ip})
