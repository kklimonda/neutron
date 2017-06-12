# Copyright 2015 Red Hat, Inc.
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

import mock
from neutron_lib import constants

from neutron.agent import firewall
from neutron.agent.linux.openvswitch_firewall import constants as ovsfw_consts
from neutron.agent.linux.openvswitch_firewall import firewall as ovsfw
from neutron.agent.linux.openvswitch_firewall import rules
from neutron.common import constants as n_const
from neutron.plugins.ml2.drivers.openvswitch.agent.common import constants \
        as ovs_consts
from neutron.tests import base

TESTING_VLAN_TAG = 1


class TestIsValidPrefix(base.BaseTestCase):
    def test_valid_prefix_ipv4(self):
        is_valid = rules.is_valid_prefix('10.0.0.0/0')
        self.assertTrue(is_valid)

    def test_invalid_prefix_ipv4(self):
        is_valid = rules.is_valid_prefix('0.0.0.0/0')
        self.assertFalse(is_valid)

    def test_valid_prefix_ipv6(self):
        is_valid = rules.is_valid_prefix('ffff::0/0')
        self.assertTrue(is_valid)

    def test_invalid_prefix_ipv6(self):
        is_valid = rules.is_valid_prefix('0000:0::0/0')
        self.assertFalse(is_valid)
        is_valid = rules.is_valid_prefix('::0/0')
        self.assertFalse(is_valid)
        is_valid = rules.is_valid_prefix('::/0')
        self.assertFalse(is_valid)


class TestCreateFlowsFromRuleAndPort(base.BaseTestCase):
    def setUp(self):
        super(TestCreateFlowsFromRuleAndPort, self).setUp()
        ovs_port = mock.Mock(vif_mac='00:00:00:00:00:00')
        ovs_port.ofport = 1
        port_dict = {'device': 'port_id'}
        self.port = ovsfw.OFPort(
            port_dict, ovs_port, vlan_tag=TESTING_VLAN_TAG)

        self.create_flows_mock = mock.patch.object(
            rules, 'create_protocol_flows').start()

    @property
    def passed_flow_template(self):
        return self.create_flows_mock.call_args[0][1]

    def _test_create_flows_from_rule_and_port_helper(
            self, rule, expected_template):
        rules.create_flows_from_rule_and_port(rule, self.port)

        self.assertEqual(expected_template, self.passed_flow_template)

    def test_create_flows_from_rule_and_port_no_ip_ipv4(self):
        rule = {
            'ethertype': constants.IPv4,
            'direction': firewall.INGRESS_DIRECTION,
        }
        expected_template = {
            'priority': 70,
            'dl_type': n_const.ETHERTYPE_IP,
            'reg_port': self.port.ofport,
        }
        self._test_create_flows_from_rule_and_port_helper(rule,
                                                          expected_template)

    def test_create_flows_from_rule_and_port_src_and_dst_ipv4(self):
        rule = {
            'ethertype': constants.IPv4,
            'direction': firewall.INGRESS_DIRECTION,
            'source_ip_prefix': '192.168.0.0/24',
            'dest_ip_prefix': '10.0.0.1/32',
        }
        expected_template = {
            'priority': 70,
            'dl_type': n_const.ETHERTYPE_IP,
            'reg_port': self.port.ofport,
            'nw_src': '192.168.0.0/24',
            'nw_dst': '10.0.0.1/32',
        }
        self._test_create_flows_from_rule_and_port_helper(rule,
                                                          expected_template)

    def test_create_flows_from_rule_and_port_src_and_dst_with_zero_ipv4(self):
        rule = {
            'ethertype': constants.IPv4,
            'direction': firewall.INGRESS_DIRECTION,
            'source_ip_prefix': '192.168.0.0/24',
            'dest_ip_prefix': '0.0.0.0/0',
        }
        expected_template = {
            'priority': 70,
            'dl_type': n_const.ETHERTYPE_IP,
            'reg_port': self.port.ofport,
            'nw_src': '192.168.0.0/24',
        }
        self._test_create_flows_from_rule_and_port_helper(rule,
                                                          expected_template)

    def test_create_flows_from_rule_and_port_no_ip_ipv6(self):
        rule = {
            'ethertype': constants.IPv6,
            'direction': firewall.INGRESS_DIRECTION,
        }
        expected_template = {
            'priority': 70,
            'dl_type': n_const.ETHERTYPE_IPV6,
            'reg_port': self.port.ofport,
        }
        self._test_create_flows_from_rule_and_port_helper(rule,
                                                          expected_template)

    def test_create_flows_from_rule_and_port_src_and_dst_ipv6(self):
        rule = {
            'ethertype': constants.IPv6,
            'direction': firewall.INGRESS_DIRECTION,
            'source_ip_prefix': '2001:db8:bbbb::1/64',
            'dest_ip_prefix': '2001:db8:aaaa::1/64',
        }
        expected_template = {
            'priority': 70,
            'dl_type': n_const.ETHERTYPE_IPV6,
            'reg_port': self.port.ofport,
            'ipv6_src': '2001:db8:bbbb::1/64',
            'ipv6_dst': '2001:db8:aaaa::1/64',
        }
        self._test_create_flows_from_rule_and_port_helper(rule,
                                                          expected_template)

    def test_create_flows_from_rule_and_port_src_and_dst_with_zero_ipv6(self):
        rule = {
            'ethertype': constants.IPv6,
            'direction': firewall.INGRESS_DIRECTION,
            'source_ip_prefix': '2001:db8:bbbb::1/64',
            'dest_ip_prefix': '::/0',
        }
        expected_template = {
            'priority': 70,
            'dl_type': n_const.ETHERTYPE_IPV6,
            'reg_port': self.port.ofport,
            'ipv6_src': '2001:db8:bbbb::1/64',
        }
        self._test_create_flows_from_rule_and_port_helper(rule,
                                                          expected_template)


