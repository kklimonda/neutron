# Copyright 2016 OVH SAS
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

from oslo_config import cfg
from oslo_utils import uuidutils

from neutron.agent.linux import tc_lib
from neutron.objects.qos import rule
from neutron.plugins.ml2.drivers.linuxbridge.agent.common import config  # noqa
from neutron.plugins.ml2.drivers.linuxbridge.agent.extension_drivers import (
    qos_driver)
from neutron.tests import base


TEST_LATENCY_VALUE = 100
DSCP_VALUE = 32


class QosLinuxbridgeAgentDriverTestCase(base.BaseTestCase):

    def setUp(self):
        super(QosLinuxbridgeAgentDriverTestCase, self).setUp()
        cfg.CONF.set_override("tbf_latency", TEST_LATENCY_VALUE, "QOS")
        self.qos_driver = qos_driver.QosLinuxbridgeAgentDriver()
        self.qos_driver.initialize()
        self.rule_bw_limit = self._create_bw_limit_rule_obj()
        self.rule_dscp_marking = self._create_dscp_marking_rule_obj()
        self.port = self._create_fake_port(uuidutils.generate_uuid())

    def _create_bw_limit_rule_obj(self):
        rule_obj = rule.QosBandwidthLimitRule()
        rule_obj.id = uuidutils.generate_uuid()
        rule_obj.max_kbps = 2
        rule_obj.max_burst_kbps = 200
        rule_obj.obj_reset_changes()
        return rule_obj

    def _create_dscp_marking_rule_obj(self):
        rule_obj = rule.QosDscpMarkingRule()
        rule_obj.id = uuidutils.generate_uuid()
        rule_obj.dscp_mark = DSCP_VALUE
        rule_obj.obj_reset_changes()
        return rule_obj

    def _create_fake_port(self, policy_id):
        return {'qos_policy_id': policy_id,
                'network_qos_policy_id': None,
                'device': 'fake_tap'}

    def _dscp_mark_chain_name(self, device):
        return "qos-o%s" % device[3:]

    def _dscp_postrouting_rule(self, device):
        return ("-m physdev --physdev-in %s --physdev-is-bridged "
                "-j $qos-o%s") % (device, device[3:])

    def _dscp_rule(self, dscp_mark_value):
        return "-j DSCP --set-dscp %s" % dscp_mark_value

    def _dscp_rule_tag(self, device):
        return "dscp-%s" % device

    def test_create_bandwidth_limit(self):
        with mock.patch.object(
            tc_lib.TcCommand, "set_filters_bw_limit"
        ) as set_bw_limit:
            self.qos_driver.create_bandwidth_limit(self.port,
                                                   self.rule_bw_limit)
            set_bw_limit.assert_called_once_with(
                self.rule_bw_limit.max_kbps, self.rule_bw_limit.max_burst_kbps,
            )

    def test_update_bandwidth_limit(self):
        with mock.patch.object(
            tc_lib.TcCommand, "update_filters_bw_limit"
        ) as update_bw_limit:
            self.qos_driver.update_bandwidth_limit(self.port,
                                                   self.rule_bw_limit)
            update_bw_limit.assert_called_once_with(
                self.rule_bw_limit.max_kbps, self.rule_bw_limit.max_burst_kbps,
            )

    def test_delete_bandwidth_limit(self):
        with mock.patch.object(
            tc_lib.TcCommand, "delete_filters_bw_limit"
        ) as delete_bw_limit:
            self.qos_driver.delete_bandwidth_limit(self.port)
            delete_bw_limit.assert_called_once_with()

    def test_create_dscp_marking(self):
        expected_calls = [
            mock.call.add_chain(
                self._dscp_mark_chain_name(self.port['device'])),
            mock.call.add_rule(
                "POSTROUTING",
                self._dscp_postrouting_rule(self.port['device'])),
            mock.call.add_rule(
                self._dscp_mark_chain_name(self.port['device']),
                self._dscp_rule(DSCP_VALUE),
                tag=self._dscp_rule_tag(self.port['device'])
            )
        ]
        with mock.patch.object(
            self.qos_driver, "iptables_manager") as iptables_manager:

            iptables_manager.ip4['mangle'] = mock.Mock()
            iptables_manager.ip6['mangle'] = mock.Mock()
            self.qos_driver.create_dscp_marking(
                self.port, self.rule_dscp_marking)
            iptables_manager.ipv4['mangle'].assert_has_calls(expected_calls)
            iptables_manager.ipv6['mangle'].assert_has_calls(expected_calls)

    def test_update_dscp_marking(self):
        expected_calls = [
            mock.call.clear_rules_by_tag(
                self._dscp_rule_tag(self.port['device'])),
            mock.call.add_chain(
                self._dscp_mark_chain_name(self.port['device'])),
            mock.call.add_rule(
                "POSTROUTING",
                self._dscp_postrouting_rule(self.port['device'])),
            mock.call.add_rule(
                self._dscp_mark_chain_name(self.port['device']),
                self._dscp_rule(DSCP_VALUE),
                tag=self._dscp_rule_tag(self.port['device'])
            )
        ]
        with mock.patch.object(
            self.qos_driver, "iptables_manager") as iptables_manager:

            iptables_manager.ip4['mangle'] = mock.Mock()
            iptables_manager.ip6['mangle'] = mock.Mock()
            self.qos_driver.update_dscp_marking(
                self.port, self.rule_dscp_marking)
            iptables_manager.ipv4['mangle'].assert_has_calls(expected_calls)
            iptables_manager.ipv6['mangle'].assert_has_calls(expected_calls)

    def test_delete_dscp_marking_chain_empty(self):
        dscp_chain_name = self._dscp_mark_chain_name(self.port['device'])
        expected_calls = [
            mock.call.clear_rules_by_tag(
                self._dscp_rule_tag(self.port['device'])),
            mock.call.remove_chain(
                dscp_chain_name),
            mock.call.remove_rule(
                "POSTROUTING",
                self._dscp_postrouting_rule(self.port['device']))
        ]
        with mock.patch.object(
            self.qos_driver, "iptables_manager") as iptables_manager:

            iptables_manager.ip4['mangle'] = mock.Mock()
            iptables_manager.ip6['mangle'] = mock.Mock()
            iptables_manager.get_chain = mock.Mock(return_value=[])
            self.qos_driver.delete_dscp_marking(self.port)
            iptables_manager.ipv4['mangle'].assert_has_calls(expected_calls)
            iptables_manager.ipv6['mangle'].assert_has_calls(expected_calls)
            iptables_manager.get_chain.assert_has_calls([
                mock.call("mangle", dscp_chain_name, ip_version=4),
                mock.call("mangle", dscp_chain_name, ip_version=6)
            ])

    def test_delete_dscp_marking_chain_not_empty(self):
        dscp_chain_name = self._dscp_mark_chain_name(self.port['device'])
        expected_calls = [
            mock.call.clear_rules_by_tag(
                self._dscp_rule_tag(self.port['device'])),
        ]
        with mock.patch.object(
            self.qos_driver, "iptables_manager") as iptables_manager:

            iptables_manager.ip4['mangle'] = mock.Mock()
            iptables_manager.ip6['mangle'] = mock.Mock()
            iptables_manager.get_chain = mock.Mock(
                return_value=["some other rule"])
            self.qos_driver.delete_dscp_marking(self.port)
            iptables_manager.ipv4['mangle'].assert_has_calls(expected_calls)
            iptables_manager.ipv6['mangle'].assert_has_calls(expected_calls)
            iptables_manager.get_chain.assert_has_calls([
                mock.call("mangle", dscp_chain_name, ip_version=4),
                mock.call("mangle", dscp_chain_name, ip_version=6)
            ])
            iptables_manager.ipv4['mangle'].remove_chain.assert_not_called()
            iptables_manager.ipv4['mangle'].remove_rule.assert_not_called()
