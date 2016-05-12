# Copyright (c) 2014 OpenStack Foundation, all rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock
from neutron_lib import constants
from oslo_config import cfg

from neutron.callbacks import events
from neutron.callbacks import registry
from neutron.callbacks import resources
from neutron import context
from neutron.db import dvr_mac_db
from neutron.extensions import dvr
from neutron.extensions import portbindings
from neutron import manager
from neutron.tests.unit.plugins.ml2 import test_plugin


class DVRDbMixinImpl(dvr_mac_db.DVRDbMixin):

    def __init__(self, notifier):
        self.notifier = notifier


class DvrDbMixinTestCase(test_plugin.Ml2PluginV2TestCase):

    def setUp(self):
        super(DvrDbMixinTestCase, self).setUp()
        self.ctx = context.get_admin_context()
        self.mixin = DVRDbMixinImpl(mock.Mock())

    def _create_dvr_mac_entry(self, host, mac_address):
        with self.ctx.session.begin(subtransactions=True):
            entry = dvr_mac_db.DistributedVirtualRouterMacAddress(
                host=host, mac_address=mac_address)
            self.ctx.session.add(entry)

    def test__get_dvr_mac_address_by_host(self):
        with self.ctx.session.begin(subtransactions=True):
            entry = dvr_mac_db.DistributedVirtualRouterMacAddress(
                host='foo_host', mac_address='foo_mac_address')
            self.ctx.session.add(entry)
        result = self.mixin._get_dvr_mac_address_by_host(self.ctx, 'foo_host')
        self.assertEqual(entry, result)

    def test__get_dvr_mac_address_by_host_not_found(self):
        self.assertRaises(dvr.DVRMacAddressNotFound,
                          self.mixin._get_dvr_mac_address_by_host,
                          self.ctx, 'foo_host')

    def test__create_dvr_mac_address_success(self):
        entry = {'host': 'foo_host', 'mac_address': '00:11:22:33:44:55:66'}
        with mock.patch.object(dvr_mac_db.utils, 'get_random_mac') as f:
            f.return_value = entry['mac_address']
            expected = self.mixin._create_dvr_mac_address(
                self.ctx, entry['host'])
        self.assertEqual(expected, entry)

    def test__create_dvr_mac_address_retries_exceeded_retry_logic(self):
        new_retries = 8
        cfg.CONF.set_override('mac_generation_retries', new_retries)
        self._create_dvr_mac_entry('foo_host_1', 'non_unique_mac')
        with mock.patch.object(dvr_mac_db.utils, 'get_random_mac') as f:
            f.return_value = 'non_unique_mac'
            self.assertRaises(dvr.MacAddressGenerationFailure,
                              self.mixin._create_dvr_mac_address,
                              self.ctx, "foo_host_2")
        self.assertEqual(new_retries, f.call_count)

    def test_mac_not_cleared_on_agent_delete_event_with_remaining_agents(self):
        plugin = manager.NeutronManager.get_plugin()
        self._create_dvr_mac_entry('host_1', 'mac_1')
        self._create_dvr_mac_entry('host_2', 'mac_2')
        agent1 = {'host': 'host_1', 'id': 'a1'}
        agent2 = {'host': 'host_1', 'id': 'a2'}
        with mock.patch.object(plugin, 'get_agents', return_value=[agent2]):
            with mock.patch.object(plugin, 'notifier') as notifier:
                registry.notify(resources.AGENT, events.BEFORE_DELETE, self,
                                context=self.ctx, agent=agent1)
        mac_list = self.mixin.get_dvr_mac_address_list(self.ctx)
        self.assertEqual(2, len(mac_list))
        self.assertFalse(notifier.dvr_mac_address_update.called)

    def test_mac_cleared_on_agent_delete_event(self):
        plugin = manager.NeutronManager.get_plugin()
        self._create_dvr_mac_entry('host_1', 'mac_1')
        self._create_dvr_mac_entry('host_2', 'mac_2')
        agent = {'host': 'host_1', 'id': 'a1'}
        with mock.patch.object(plugin, 'notifier') as notifier:
            registry.notify(resources.AGENT, events.BEFORE_DELETE, self,
                            context=self.ctx, agent=agent)
        mac_list = self.mixin.get_dvr_mac_address_list(self.ctx)
        self.assertEqual(1, len(mac_list))
        self.assertEqual('host_2', mac_list[0]['host'])
        notifier.dvr_mac_address_update.assert_called_once_with(
            self.ctx, mac_list)

    def test_get_dvr_mac_address_list(self):
        self._create_dvr_mac_entry('host_1', 'mac_1')
        self._create_dvr_mac_entry('host_2', 'mac_2')
        mac_list = self.mixin.get_dvr_mac_address_list(self.ctx)
        self.assertEqual(2, len(mac_list))

    def test_get_dvr_mac_address_by_host_existing_host(self):
        self._create_dvr_mac_entry('foo_host', 'foo_mac')
        with mock.patch.object(self.mixin,
                               '_get_dvr_mac_address_by_host') as f:
            self.mixin.get_dvr_mac_address_by_host(self.ctx, 'foo_host')
            self.assertEqual(1, f.call_count)

    def test_get_dvr_mac_address_by_host_missing_host(self):
        with mock.patch.object(self.mixin, '_create_dvr_mac_address') as f:
            self.mixin.get_dvr_mac_address_by_host(self.ctx, 'foo_host')
            self.assertEqual(1, f.call_count)

    def test_get_subnet_for_dvr_returns_correct_mac(self):
        with self.subnet() as subnet,\
                self.port(subnet=subnet),\
                self.port(subnet=subnet):
            dvr_subnet = self.mixin.get_subnet_for_dvr(self.ctx,
                                                       subnet['subnet']['id'])
            # no gateway port should be found so no info should be returned
            self.assertEqual({}, dvr_subnet)
            with self.port(
                    subnet=subnet,
                    fixed_ips=[{'ip_address': subnet['subnet'][
                        'gateway_ip']}]) as gw_port:
                dvr_subnet = self.mixin.get_subnet_for_dvr(
                    self.ctx, subnet['subnet']['id'])
                self.assertEqual(gw_port['port']['mac_address'],
                                 dvr_subnet['gateway_mac'])

    def test_get_subnet_for_dvr_returns_correct_mac_fixed_ips_passed(self):
        with self.subnet() as subnet,\
                self.port(subnet=subnet,
                          fixed_ips=[{'ip_address': '10.0.0.2'}]),\
                self.port(subnet=subnet,
                          fixed_ips=[{'ip_address': '10.0.0.3'}]):
            fixed_ips = [{'subnet_id': subnet['subnet']['id'],
                          'ip_address': '10.0.0.4'}]
            dvr_subnet = self.mixin.get_subnet_for_dvr(
                self.ctx, subnet['subnet']['id'], fixed_ips)
            # no gateway port should be found so no info should be returned
            self.assertEqual({}, dvr_subnet)
            with self.port(
                    subnet=subnet,
                    fixed_ips=[{'ip_address': '10.0.0.4'}]) as gw_port:
                dvr_subnet = self.mixin.get_subnet_for_dvr(
                    self.ctx, subnet['subnet']['id'], fixed_ips)
                self.assertEqual(gw_port['port']['mac_address'],
                                 dvr_subnet['gateway_mac'])

    def test_get_ports_on_host_by_subnet(self):
        HOST = 'host1'
        host_arg = {portbindings.HOST_ID: HOST}
        arg_list = (portbindings.HOST_ID,)
        with self.subnet() as subnet,\
                self.port(subnet=subnet,
                          device_owner=constants.DEVICE_OWNER_COMPUTE_PREFIX,
                          arg_list=arg_list, **host_arg) as compute_port,\
                self.port(subnet=subnet,
                          device_owner=constants.DEVICE_OWNER_DHCP,
                          arg_list=arg_list, **host_arg) as dhcp_port,\
                self.port(subnet=subnet,
                          device_owner=constants.DEVICE_OWNER_LOADBALANCER,
                          arg_list=arg_list, **host_arg) as lb_port,\
                self.port(device_owner=constants.DEVICE_OWNER_COMPUTE_PREFIX,
                          arg_list=arg_list, **host_arg),\
                self.port(subnet=subnet,
                          device_owner=constants.DEVICE_OWNER_COMPUTE_PREFIX,
                          arg_list=arg_list,
                          **{portbindings.HOST_ID: 'other'}),\
                self.port(subnet=subnet,
                          device_owner=constants.DEVICE_OWNER_NETWORK_PREFIX,
                          arg_list=arg_list, **host_arg):
            expected_ids = [port['port']['id'] for port in
                            [compute_port, dhcp_port, lb_port]]
            dvr_ports = self.mixin.get_ports_on_host_by_subnet(
                self.ctx, HOST, subnet['subnet']['id'])
            self.assertEqual(len(expected_ids), len(dvr_ports))
            self.assertEqual(expected_ids, [port['id'] for port in dvr_ports])