class TestCreateProtocolFlows(base.BaseTestCase):
    def setUp(self):
        super(TestCreateProtocolFlows, self).setUp()
        ovs_port = mock.Mock(vif_mac='00:00:00:00:00:00')
        ovs_port.ofport = 1
        port_dict = {'device': 'port_id'}
        self.port = ovsfw.OFPort(
            port_dict, ovs_port, vlan_tag=TESTING_VLAN_TAG)

    def _test_create_protocol_flows_helper(self, direction, rule,
                                           expected_flows):
        flow_template = {'some_settings': 'foo'}
        for flow in expected_flows:
            flow.update(flow_template)
        flows = rules.create_protocol_flows(
            direction, flow_template, self.port, rule)
        self.assertEqual(expected_flows, flows)

    def test_create_protocol_flows_ingress(self):
        rule = {'protocol': constants.PROTO_NAME_TCP}
        expected_flows = [{
            'table': ovs_consts.RULES_INGRESS_TABLE,
            'dl_dst': self.port.mac,
            'actions': 'strip_vlan,output:1',
            'nw_proto': constants.PROTO_NUM_TCP,
        }]
        self._test_create_protocol_flows_helper(
            firewall.INGRESS_DIRECTION, rule, expected_flows)

    def test_create_protocol_flows_egress(self):
        rule = {'protocol': constants.PROTO_NAME_TCP}
        expected_flows = [{
            'table': ovs_consts.RULES_EGRESS_TABLE,
            'dl_src': self.port.mac,
            'actions': 'resubmit(,{:d})'.format(
                ovs_consts.ACCEPT_OR_INGRESS_TABLE),
            'nw_proto': constants.PROTO_NUM_TCP,
        }]
        self._test_create_protocol_flows_helper(
            firewall.EGRESS_DIRECTION, rule, expected_flows)

    def test_create_protocol_flows_no_protocol(self):
        rule = {}
        expected_flows = [{
            'table': ovs_consts.RULES_EGRESS_TABLE,
            'dl_src': self.port.mac,
            'actions': 'resubmit(,{:d})'.format(
                ovs_consts.ACCEPT_OR_INGRESS_TABLE),
        }]
        self._test_create_protocol_flows_helper(
            firewall.EGRESS_DIRECTION, rule, expected_flows)

    def test_create_protocol_flows_icmp6(self):
        rule = {'ethertype': constants.IPv6,
                'protocol': constants.PROTO_NAME_ICMP}
        expected_flows = [{
            'table': ovs_consts.RULES_EGRESS_TABLE,
            'dl_src': self.port.mac,
            'actions': 'resubmit(,{:d})'.format(
                ovs_consts.ACCEPT_OR_INGRESS_TABLE),
            'nw_proto': constants.PROTO_NUM_IPV6_ICMP,
        }]
        self._test_create_protocol_flows_helper(
            firewall.EGRESS_DIRECTION, rule, expected_flows)

    def test_create_protocol_flows_port_range(self):
        rule = {'ethertype': constants.IPv4,
                'protocol': constants.PROTO_NAME_TCP,
                'port_range_min': 22,
                'port_range_max': 23}
        expected_flows = [{
            'table': ovs_consts.RULES_EGRESS_TABLE,
            'dl_src': self.port.mac,
            'actions': 'resubmit(,{:d})'.format(
                ovs_consts.ACCEPT_OR_INGRESS_TABLE),
            'nw_proto': constants.PROTO_NUM_TCP,
            'tcp_dst': '0x0016/0xfffe'
        }]
        self._test_create_protocol_flows_helper(
            firewall.EGRESS_DIRECTION, rule, expected_flows)


