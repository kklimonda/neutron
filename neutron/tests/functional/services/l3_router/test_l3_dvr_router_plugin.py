# Copyright (c) 2015 Red Hat, Inc.
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

from neutron.api.v2 import attributes
from neutron.common import constants as l3_const
from neutron.extensions import external_net
from neutron.tests.common import helpers
from neutron.tests.unit.plugins.ml2 import base as ml2_test_base


class L3DvrTestCase(ml2_test_base.ML2TestFramework):
    def setUp(self):
        super(L3DvrTestCase, self).setUp()
        self.l3_agent = helpers.register_l3_agent(
            agent_mode=l3_const.L3_AGENT_MODE_DVR_SNAT)

    def _create_router(self, distributed=True):
        return (super(L3DvrTestCase, self).
                _create_router(distributed=distributed))

    def test_update_router_db_centralized_to_distributed(self):
        router = self._create_router(distributed=False)
        # router needs to be in admin state down in order to be upgraded to DVR
        self.l3_plugin.update_router(
            self.context, router['id'], {'router': {'admin_state_up': False}})
        self.assertFalse(router['distributed'])
        self.l3_plugin.update_router(
            self.context, router['id'], {'router': {'distributed': True}})
        router = self.l3_plugin.get_router(self.context, router['id'])
        self.assertTrue(router['distributed'])

    def test_get_device_owner_distributed_router_object(self):
        router = self._create_router()
        self.assertEqual(
            l3_const.DEVICE_OWNER_DVR_INTERFACE,
            self.l3_plugin._get_device_owner(self.context, router))

    def test_get_device_owner_distributed_router_id(self):
        router = self._create_router()
        self.assertEqual(
            l3_const.DEVICE_OWNER_DVR_INTERFACE,
            self.l3_plugin._get_device_owner(self.context, router['id']))

    def test_get_device_owner_centralized(self):
        router = self._create_router(distributed=False)
        self.assertEqual(
            l3_const.DEVICE_OWNER_ROUTER_INTF,
            self.l3_plugin._get_device_owner(self.context, router['id']))

    def test_get_agent_gw_ports_exist_for_network_no_port(self):
        self.assertIsNone(
            self.l3_plugin._get_agent_gw_ports_exist_for_network(
                self.context, 'network_id', 'host', 'agent_id'))

    def _test_remove_router_interface_leaves_snat_intact(self, by_subnet):
        with self.subnet() as subnet1, \
                self.subnet(cidr='20.0.0.0/24') as subnet2:
            kwargs = {'arg_list': (external_net.EXTERNAL,),
                      external_net.EXTERNAL: True}
            with self.network(**kwargs) as ext_net, \
                    self.subnet(network=ext_net,
                                cidr='30.0.0.0/24'):
                router = self._create_router()
                self.l3_plugin.add_router_interface(
                    self.context, router['id'],
                    {'subnet_id': subnet1['subnet']['id']})
                self.l3_plugin.add_router_interface(
                    self.context, router['id'],
                    {'subnet_id': subnet2['subnet']['id']})
                self.l3_plugin._update_router_gw_info(
                    self.context, router['id'],
                    {'network_id': ext_net['network']['id']})

                snat_router_intfs = self.l3_plugin._get_snat_sync_interfaces(
                    self.context, [router['id']])
                self.assertEqual(
                    2, len(snat_router_intfs[router['id']]))

                if by_subnet:
                    self.l3_plugin.remove_router_interface(
                        self.context, router['id'],
                        {'subnet_id': subnet1['subnet']['id']})
                else:
                    port = self.core_plugin.get_ports(
                        self.context, filters={
                            'network_id': [subnet1['subnet']['network_id']],
                            'device_owner':
                                [l3_const.DEVICE_OWNER_DVR_INTERFACE]})[0]
                    self.l3_plugin.remove_router_interface(
                        self.context, router['id'],
                        {'port_id': port['id']})

                self.assertEqual(
                    1, len(self.l3_plugin._get_snat_sync_interfaces(
                        self.context, [router['id']])))

    def test_remove_router_interface_by_subnet_leaves_snat_intact(self):
        self._test_remove_router_interface_leaves_snat_intact(by_subnet=True)

    def test_remove_router_interface_by_port_leaves_snat_intact(self):
        self._test_remove_router_interface_leaves_snat_intact(
            by_subnet=False)

    def setup_create_agent_gw_port_for_network(self, network=None):
        if not network:
            network = self._make_network(self.fmt, '', True)
        network_id = network['network']['id']
        port = self.core_plugin.create_port(
            self.context,
            {'port': {'tenant_id': '',
                      'network_id': network_id,
                      'mac_address': attributes.ATTR_NOT_SPECIFIED,
                      'fixed_ips': attributes.ATTR_NOT_SPECIFIED,
                      'device_id': self.l3_agent['id'],
                      'device_owner': l3_const.DEVICE_OWNER_AGENT_GW,
                      'binding:host_id': '',
                      'admin_state_up': True,
                      'name': ''}})
        return network_id, port

    def test_get_agent_gw_port_for_network(self):
        network_id, port = (
            self.setup_create_agent_gw_port_for_network())

        self.assertEqual(
            port['id'],
            self.l3_plugin._get_agent_gw_ports_exist_for_network(
                self.context, network_id, None, self.l3_agent['id'])['id'])

    def test_delete_agent_gw_port_for_network(self):
        network_id, port = (
            self.setup_create_agent_gw_port_for_network())

        self.l3_plugin.delete_floatingip_agent_gateway_port(
            self.context, "", network_id)
        self.assertIsNone(
            self.l3_plugin._get_agent_gw_ports_exist_for_network(
                self.context, network_id, "", self.l3_agent['id']))

    def test_get_fip_sync_interfaces(self):
        self.setup_create_agent_gw_port_for_network()

        self.assertEqual(
            1, len(self.l3_plugin._get_fip_sync_interfaces(
                self.context, self.l3_agent['id'])))

    def test_process_routers(self):
        router = self._create_router()
        result = self.l3_plugin._process_routers(self.context, [router])
        self.assertEqual(
            router['id'], result[router['id']]['id'])

    def test_get_router_ids(self):
        router = self._create_router()
        self.assertEqual(
            router['id'],
            self.l3_plugin._get_router_ids(self.context)[0])
        self._create_router()
        self.assertEqual(
            2, len(self.l3_plugin._get_router_ids(self.context)))

    def test_agent_gw_port_delete_when_last_gateway_for_ext_net_removed(self):
        kwargs = {'arg_list': (external_net.EXTERNAL,),
                  external_net.EXTERNAL: True}
        net1 = self._make_network(self.fmt, 'net1', True)
        net2 = self._make_network(self.fmt, 'net2', True)
        subnet1 = self._make_subnet(
            self.fmt, net1, '10.1.0.1', '10.1.0.0/24', enable_dhcp=True)
        subnet2 = self._make_subnet(
            self.fmt, net2, '10.1.0.1', '10.1.0.0/24', enable_dhcp=True)
        ext_net = self._make_network(self.fmt, 'ext_net', True, **kwargs)
        self._make_subnet(
            self.fmt, ext_net, '20.0.0.1', '20.0.0.0/24', enable_dhcp=True)
        # Create first router and add an interface
        router1 = self._create_router()
        ext_net_id = ext_net['network']['id']
        self.l3_plugin.add_router_interface(
            self.context, router1['id'],
            {'subnet_id': subnet1['subnet']['id']})
        # Set gateway to first router
        self.l3_plugin._update_router_gw_info(
            self.context, router1['id'],
            {'network_id': ext_net_id})
        # Create second router and add an interface
        router2 = self._create_router()
        self.l3_plugin.add_router_interface(
            self.context, router2['id'],
            {'subnet_id': subnet2['subnet']['id']})
        # Set gateway to second router
        self.l3_plugin._update_router_gw_info(
            self.context, router2['id'],
            {'network_id': ext_net_id})
        # Create an agent gateway port for the external network
        net_id, agent_gw_port = (
            self.setup_create_agent_gw_port_for_network(network=ext_net))
        # Check for agent gateway ports
        self.assertIsNotNone(
            self.l3_plugin._get_agent_gw_ports_exist_for_network(
                self.context, ext_net_id, "", self.l3_agent['id']))
        self.l3_plugin._update_router_gw_info(
            self.context, router1['id'], {})
        # Check for agent gateway port after deleting one of the gw
        self.assertIsNotNone(
            self.l3_plugin._get_agent_gw_ports_exist_for_network(
                self.context, ext_net_id, "", self.l3_agent['id']))
        self.l3_plugin._update_router_gw_info(
            self.context, router2['id'], {})
        # Check for agent gateway port after deleting last gw
        self.assertIsNone(
            self.l3_plugin._get_agent_gw_ports_exist_for_network(
                self.context, ext_net_id, "", self.l3_agent['id']))

    def test_update_vm_port_host_router_update(self):
        # register l3 agents in dvr mode in addition to existing dvr_snat agent
        HOST1 = 'host1'
        dvr_agent1 = helpers.register_l3_agent(
            host=HOST1, agent_mode=l3_const.L3_AGENT_MODE_DVR)
        HOST2 = 'host2'
        dvr_agent2 = helpers.register_l3_agent(
            host=HOST2, agent_mode=l3_const.L3_AGENT_MODE_DVR)
        router = self._create_router()
        with self.subnet() as subnet:
            self.l3_plugin.add_router_interface(
                self.context, router['id'],
                {'subnet_id': subnet['subnet']['id']})

            # since there are no vm ports on HOST, and the router
            # has no external gateway at this point the router
            # should neither be scheduled to dvr nor to dvr_snat agents
            agents = self.l3_plugin.list_l3_agents_hosting_router(
                self.context, router['id'])['agents']
            self.assertEqual(0, len(agents))
            with mock.patch.object(self.l3_plugin,
                                   '_l3_rpc_notifier') as l3_notifier,\
                    self.port(subnet=subnet,
                              device_owner='compute:None') as port:
                self.l3_plugin.agent_notifiers[
                    l3_const.AGENT_TYPE_L3] = l3_notifier
                self.core_plugin.update_port(
                    self.context, port['port']['id'],
                    {'port': {'binding:host_id': HOST1}})

                # now router should be scheduled to agent on HOST1
                agents = self.l3_plugin.list_l3_agents_hosting_router(
                    self.context, router['id'])['agents']
                self.assertEqual(1, len(agents))
                self.assertEqual(dvr_agent1['id'], agents[0]['id'])
                # and notification should only be sent to the agent on HOST1
                l3_notifier.routers_updated_on_host.assert_called_once_with(
                    self.context, {router['id']}, HOST1)
                self.assertFalse(l3_notifier.routers_updated.called)

                # updating port's host (instance migration)
                l3_notifier.reset_mock()
                self.core_plugin.update_port(
                    self.context, port['port']['id'],
                    {'port': {'binding:host_id': HOST2}})
                # now router should only be scheduled to dvr agent on host2
                agents = self.l3_plugin.list_l3_agents_hosting_router(
                    self.context, router['id'])['agents']
                self.assertEqual(1, len(agents))
                self.assertEqual(dvr_agent2['id'], agents[0]['id'])
                l3_notifier.routers_updated_on_host.assert_called_once_with(
                    self.context, {router['id']}, HOST2)
                l3_notifier.router_removed_from_agent.assert_called_once_with(
                    self.context, router['id'], HOST1)

    def _test_create_floating_ip_agent_notification(self, dvr=True):
        with self.subnet() as ext_subnet,\
                self.subnet(cidr='20.0.0.0/24') as int_subnet,\
                self.port(subnet=int_subnet,
                          device_owner='compute:None') as int_port:
            # make net external
            ext_net_id = ext_subnet['subnet']['network_id']
            self._update('networks', ext_net_id,
                     {'network': {external_net.EXTERNAL: True}})

            router = self._create_router(distributed=dvr)
            self.l3_plugin.update_router(
                self.context, router['id'],
                {'router': {
                    'external_gateway_info': {'network_id': ext_net_id}}})
            self.l3_plugin.add_router_interface(
                self.context, router['id'],
                {'subnet_id': int_subnet['subnet']['id']})

            floating_ip = {'floating_network_id': ext_net_id,
                           'router_id': router['id'],
                           'port_id': int_port['port']['id'],
                           'tenant_id': int_port['port']['tenant_id']}
            with mock.patch.object(
                    self.l3_plugin, '_l3_rpc_notifier') as l3_notif:
                self.l3_plugin.create_floatingip(
                    self.context, {'floatingip': floating_ip})
                if dvr:
                    l3_notif.routers_updated_on_host.assert_called_once_with(
                        self.context, [router['id']],
                        int_port['port']['binding:host_id'])
                    self.assertFalse(l3_notif.routers_updated.called)
                else:
                    l3_notif.routers_updated.assert_called_once_with(
                        self.context, [router['id']], None)
                    self.assertFalse(
                        l3_notif.routers_updated_on_host.called)

    def test_create_floating_ip_agent_notification(self):
        self._test_create_floating_ip_agent_notification()

    def test_create_floating_ip_agent_notification_non_dvr(self):
        self._test_create_floating_ip_agent_notification(dvr=False)

    def _test_update_floating_ip_agent_notification(self, dvr=True):
        with self.subnet() as ext_subnet,\
                self.subnet(cidr='20.0.0.0/24') as int_subnet1,\
                self.subnet(cidr='30.0.0.0/24') as int_subnet2,\
                self.port(subnet=int_subnet1,
                          device_owner='compute:None') as int_port1,\
                self.port(subnet=int_subnet2,
                          device_owner='compute:None') as int_port2:
            # locate internal ports on different hosts
            self.core_plugin.update_port(
                self.context, int_port1['port']['id'],
                {'port': {'binding:host_id': 'host1'}})
            self.core_plugin.update_port(
                self.context, int_port2['port']['id'],
                {'port': {'binding:host_id': 'host2'}})
            # and create l3 agents on corresponding hosts
            helpers.register_l3_agent(host='host1',
                agent_mode=l3_const.L3_AGENT_MODE_DVR)
            helpers.register_l3_agent(host='host2',
                agent_mode=l3_const.L3_AGENT_MODE_DVR)

            # make net external
            ext_net_id = ext_subnet['subnet']['network_id']
            self._update('networks', ext_net_id,
                     {'network': {external_net.EXTERNAL: True}})

            router1 = self._create_router(distributed=dvr)
            router2 = self._create_router(distributed=dvr)
            for router in (router1, router2):
                self.l3_plugin.update_router(
                    self.context, router['id'],
                    {'router': {
                        'external_gateway_info': {'network_id': ext_net_id}}})
            self.l3_plugin.add_router_interface(
                self.context, router1['id'],
                {'subnet_id': int_subnet1['subnet']['id']})
            self.l3_plugin.add_router_interface(
                self.context, router2['id'],
                {'subnet_id': int_subnet2['subnet']['id']})

            floating_ip = {'floating_network_id': ext_net_id,
                           'router_id': router1['id'],
                           'port_id': int_port1['port']['id'],
                           'tenant_id': int_port1['port']['tenant_id']}
            floating_ip = self.l3_plugin.create_floatingip(
                self.context, {'floatingip': floating_ip})

            with mock.patch.object(
                    self.l3_plugin, '_l3_rpc_notifier') as l3_notif:
                updated_floating_ip = {'router_id': router2['id'],
                                       'port_id': int_port2['port']['id']}
                self.l3_plugin.update_floatingip(
                    self.context, floating_ip['id'],
                    {'floatingip': updated_floating_ip})
                if dvr:
                    self.assertEqual(
                        2, l3_notif.routers_updated_on_host.call_count)
                    expected_calls = [
                        mock.call(self.context, [router1['id']], 'host1'),
                        mock.call(self.context, [router2['id']], 'host2')]
                    l3_notif.routers_updated_on_host.assert_has_calls(
                        expected_calls)
                    self.assertFalse(l3_notif.routers_updated.called)
                else:
                    self.assertEqual(
                        2, l3_notif.routers_updated.call_count)
                    expected_calls = [
                        mock.call(self.context, [router1['id']], None),
                        mock.call(self.context, [router2['id']], None)]
                    l3_notif.routers_updated.assert_has_calls(
                        expected_calls)
                    self.assertFalse(l3_notif.routers_updated_on_host.called)

    def test_update_floating_ip_agent_notification(self):
        self._test_update_floating_ip_agent_notification()

    def test_update_floating_ip_agent_notification_non_dvr(self):
        self._test_update_floating_ip_agent_notification(dvr=False)

    def _test_delete_floating_ip_agent_notification(self, dvr=True):
        with self.subnet() as ext_subnet,\
                self.subnet(cidr='20.0.0.0/24') as int_subnet,\
                self.port(subnet=int_subnet,
                          device_owner='compute:None') as int_port:
            # make net external
            ext_net_id = ext_subnet['subnet']['network_id']
            self._update('networks', ext_net_id,
                     {'network': {external_net.EXTERNAL: True}})

            router = self._create_router(distributed=dvr)
            self.l3_plugin.update_router(
                self.context, router['id'],
                {'router': {
                    'external_gateway_info': {'network_id': ext_net_id}}})
            self.l3_plugin.add_router_interface(
                self.context, router['id'],
                {'subnet_id': int_subnet['subnet']['id']})

            floating_ip = {'floating_network_id': ext_net_id,
                           'router_id': router['id'],
                           'port_id': int_port['port']['id'],
                           'tenant_id': int_port['port']['tenant_id']}
            floating_ip = self.l3_plugin.create_floatingip(
                self.context, {'floatingip': floating_ip})
            with mock.patch.object(
                    self.l3_plugin, '_l3_rpc_notifier') as l3_notif:
                self.l3_plugin.delete_floatingip(
                    self.context, floating_ip['id'])
                if dvr:
                    l3_notif.routers_updated_on_host.assert_called_once_with(
                        self.context, [router['id']],
                        int_port['port']['binding:host_id'])
                    self.assertFalse(l3_notif.routers_updated.called)
                else:
                    l3_notif.routers_updated.assert_called_once_with(
                        self.context, [router['id']], None)
                    self.assertFalse(
                        l3_notif.routers_updated_on_host.called)

    def test_delete_floating_ip_agent_notification(self):
        self._test_delete_floating_ip_agent_notification()

    def test_delete_floating_ip_agent_notification_non_dvr(self):
        self._test_delete_floating_ip_agent_notification(dvr=False)

    def test_router_is_not_removed_from_snat_agent_on_interface_removal(self):
        """Check that dvr router is not removed from l3 agent hosting
        SNAT for it on router interface removal
        """
        router = self._create_router()
        kwargs = {'arg_list': (external_net.EXTERNAL,),
                  external_net.EXTERNAL: True}
        host = self.l3_agent['host']
        with self.subnet() as subnet,\
                self.network(**kwargs) as ext_net,\
                self.subnet(network=ext_net, cidr='20.0.0.0/24'):
            self.l3_plugin._update_router_gw_info(
                self.context, router['id'],
                {'network_id': ext_net['network']['id']})
            self.l3_plugin.add_router_interface(
                self.context, router['id'],
                {'subnet_id': subnet['subnet']['id']})

            agents = self.l3_plugin.list_l3_agents_hosting_router(
                self.context, router['id'])
            self.assertEqual(1, len(agents['agents']))
            csnat_agent_host = self.l3_plugin.get_snat_bindings(
                self.context, [router['id']])[0]['l3_agent']['host']
            self.assertEqual(host, csnat_agent_host)
            with mock.patch.object(self.l3_plugin,
                                   '_l3_rpc_notifier') as l3_notifier:
                self.l3_plugin.remove_router_interface(
                        self.context, router['id'],
                        {'subnet_id': subnet['subnet']['id']})
                agents = self.l3_plugin.list_l3_agents_hosting_router(
                    self.context, router['id'])
                self.assertEqual(1, len(agents['agents']))
                self.assertFalse(l3_notifier.router_removed_from_agent.called)

    def test_router_is_not_removed_from_snat_agent_on_dhcp_port_deletion(self):
        """Check that dvr router is not removed from l3 agent hosting
        SNAT for it on DHCP port removal
        """
        router = self._create_router()
        kwargs = {'arg_list': (external_net.EXTERNAL,),
                  external_net.EXTERNAL: True}
        with self.network(**kwargs) as ext_net,\
                self.subnet(network=ext_net),\
                self.subnet(cidr='20.0.0.0/24') as subnet,\
                self.port(subnet=subnet,
                          device_owner=l3_const.DEVICE_OWNER_DHCP) as port:
            self.core_plugin.update_port(
                    self.context, port['port']['id'],
                    {'port': {'binding:host_id': self.l3_agent['host']}})
            self.l3_plugin._update_router_gw_info(
                self.context, router['id'],
                {'network_id': ext_net['network']['id']})
            self.l3_plugin.add_router_interface(
                self.context, router['id'],
                {'subnet_id': subnet['subnet']['id']})

            # router should be scheduled to the dvr_snat l3 agent
            csnat_agent_host = self.l3_plugin.get_snat_bindings(
                self.context, [router['id']])[0]['l3_agent']['host']
            self.assertEqual(self.l3_agent['host'], csnat_agent_host)
            agents = self.l3_plugin.list_l3_agents_hosting_router(
                self.context, router['id'])
            self.assertEqual(1, len(agents['agents']))
            self.assertEqual(self.l3_agent['id'], agents['agents'][0]['id'])

            notifier = self.l3_plugin.agent_notifiers[
                l3_const.AGENT_TYPE_L3]
            with mock.patch.object(
                    notifier, 'router_removed_from_agent') as remove_mock:
                self._delete('ports', port['port']['id'])
                # now when port is deleted the router still has external
                # gateway and should still be scheduled to the snat agent
                agents = self.l3_plugin.list_l3_agents_hosting_router(
                    self.context, router['id'])
                self.assertEqual(1, len(agents['agents']))
                self.assertEqual(self.l3_agent['id'],
                                 agents['agents'][0]['id'])
                self.assertFalse(remove_mock.called)