class TestCreatePortRangeFlows(base.BaseTestCase):
    def _test_create_port_range_flows_helper(self, expected_flows, rule):
        flow_template = {'some_settings': 'foo'}
        for flow in expected_flows:
            flow.update(flow_template)
        port_range_flows = rules.create_port_range_flows(flow_template, rule)
        self.assertEqual(expected_flows, port_range_flows)

    def test_create_port_range_flows_with_source_and_destination(self):
        rule = {
            'protocol': constants.PROTO_NAME_TCP,
            'source_port_range_min': 123,
            'source_port_range_max': 124,
            'port_range_min': 10,
            'port_range_max': 11,
        }
        expected_flows = [
            {'tcp_src': '0x007b', 'tcp_dst': '0x000a/0xfffe'},
            {'tcp_src': '0x007c', 'tcp_dst': '0x000a/0xfffe'},
        ]
        self._test_create_port_range_flows_helper(expected_flows, rule)

    def test_create_port_range_flows_with_source(self):
        rule = {
            'protocol': constants.PROTO_NAME_TCP,
            'source_port_range_min': 123,
            'source_port_range_max': 124,
        }
        expected_flows = [
            {'tcp_src': '0x007b'},
            {'tcp_src': '0x007c'},
        ]
        self._test_create_port_range_flows_helper(expected_flows, rule)

    def test_create_port_range_flows_with_destination(self):
        rule = {
            'protocol': constants.PROTO_NAME_TCP,
            'port_range_min': 10,
            'port_range_max': 11,
        }
        expected_flows = [
            {'tcp_dst': '0x000a/0xfffe'},
        ]
        self._test_create_port_range_flows_helper(expected_flows, rule)

    def test_create_port_range_flows_without_port_range(self):
        rule = {
            'protocol': constants.PROTO_NAME_TCP,
        }
        expected_flows = []
        self._test_create_port_range_flows_helper(expected_flows, rule)

    def test_create_port_range_with_icmp_protocol(self):
        rule = {
            'protocol': 'icmp',
            'port_range_min': 10,
            'port_range_max': 11,
        }
        expected_flows = []
        self._test_create_port_range_flows_helper(expected_flows, rule)


class TestCreateFlowsForIpAddress(base.BaseTestCase):
    def _generate_conjuncion_actions(self, conj_ids, offset):
        return ','.join(
            ["conjunction(%d,1/2)" % (c + offset)
             for c in conj_ids])

    def test_create_flows_for_ip_address_egress(self):
        expected_template = {
            'table': ovs_consts.RULES_EGRESS_TABLE,
            'priority': 70,
            'dl_type': n_const.ETHERTYPE_IP,
            'reg_net': 0x123,
            'nw_dst': '192.168.0.1/32'
        }

        conj_ids = [12, 20]
        flows = rules.create_flows_for_ip_address(
            '192.168.0.1', firewall.EGRESS_DIRECTION, constants.IPv4,
            0x123, conj_ids)

        self.assertEqual(2, len(flows))
        self.assertEqual(ovsfw_consts.OF_STATE_ESTABLISHED_NOT_REPLY,
                         flows[0]['ct_state'])
        self.assertEqual(ovsfw_consts.OF_STATE_NEW_NOT_ESTABLISHED,
                         flows[1]['ct_state'])
        self.assertEqual(self._generate_conjuncion_actions(conj_ids, 0),
                         flows[0]['actions'])
        self.assertEqual(self._generate_conjuncion_actions(conj_ids, 1),
                         flows[1]['actions'])
        for f in flows:
            del f['actions']
            del f['ct_state']
            self.assertEqual(expected_template, f)


class TestCreateConjFlows(base.BaseTestCase):
    def test_create_conj_flows(self):
        ovs_port = mock.Mock(ofport=1, vif_mac='00:00:00:00:00:00')
        port_dict = {'device': 'port_id'}
        port = ovsfw.OFPort(
            port_dict, ovs_port, vlan_tag=TESTING_VLAN_TAG)

        conj_id = 1234
        expected_template = {
            'table': ovs_consts.RULES_INGRESS_TABLE,
            'dl_dst': port.mac,
            'dl_type': n_const.ETHERTYPE_IPV6,
            'priority': 70,
            'conj_id': conj_id,
            'reg_port': port.ofport
        }

        flows = rules.create_conj_flows(port, conj_id,
                                        firewall.INGRESS_DIRECTION,
                                        constants.IPv6)

        self.assertEqual(ovsfw_consts.OF_STATE_ESTABLISHED_NOT_REPLY,
                         flows[0]['ct_state'])
        self.assertEqual(ovsfw_consts.OF_STATE_NEW_NOT_ESTABLISHED,
                         flows[1]['ct_state'])
        self.assertEqual("strip_vlan,output:{:d}".format(port.ofport),
                         flows[0]['actions'])
        self.assertEqual("ct(commit,zone=NXM_NX_REG{:d}[0..15]),{:s}".format(
            ovsfw_consts.REG_NET, flows[0]['actions']),
                         flows[1]['actions'])

        for f in flows:
            del f['actions']
            del f['ct_state']
            self.assertEqual(expected_template, f)
            expected_template['conj_id'] += 1
