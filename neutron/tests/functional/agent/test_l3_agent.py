# Copyright (c) 2014 Red Hat, Inc.
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

import copy
import functools
import os.path

import mock
import netaddr
from oslo_config import cfg
from oslo_log import log as logging
import testtools
import webob
import webob.dec
import webob.exc

from neutron.agent.common import config as agent_config
from neutron.agent.common import ovs_lib
from neutron.agent.l3 import agent as neutron_l3_agent
from neutron.agent.l3 import dvr_snat_ns
from neutron.agent.l3 import namespace_manager
from neutron.agent.l3 import namespaces
from neutron.agent import l3_agent as l3_agent_main
from neutron.agent.linux import dhcp
from neutron.agent.linux import external_process
from neutron.agent.linux import ip_lib
from neutron.agent.linux import utils
from neutron.callbacks import events
from neutron.callbacks import manager
from neutron.callbacks import registry
from neutron.callbacks import resources
from neutron.common import config as common_config
from neutron.common import constants as l3_constants
from neutron.common import utils as common_utils
from neutron.openstack.common import uuidutils
from neutron.tests.common import net_helpers
from neutron.tests.functional.agent.linux import base
from neutron.tests.functional.agent.linux import helpers
from neutron.tests.unit import test_l3_agent

LOG = logging.getLogger(__name__)
_uuid = uuidutils.generate_uuid

METADATA_REQUEST_TIMEOUT = 60


def get_ovs_bridge(br_name):
    return ovs_lib.OVSBridge(br_name)


class L3AgentTestFramework(base.BaseOVSLinuxTestCase):
    def setUp(self):
        super(L3AgentTestFramework, self).setUp()
        mock.patch('neutron.agent.l3.agent.L3PluginApi').start()

        # TODO(pcm): Move this to BaseTestCase, if we find that more tests
        # use this mechanism.
        self._callback_manager = manager.CallbacksManager()
        mock.patch.object(registry, '_get_callback_manager',
                          return_value=self._callback_manager).start()
        self.agent = self._configure_agent('agent1')

    def _get_config_opts(self):
        config = cfg.ConfigOpts()
        config.register_opts(common_config.core_opts)
        config.register_opts(common_config.core_cli_opts)
        logging.register_options(config)
        agent_config.register_process_monitor_opts(config)
        return config

    def _configure_agent(self, host):
        conf = self._get_config_opts()
        l3_agent_main.register_opts(conf)
        cfg.CONF.set_override('debug', False)
        agent_config.setup_logging()
        conf.set_override(
            'interface_driver',
            'neutron.agent.linux.interface.OVSInterfaceDriver')
        conf.set_override('router_delete_namespaces', True)

        br_int = self.useFixture(net_helpers.OVSBridgeFixture()).bridge
        br_ex = self.useFixture(net_helpers.OVSBridgeFixture()).bridge
        conf.set_override('ovs_integration_bridge', br_int.br_name)
        conf.set_override('external_network_bridge', br_ex.br_name)

        temp_dir = self.get_new_temp_dir()
        get_temp_file_path = functools.partial(self.get_temp_file_path,
                                               root=temp_dir)
        conf.set_override('state_path', temp_dir.path)
        conf.set_override('metadata_proxy_socket',
                          get_temp_file_path('metadata_proxy'))
        conf.set_override('ha_confs_path',
                          get_temp_file_path('ha_confs'))
        conf.set_override('external_pids',
                          get_temp_file_path('external/pids'))
        conf.set_override('host', host)
        agent = neutron_l3_agent.L3NATAgentWithStateReport(host, conf)
        mock.patch.object(ip_lib, '_arping').start()

        return agent

    def generate_router_info(self, enable_ha, ip_version=4, extra_routes=True,
                             enable_fip=True, enable_snat=True,
                             dual_stack=False, v6_ext_gw_with_sub=True):
        if ip_version == 6 and not dual_stack:
            enable_snat = False
            enable_fip = False
            extra_routes = False

        if not v6_ext_gw_with_sub:
            self.agent.conf.set_override('ipv6_gateway',
                                         'fe80::f816:3eff:fe2e:1')
        return test_l3_agent.prepare_router_data(ip_version=ip_version,
                                                 enable_snat=enable_snat,
                                                 enable_floating_ip=enable_fip,
                                                 enable_ha=enable_ha,
                                                 extra_routes=extra_routes,
                                                 dual_stack=dual_stack,
                                                 v6_ext_gw_with_sub=(
                                                     v6_ext_gw_with_sub))

    def manage_router(self, agent, router):
        self.addCleanup(self._delete_router, agent, router['id'])
        ri = self._create_router(agent, router)
        return ri

    def _create_router(self, agent, router):
        agent._process_added_router(router)
        return agent.router_info[router['id']]

    def _delete_router(self, agent, router_id):
        agent._router_removed(router_id)

    def _add_fip(self, router, fip_address, fixed_address='10.0.0.2',
                 host=None):
        fip = {'id': _uuid(),
               'port_id': _uuid(),
               'floating_ip_address': fip_address,
               'fixed_ip_address': fixed_address,
               'host': host}
        router.router[l3_constants.FLOATINGIP_KEY].append(fip)

    def _add_internal_interface_by_subnet(self, router, count=1,
                                          ip_version=4,
                                          ipv6_subnet_modes=None,
                                          interface_id=None):
        return test_l3_agent.router_append_subnet(router, count,
                ip_version, ipv6_subnet_modes, interface_id)

    def _namespace_exists(self, namespace):
        ip = ip_lib.IPWrapper(namespace=namespace)
        return ip.netns.exists(namespace)

    def _metadata_proxy_exists(self, conf, router):
        pm = external_process.ProcessManager(
            conf,
            router.router_id,
            router.ns_name)
        return pm.active

    def device_exists_with_ips_and_mac(self, expected_device, name_getter,
                                       namespace):
        ip_cidrs = common_utils.fixed_ip_cidrs(expected_device['fixed_ips'])
        return ip_lib.device_exists_with_ips_and_mac(
            name_getter(expected_device['id']), ip_cidrs,
            expected_device['mac_address'], namespace)

    @staticmethod
    def _port_first_ip_cidr(port):
        fixed_ip = port['fixed_ips'][0]
        return common_utils.ip_to_cidr(fixed_ip['ip_address'],
                                       fixed_ip['prefixlen'])

    def get_device_mtu(self, target_device, name_getter, namespace):
        device = ip_lib.IPDevice(name_getter(target_device), namespace)
        return device.link.mtu

    def get_expected_keepalive_configuration(self, router):
        router_id = router.router_id
        ha_device_name = router.get_ha_device_name()
        ha_device_cidr = self._port_first_ip_cidr(router.ha_port)
        external_port = router.get_ex_gw_port()
        ex_port_ipv6 = ip_lib.get_ipv6_lladdr(external_port['mac_address'])
        external_device_name = router.get_external_device_name(
            external_port['id'])
        external_device_cidr = self._port_first_ip_cidr(external_port)
        internal_port = router.router[l3_constants.INTERFACE_KEY][0]
        int_port_ipv6 = ip_lib.get_ipv6_lladdr(internal_port['mac_address'])
        internal_device_name = router.get_internal_device_name(
            internal_port['id'])
        internal_device_cidr = self._port_first_ip_cidr(internal_port)
        floating_ip_cidr = common_utils.ip_to_cidr(
            router.get_floating_ips()[0]['floating_ip_address'])
        default_gateway_ip = external_port['subnets'][0].get('gateway_ip')

        return """vrrp_instance VR_1 {
    state BACKUP
    interface %(ha_device_name)s
    virtual_router_id 1
    priority 50
    nopreempt
    advert_int 2
    track_interface {
        %(ha_device_name)s
    }
    virtual_ipaddress {
        169.254.0.1/24 dev %(ha_device_name)s
    }
    virtual_ipaddress_excluded {
        %(floating_ip_cidr)s dev %(external_device_name)s
        %(external_device_cidr)s dev %(external_device_name)s
        %(internal_device_cidr)s dev %(internal_device_name)s
        %(ex_port_ipv6)s dev %(external_device_name)s scope link
        %(int_port_ipv6)s dev %(internal_device_name)s scope link
    }
    virtual_routes {
        0.0.0.0/0 via %(default_gateway_ip)s dev %(external_device_name)s
        8.8.8.0/24 via 19.4.4.4
    }
}""" % {
            'router_id': router_id,
            'ha_device_name': ha_device_name,
            'ha_device_cidr': ha_device_cidr,
            'external_device_name': external_device_name,
            'external_device_cidr': external_device_cidr,
            'internal_device_name': internal_device_name,
            'internal_device_cidr': internal_device_cidr,
            'floating_ip_cidr': floating_ip_cidr,
            'default_gateway_ip': default_gateway_ip,
            'int_port_ipv6': int_port_ipv6,
            'ex_port_ipv6': ex_port_ipv6
        }

    def _get_rule(self, iptables_manager, table, chain, predicate):
        rules = iptables_manager.get_chain(table, chain)
        result = next(rule for rule in rules if predicate(rule))
        return result

    def _assert_router_does_not_exist(self, router):
        # If the namespace assertion succeeds
        # then the devices and iptable rules have also been deleted,
        # so there's no need to check that explicitly.
        self.assertFalse(self._namespace_exists(router.ns_name))
        utils.wait_until_true(
            lambda: not self._metadata_proxy_exists(self.agent.conf, router))

    def _assert_snat_chains(self, router):
        self.assertFalse(router.iptables_manager.is_chain_empty(
            'nat', 'snat'))
        self.assertFalse(router.iptables_manager.is_chain_empty(
            'nat', 'POSTROUTING'))

    def _assert_floating_ip_chains(self, router):
        self.assertFalse(router.iptables_manager.is_chain_empty(
            'nat', 'float-snat'))

    def _assert_metadata_chains(self, router):
        metadata_port_filter = lambda rule: (
            str(self.agent.conf.metadata_port) in rule.rule)
        self.assertTrue(self._get_rule(router.iptables_manager,
                                       'nat',
                                       'PREROUTING',
                                       metadata_port_filter))
        self.assertTrue(self._get_rule(router.iptables_manager,
                                       'filter',
                                       'INPUT',
                                       metadata_port_filter))

    def _assert_internal_devices(self, router):
        internal_devices = router.router[l3_constants.INTERFACE_KEY]
        self.assertTrue(len(internal_devices))
        for device in internal_devices:
            self.assertTrue(self.device_exists_with_ips_and_mac(
                device, router.get_internal_device_name, router.ns_name))

    def _assert_extra_routes(self, router):
        routes = ip_lib.get_routing_table(namespace=router.ns_name)
        routes = [{'nexthop': route['nexthop'],
                   'destination': route['destination']} for route in routes]

        for extra_route in router.router['routes']:
            self.assertIn(extra_route, routes)

    def _assert_interfaces_deleted_from_ovs(self):
        def assert_ovs_bridge_empty(bridge_name):
            bridge = ovs_lib.OVSBridge(bridge_name)
            self.assertFalse(bridge.get_port_name_list())

        assert_ovs_bridge_empty(self.agent.conf.ovs_integration_bridge)
        assert_ovs_bridge_empty(self.agent.conf.external_network_bridge)

    def floating_ips_configured(self, router):
        floating_ips = router.router[l3_constants.FLOATINGIP_KEY]
        external_port = router.get_ex_gw_port()
        return len(floating_ips) and all(
            ip_lib.device_exists_with_ips_and_mac(
                router.get_external_device_name(external_port['id']),
                ['%s/32' % fip['floating_ip_address']],
                external_port['mac_address'],
                namespace=router.ns_name) for fip in floating_ips)

    def fail_ha_router(self, router):
        device_name = router.get_ha_device_name()
        ha_device = ip_lib.IPDevice(device_name, router.ns_name)
        ha_device.link.set_down()


class L3AgentTestCase(L3AgentTestFramework):

    def test_keepalived_state_change_notification(self):
        enqueue_mock = mock.patch.object(
            self.agent, 'enqueue_state_change').start()
        router_info = self.generate_router_info(enable_ha=True)
        router = self.manage_router(self.agent, router_info)
        utils.wait_until_true(lambda: router.ha_state == 'master')

        self.fail_ha_router(router)
        utils.wait_until_true(lambda: router.ha_state == 'backup')

        utils.wait_until_true(lambda: enqueue_mock.call_count == 3)
        calls = [args[0] for args in enqueue_mock.call_args_list]
        self.assertEqual((router.router_id, 'backup'), calls[0])
        self.assertEqual((router.router_id, 'master'), calls[1])
        self.assertEqual((router.router_id, 'backup'), calls[2])

    def _expected_rpc_report(self, expected):
        calls = (args[0][1] for args in
                 self.agent.plugin_rpc.update_ha_routers_states.call_args_list)

        # Get the last state reported for each router
        actual_router_states = {}
        for call in calls:
            for router_id, state in call.iteritems():
                actual_router_states[router_id] = state

        return actual_router_states == expected

    def test_keepalived_state_change_bulk_rpc(self):
        router_info = self.generate_router_info(enable_ha=True)
        router1 = self.manage_router(self.agent, router_info)
        self.fail_ha_router(router1)
        router_info = self.generate_router_info(enable_ha=True)
        router2 = self.manage_router(self.agent, router_info)

        utils.wait_until_true(lambda: router1.ha_state == 'backup')
        utils.wait_until_true(lambda: router2.ha_state == 'master')
        utils.wait_until_true(
            lambda: self._expected_rpc_report(
                {router1.router_id: 'standby', router2.router_id: 'active'}))

    def test_agent_notifications_for_router_events(self):
        """Test notifications for router create, update, and delete.

        Make sure that when the agent sends notifications of router events
        for router create, update, and delete, that the correct handler is
        called with the right resource, event, and router information.
        """
        event_handler = mock.Mock()
        registry.subscribe(event_handler,
                           resources.ROUTER, events.BEFORE_CREATE)
        registry.subscribe(event_handler,
                           resources.ROUTER, events.AFTER_CREATE)
        registry.subscribe(event_handler,
                           resources.ROUTER, events.BEFORE_UPDATE)
        registry.subscribe(event_handler,
                           resources.ROUTER, events.AFTER_UPDATE)
        registry.subscribe(event_handler,
                           resources.ROUTER, events.BEFORE_DELETE)
        registry.subscribe(event_handler,
                           resources.ROUTER, events.AFTER_DELETE)

        router_info = self.generate_router_info(enable_ha=False)
        router = self.manage_router(self.agent, router_info)
        self.agent._process_updated_router(router.router)
        self._delete_router(self.agent, router.router_id)

        expected_calls = [
            mock.call('router', 'before_create', self.agent, router=router),
            mock.call('router', 'after_create', self.agent, router=router),
            mock.call('router', 'before_update', self.agent, router=router),
            mock.call('router', 'after_update', self.agent, router=router),
            mock.call('router', 'before_delete', self.agent, router=router),
            mock.call('router', 'after_delete', self.agent, router=router)]
        event_handler.assert_has_calls(expected_calls)

    def test_legacy_router_lifecycle(self):
        self._router_lifecycle(enable_ha=False, dual_stack=True)

    def test_legacy_router_lifecycle_with_no_gateway_subnet(self):
        self._router_lifecycle(enable_ha=False, dual_stack=True,
                               v6_ext_gw_with_sub=False)

    def test_ha_router_lifecycle(self):
        self._router_lifecycle(enable_ha=True)

    def test_conntrack_disassociate_fip(self):
        '''Test that conntrack immediately drops stateful connection
           that uses floating IP once it's disassociated.
        '''
        router_info = self.generate_router_info(enable_ha=False)
        router = self.manage_router(self.agent, router_info)

        port = helpers.get_free_namespace_port(router.ns_name)
        client_address = '19.4.4.3'
        server_address = '35.4.0.4'

        def clean_fips(router):
            router.router[l3_constants.FLOATINGIP_KEY] = []

        clean_fips(router)
        self._add_fip(router, client_address, fixed_address=server_address)
        router.process(self.agent)

        router_ns = ip_lib.IPWrapper(namespace=router.ns_name)
        netcat = helpers.NetcatTester(router_ns, router_ns,
                                      server_address, port,
                                      client_address=client_address,
                                      run_as_root=True,
                                      udp=False)
        self.addCleanup(netcat.stop_processes)

        def assert_num_of_conntrack_rules(n):
            out = router_ns.netns.execute(["conntrack", "-L",
                                           "--orig-src", client_address])
            self.assertEqual(
                n, len([line for line in out.strip().split('\n') if line]))

        with self.assert_max_execution_time(100):
            assert_num_of_conntrack_rules(0)

            self.assertTrue(netcat.test_connectivity())
            assert_num_of_conntrack_rules(1)

            clean_fips(router)
            router.process(self.agent)
            assert_num_of_conntrack_rules(0)

            with testtools.ExpectedException(RuntimeError):
                netcat.test_connectivity()

    def test_ipv6_ha_router_lifecycle(self):
        self._router_lifecycle(enable_ha=True, ip_version=6)

    def test_keepalived_configuration(self):
        router_info = self.generate_router_info(enable_ha=True)
        router = self.manage_router(self.agent, router_info)
        expected = self.get_expected_keepalive_configuration(router)

        self.assertEqual(expected,
                         router.keepalived_manager.get_conf_on_disk())

        # Add a new FIP and change the GW IP address
        router.router = copy.deepcopy(router.router)
        existing_fip = '19.4.4.2'
        new_fip = '19.4.4.3'
        self._add_fip(router, new_fip)
        subnet_id = _uuid()
        fixed_ips = [{'ip_address': '19.4.4.10',
                      'prefixlen': 24,
                      'subnet_id': subnet_id}]
        subnets = [{'id': subnet_id,
                    'cidr': '19.4.4.0/24',
                    'gateway_ip': '19.4.4.5'}]
        router.router['gw_port']['subnets'] = subnets
        router.router['gw_port']['fixed_ips'] = fixed_ips

        router.process(self.agent)

        # Get the updated configuration and assert that both FIPs are in,
        # and that the GW IP address was updated.
        new_config = router.keepalived_manager.config.get_config_str()
        old_gw = '0.0.0.0/0 via 19.4.4.1'
        new_gw = '0.0.0.0/0 via 19.4.4.5'
        old_external_device_ip = '19.4.4.4'
        new_external_device_ip = '19.4.4.10'
        self.assertIn(existing_fip, new_config)
        self.assertIn(new_fip, new_config)
        self.assertNotIn(old_gw, new_config)
        self.assertIn(new_gw, new_config)
        external_port = router.get_ex_gw_port()
        external_device_name = router.get_external_device_name(
            external_port['id'])
        self.assertNotIn('%s/24 dev %s' %
                         (old_external_device_ip, external_device_name),
                         new_config)
        self.assertIn('%s/24 dev %s' %
                      (new_external_device_ip, external_device_name),
                      new_config)

    def test_periodic_sync_routers_task(self):
        routers_to_keep = []
        routers_to_delete = []
        ns_names_to_retrieve = set()
        for i in range(2):
            routers_to_keep.append(self.generate_router_info(False))
            self.manage_router(self.agent, routers_to_keep[i])
            ns_names_to_retrieve.add(namespaces.NS_PREFIX +
                                     routers_to_keep[i]['id'])
        for i in range(2):
            routers_to_delete.append(self.generate_router_info(False))
            self.manage_router(self.agent, routers_to_delete[i])
            ns_names_to_retrieve.add(namespaces.NS_PREFIX +
                                     routers_to_delete[i]['id'])

        # Mock the plugin RPC API to Simulate a situation where the agent
        # was handling the 4 routers created above, it went down and after
        # starting up again, two of the routers were deleted via the API
        mocked_get_routers = (
            neutron_l3_agent.L3PluginApi.return_value.get_routers)
        mocked_get_routers.return_value = routers_to_keep

        # Synchonize the agent with the plug-in
        with mock.patch.object(namespace_manager.NamespaceManager, 'list_all',
                               return_value=ns_names_to_retrieve):
            self.agent.periodic_sync_routers_task(self.agent.context)

        # Mock the plugin RPC API so a known external network id is returned
        # when the router updates are processed by the agent
        external_network_id = _uuid()
        mocked_get_external_network_id = (
            neutron_l3_agent.L3PluginApi.return_value.get_external_network_id)
        mocked_get_external_network_id.return_value = external_network_id

        # Plug external_gateway_info in the routers that are not going to be
        # deleted by the agent when it processes the updates. Otherwise,
        # _process_router_if_compatible in the agent fails
        for i in range(2):
            routers_to_keep[i]['external_gateway_info'] = {'network_id':
                                                           external_network_id}

        # Have the agent process the update from the plug-in and verify
        # expected behavior
        for _ in routers_to_keep + routers_to_delete:
            self.agent._process_router_update()

        for i in range(2):
            self.assertIn(routers_to_keep[i]['id'], self.agent.router_info)
            self.assertTrue(self._namespace_exists(namespaces.NS_PREFIX +
                                                   routers_to_keep[i]['id']))
        for i in range(2):
            self.assertNotIn(routers_to_delete[i]['id'],
                             self.agent.router_info)
            self.assertFalse(self._namespace_exists(
                namespaces.NS_PREFIX + routers_to_delete[i]['id']))

    def _router_lifecycle(self, enable_ha, ip_version=4,
                          dual_stack=False, v6_ext_gw_with_sub=True):
        router_info = self.generate_router_info(enable_ha, ip_version,
                                                dual_stack=dual_stack,
                                                v6_ext_gw_with_sub=(
                                                    v6_ext_gw_with_sub))
        router = self.manage_router(self.agent, router_info)

        # Add multiple-IPv6-prefix internal router port
        slaac = l3_constants.IPV6_SLAAC
        slaac_mode = {'ra_mode': slaac, 'address_mode': slaac}
        subnet_modes = [slaac_mode] * 2
        self._add_internal_interface_by_subnet(router.router, count=2,
                ip_version=6, ipv6_subnet_modes=subnet_modes)
        router.process(self.agent)

        if enable_ha:
            port = router.get_ex_gw_port()
            interface_name = router.get_external_device_name(port['id'])
            self._assert_no_ip_addresses_on_interface(router.ns_name,
                                                      interface_name)
            utils.wait_until_true(lambda: router.ha_state == 'master')

            # Keepalived notifies of a state transition when it starts,
            # not when it ends. Thus, we have to wait until keepalived finishes
            # configuring everything. We verify this by waiting until the last
            # device has an IP address.
            device = router.router[l3_constants.INTERFACE_KEY][-1]
            device_exists = functools.partial(
                self.device_exists_with_ips_and_mac,
                device,
                router.get_internal_device_name,
                router.ns_name)
            utils.wait_until_true(device_exists)

        self.assertTrue(self._namespace_exists(router.ns_name))
        utils.wait_until_true(
            lambda: self._metadata_proxy_exists(self.agent.conf, router))
        self._assert_internal_devices(router)
        self._assert_external_device(router)
        if not (enable_ha and (ip_version == 6 or dual_stack)):
            # Note(SridharG): enable the assert_gateway for IPv6 once
            # keepalived on Ubuntu14.04 (i.e., check-neutron-dsvm-functional
            # platform) is updated to 1.2.10 (or above).
            # For more details: https://review.openstack.org/#/c/151284/
            self._assert_gateway(router, v6_ext_gw_with_sub)
            self.assertTrue(self.floating_ips_configured(router))
            self._assert_snat_chains(router)
            self._assert_floating_ip_chains(router)
            self._assert_extra_routes(router)
        self._assert_metadata_chains(router)

        if enable_ha:
            self._assert_ha_device(router)
            self.assertTrue(router.keepalived_manager.get_process().active)

        self._delete_router(self.agent, router.router_id)

        self._assert_interfaces_deleted_from_ovs()
        self._assert_router_does_not_exist(router)
        if enable_ha:
            self.assertFalse(router.keepalived_manager.get_process().active)

    def _assert_external_device(self, router):
        external_port = router.get_ex_gw_port()
        self.assertTrue(self.device_exists_with_ips_and_mac(
            external_port, router.get_external_device_name,
            router.ns_name))

    def _assert_gateway(self, router, v6_ext_gw_with_sub=True):
        external_port = router.get_ex_gw_port()
        external_device_name = router.get_external_device_name(
            external_port['id'])
        external_device = ip_lib.IPDevice(external_device_name,
                                          namespace=router.ns_name)
        for subnet in external_port['subnets']:
            self._gateway_check(subnet['gateway_ip'], external_device)
        if not v6_ext_gw_with_sub:
            self._gateway_check(self.agent.conf.ipv6_gateway,
                                external_device)

    def _gateway_check(self, gateway_ip, external_device):
        expected_gateway = gateway_ip
        ip_vers = netaddr.IPAddress(expected_gateway).version
        existing_gateway = (external_device.route.get_gateway(
            ip_version=ip_vers).get('gateway'))
        self.assertEqual(expected_gateway, existing_gateway)

    def _assert_ha_device(self, router):
        def ha_router_dev_name_getter(not_used):
            return router.get_ha_device_name()
        self.assertTrue(self.device_exists_with_ips_and_mac(
            router.router[l3_constants.HA_INTERFACE_KEY],
            ha_router_dev_name_getter, router.ns_name))

    @classmethod
    def _get_addresses_on_device(cls, namespace, interface):
        return [address['cidr'] for address in
                ip_lib.IPDevice(interface, namespace=namespace).addr.list()]

    def _assert_no_ip_addresses_on_interface(self, namespace, interface):
        self.assertEqual(
            [], self._get_addresses_on_device(namespace, interface))

    def test_ha_router_conf_on_restarted_agent(self):
        router_info = self.generate_router_info(enable_ha=True)
        router1 = self.manage_router(self.agent, router_info)
        self._add_fip(router1, '192.168.111.12')
        restarted_agent = neutron_l3_agent.L3NATAgentWithStateReport(
            self.agent.host, self.agent.conf)
        self._create_router(restarted_agent, router1.router)
        utils.wait_until_true(lambda: self.floating_ips_configured(router1))
        self.assertIn(
            router1._get_primary_vip(),
            self._get_addresses_on_device(
                router1.ns_name,
                router1.get_ha_device_name()))

    def test_fip_connection_from_same_subnet(self):
        '''Test connection to floatingip which is associated with
           fixed_ip on the same subnet of the source fixed_ip.
           In other words it confirms that return packets surely
           go through the router.
        '''
        router_info = self.generate_router_info(enable_ha=False)
        router = self.manage_router(self.agent, router_info)
        router_ip_cidr = self._port_first_ip_cidr(router.internal_ports[0])
        router_ip = router_ip_cidr.partition('/')[0]

        src_ip_cidr = net_helpers.increment_ip_cidr(router_ip_cidr)
        dst_ip_cidr = net_helpers.increment_ip_cidr(src_ip_cidr)
        dst_ip = dst_ip_cidr.partition('/')[0]
        dst_fip = '19.4.4.10'
        router.router[l3_constants.FLOATINGIP_KEY] = []
        self._add_fip(router, dst_fip, fixed_address=dst_ip)
        router.process(self.agent)

        br_int = get_ovs_bridge(self.agent.conf.ovs_integration_bridge)

        # FIXME(cbrandily): temporary, will be replaced by fake machines
        src_ns = self._create_namespace(prefix='test-src-')
        src_port = self.useFixture(
            net_helpers.OVSPortFixture(br_int, src_ns.namespace)).port
        src_port.addr.add(src_ip_cidr)
        net_helpers.set_namespace_gateway(src_port, router_ip)
        dst_ns = self._create_namespace(prefix='test-dst-')
        dst_port = self.useFixture(
            net_helpers.OVSPortFixture(br_int, dst_ns.namespace)).port
        dst_port.addr.add(dst_ip_cidr)
        net_helpers.set_namespace_gateway(dst_port, router_ip)

        protocol_port = helpers.get_free_namespace_port(dst_ns)
        # client sends to fip
        netcat = helpers.NetcatTester(src_ns, dst_ns, dst_ip,
                                      protocol_port,
                                      client_address=dst_fip,
                                      run_as_root=True,
                                      udp=False)
        self.addCleanup(netcat.stop_processes)
        self.assertTrue(netcat.test_connectivity())


class L3HATestFramework(L3AgentTestFramework):

    NESTED_NAMESPACE_SEPARATOR = '@'

    def setUp(self):
        super(L3HATestFramework, self).setUp()
        self.failover_agent = self._configure_agent('agent2')

        br_int_1 = get_ovs_bridge(self.agent.conf.ovs_integration_bridge)
        br_int_2 = get_ovs_bridge(
            self.failover_agent.conf.ovs_integration_bridge)

        veth1, veth2 = self.create_veth()
        br_int_1.add_port(veth1.name)
        br_int_2.add_port(veth2.name)

    def test_ha_router_failover(self):
        router_info = self.generate_router_info(enable_ha=True)
        ns_name = "%s%s%s" % (
                namespaces.RouterNamespace._get_ns_name(router_info['id']),
                self.NESTED_NAMESPACE_SEPARATOR, self.agent.host)
        mock.patch.object(namespaces.RouterNamespace, '_get_ns_name',
                return_value=ns_name).start()
        router1 = self.manage_router(self.agent, router_info)

        router_info_2 = copy.deepcopy(router_info)
        router_info_2[l3_constants.HA_INTERFACE_KEY] = (
            test_l3_agent.get_ha_interface(ip='169.254.192.2',
                                           mac='22:22:22:22:22:22'))

        ns_name = "%s%s%s" % (
                namespaces.RouterNamespace._get_ns_name(router_info_2['id']),
                self.NESTED_NAMESPACE_SEPARATOR, self.failover_agent.host)
        mock.patch.object(namespaces.RouterNamespace, '_get_ns_name',
                return_value=ns_name).start()
        router2 = self.manage_router(self.failover_agent, router_info_2)

        utils.wait_until_true(lambda: router1.ha_state == 'master')
        utils.wait_until_true(lambda: router2.ha_state == 'backup')

        device_name = router1.get_ha_device_name()
        ha_device = ip_lib.IPDevice(device_name, namespace=router1.ns_name)
        ha_device.link.set_down()

        utils.wait_until_true(lambda: router2.ha_state == 'master')
        utils.wait_until_true(lambda: router1.ha_state == 'backup')


class MetadataFakeProxyHandler(object):

    def __init__(self, status):
        self.status = status

    @webob.dec.wsgify()
    def __call__(self, req):
        return webob.Response(status=self.status)


class MetadataL3AgentTestCase(L3AgentTestFramework):

    SOCKET_MODE = 0o644

    def _create_metadata_fake_server(self, status):
        server = utils.UnixDomainWSGIServer('metadata-fake-server')
        self.addCleanup(server.stop)

        # NOTE(cbrandily): TempDir fixture creates a folder with 0o700
        # permissions but metadata_proxy_socket folder must be readable by all
        # users
        self.useFixture(
            helpers.RecursivePermDirFixture(
                os.path.dirname(self.agent.conf.metadata_proxy_socket), 0o555))
        server.start(MetadataFakeProxyHandler(status),
                     self.agent.conf.metadata_proxy_socket,
                     workers=0, backlog=4096, mode=self.SOCKET_MODE)

    def test_access_to_metadata_proxy(self):
        """Test access to the l3-agent metadata proxy.

        The test creates:
         * A l3-agent metadata service:
           * A router (which creates a metadata proxy in the router namespace),
           * A fake metadata server
         * A "client" namespace (simulating a vm) with a port on router
           internal subnet.

        The test queries from the "client" namespace the metadata proxy on
        http://169.254.169.254 and asserts that the metadata proxy added
        the X-Forwarded-For and X-Neutron-Router-Id headers to the request
        and forwarded the http request to the fake metadata server and the
        response to the "client" namespace.
        """
        router_info = self.generate_router_info(enable_ha=False)
        router = self.manage_router(self.agent, router_info)
        self._create_metadata_fake_server(webob.exc.HTTPOk.code)

        # Create and configure client namespace
        client_ns = self._create_namespace()
        router_ip_cidr = self._port_first_ip_cidr(router.internal_ports[0])
        ip_cidr = net_helpers.increment_ip_cidr(router_ip_cidr)
        br_int = get_ovs_bridge(self.agent.conf.ovs_integration_bridge)

        # FIXME(cbrandily): temporary, will be replaced by a fake machine
        port = self.useFixture(
            net_helpers.OVSPortFixture(br_int, client_ns.namespace)).port
        port.addr.add(ip_cidr)
        net_helpers.set_namespace_gateway(port,
                                          router_ip_cidr.partition('/')[0])

        # Query metadata proxy
        url = 'http://%(host)s:%(port)s' % {'host': dhcp.METADATA_DEFAULT_IP,
                                            'port': dhcp.METADATA_PORT}
        cmd = 'curl', '--max-time', METADATA_REQUEST_TIMEOUT, '-D-', url
        try:
            raw_headers = client_ns.netns.execute(cmd)
        except RuntimeError:
            self.fail('metadata proxy unreachable on %s before timeout' % url)

        # Check status code
        firstline = raw_headers.splitlines()[0]
        self.assertIn(str(webob.exc.HTTPOk.code), firstline.split())


class UnprivilegedUserMetadataL3AgentTestCase(MetadataL3AgentTestCase):
    """Test metadata proxy with least privileged user.

    The least privileged user has uid=65534 and is commonly named 'nobody' but
    not always, that's why we use its uid.
    """

    SOCKET_MODE = 0o664

    def setUp(self):
        super(UnprivilegedUserMetadataL3AgentTestCase, self).setUp()
        self.agent.conf.set_override('metadata_proxy_user', '65534')
        self.agent.conf.set_override('metadata_proxy_watch_log', False)


class UnprivilegedUserGroupMetadataL3AgentTestCase(MetadataL3AgentTestCase):
    """Test metadata proxy with least privileged user/group.

    The least privileged user has uid=65534 and is commonly named 'nobody' but
    not always, that's why we use its uid.
    Its group has gid=65534 and is commonly named 'nobody' or 'nogroup', that's
    why we use its gid.
    """

    SOCKET_MODE = 0o666

    def setUp(self):
        super(UnprivilegedUserGroupMetadataL3AgentTestCase, self).setUp()
        self.agent.conf.set_override('metadata_proxy_user', '65534')
        self.agent.conf.set_override('metadata_proxy_group', '65534')
        self.agent.conf.set_override('metadata_proxy_watch_log', False)


class TestDvrRouter(L3AgentTestFramework):
    def test_dvr_router_lifecycle_without_ha_without_snat_with_fips(self):
        self._dvr_router_lifecycle(enable_ha=False, enable_snat=False)

    def test_dvr_router_lifecycle_without_ha_with_snat_with_fips(self):
        self._dvr_router_lifecycle(enable_ha=False, enable_snat=True)

    def _dvr_router_lifecycle(self, enable_ha=False, enable_snat=False,
                              custom_mtu=2000):
        '''Test dvr router lifecycle

        :param enable_ha: sets the ha value for the router.
        :param enable_snat:  the value of enable_snat is used
        to  set the  agent_mode.
        '''

        # The value of agent_mode can be dvr, dvr_snat, or legacy.
        # Since by definition this is a dvr (distributed = true)
        # only dvr and dvr_snat are applicable
        self.agent.conf.agent_mode = 'dvr_snat' if enable_snat else 'dvr'
        self.agent.conf.network_device_mtu = custom_mtu

        # We get the router info particular to a dvr router
        router_info = self.generate_dvr_router_info(
            enable_ha, enable_snat)

        # We need to mock the get_agent_gateway_port return value
        # because the whole L3PluginApi is mocked and we need the port
        # gateway_port information before the l3_agent will create it.
        # The port returned needs to have the same information as
        # router_info['gw_port']
        mocked_gw_port = (
            neutron_l3_agent.L3PluginApi.return_value.get_agent_gateway_port)
        mocked_gw_port.return_value = router_info['gw_port']

        # We also need to mock the get_external_network_id method to
        # get the correct fip namespace.
        mocked_ext_net_id = (
            neutron_l3_agent.L3PluginApi.return_value.get_external_network_id)
        mocked_ext_net_id.return_value = (
            router_info['_floatingips'][0]['floating_network_id'])

        # With all that set we can now ask the l3_agent to
        # manage the router (create it, create namespaces,
        # attach interfaces, etc...)
        router = self.manage_router(self.agent, router_info)

        self.assertTrue(self._namespace_exists(router.ns_name))
        self.assertTrue(self._metadata_proxy_exists(self.agent.conf, router))
        self._assert_internal_devices(router)
        self._assert_dvr_external_device(router)
        self._assert_dvr_gateway(router)
        self._assert_dvr_floating_ips(router)
        self._assert_snat_chains(router)
        self._assert_floating_ip_chains(router)
        self._assert_metadata_chains(router)
        self._assert_extra_routes(router)
        self._assert_rfp_fpr_mtu(router, custom_mtu)

        self._delete_router(self.agent, router.router_id)
        self._assert_interfaces_deleted_from_ovs()
        self._assert_router_does_not_exist(router)

    def generate_dvr_router_info(self, enable_ha=False, enable_snat=False):
        router = test_l3_agent.prepare_router_data(
            enable_snat=enable_snat,
            enable_floating_ip=True,
            enable_ha=enable_ha)
        internal_ports = router.get(l3_constants.INTERFACE_KEY, [])
        router['distributed'] = True
        router['gw_port_host'] = self.agent.conf.host
        router['gw_port']['binding:host_id'] = self.agent.conf.host
        floating_ip = router['_floatingips'][0]
        floating_ip['floating_network_id'] = router['gw_port']['network_id']
        floating_ip['host'] = self.agent.conf.host
        floating_ip['port_id'] = internal_ports[0]['id']
        floating_ip['status'] = 'ACTIVE'

        self._add_snat_port_info_to_router(router, internal_ports)
        # FIP has a dependency on external gateway. So we need to create
        # the snat_port info and fip_agent_gw_port_info irrespective of
        # the agent type the dvr supports. The namespace creation is
        # dependent on the agent_type.
        external_gw_port = router['gw_port']
        self._add_fip_agent_gw_port_info_to_router(router, external_gw_port)
        return router

    def _add_fip_agent_gw_port_info_to_router(self, router, external_gw_port):
        # Add fip agent gateway port information to the router_info
        fip_gw_port_list = router.get(
            l3_constants.FLOATINGIP_AGENT_INTF_KEY, [])
        if not fip_gw_port_list and external_gw_port:
            # Get values from external gateway port
            fixed_ip = external_gw_port['fixed_ips'][0]
            float_subnet = external_gw_port['subnets'][0]
            port_ip = fixed_ip['ip_address']
            # Pick an ip address which is not the same as port_ip
            fip_gw_port_ip = str(netaddr.IPAddress(port_ip) + 5)
            # Add floatingip agent gateway port info to router
            prefixlen = netaddr.IPNetwork(float_subnet['cidr']).prefixlen
            router[l3_constants.FLOATINGIP_AGENT_INTF_KEY] = [
                {'subnets': [
                    {'cidr': float_subnet['cidr'],
                     'gateway_ip': float_subnet['gateway_ip'],
                     'id': fixed_ip['subnet_id']}],
                 'network_id': external_gw_port['network_id'],
                 'device_owner': 'network:floatingip_agent_gateway',
                 'mac_address': 'fa:16:3e:80:8d:89',
                 'binding:host_id': self.agent.conf.host,
                 'fixed_ips': [{'subnet_id': fixed_ip['subnet_id'],
                                'ip_address': fip_gw_port_ip,
                                'prefixlen': prefixlen}],
                 'id': _uuid(),
                 'device_id': _uuid()}
            ]

    def _add_snat_port_info_to_router(self, router, internal_ports):
        # Add snat port information to the router
        snat_port_list = router.get(l3_constants.SNAT_ROUTER_INTF_KEY, [])
        if not snat_port_list and internal_ports:
            # Get values from internal port
            port = internal_ports[0]
            fixed_ip = port['fixed_ips'][0]
            snat_subnet = port['subnets'][0]
            port_ip = fixed_ip['ip_address']
            # Pick an ip address which is not the same as port_ip
            snat_ip = str(netaddr.IPAddress(port_ip) + 5)
            # Add the info to router as the first snat port
            # in the list of snat ports
            prefixlen = netaddr.IPNetwork(snat_subnet['cidr']).prefixlen
            router[l3_constants.SNAT_ROUTER_INTF_KEY] = [
                {'subnets': [
                    {'cidr': snat_subnet['cidr'],
                     'gateway_ip': snat_subnet['gateway_ip'],
                     'id': fixed_ip['subnet_id']}],
                 'network_id': port['network_id'],
                 'device_owner': 'network:router_centralized_snat',
                 'mac_address': 'fa:16:3e:80:8d:89',
                 'fixed_ips': [{'subnet_id': fixed_ip['subnet_id'],
                                'ip_address': snat_ip,
                                'prefixlen': prefixlen}],
                 'id': _uuid(),
                 'device_id': _uuid()}
            ]

    def _assert_dvr_external_device(self, router):
        external_port = router.get_ex_gw_port()
        snat_ns_name = dvr_snat_ns.SnatNamespace.get_snat_ns_name(
            router.router_id)

        # if the agent is in dvr_snat mode, then we have to check
        # that the correct ports and ip addresses exist in the
        # snat_ns_name namespace
        if self.agent.conf.agent_mode == 'dvr_snat':
            self.assertTrue(self.device_exists_with_ips_and_mac(
                external_port, router.get_external_device_name,
                snat_ns_name))
        # if the agent is in dvr mode then the snat_ns_name namespace
        # should not be present at all:
        elif self.agent.conf.agent_mode == 'dvr':
            self.assertFalse(
                self._namespace_exists(snat_ns_name),
                "namespace %s was found but agent is in dvr mode not dvr_snat"
                % (str(snat_ns_name))
            )
        # if the agent is anything else the test is misconfigured
        # we force a test failure with message
        else:
            self.assertTrue(False, " agent not configured for dvr or dvr_snat")

    def _assert_dvr_gateway(self, router):
        gateway_expected_in_snat_namespace = (
            self.agent.conf.agent_mode == 'dvr_snat'
        )
        if gateway_expected_in_snat_namespace:
            self._assert_dvr_snat_gateway(router)

        snat_namespace_should_not_exist = (
            self.agent.conf.agent_mode == 'dvr'
        )
        if snat_namespace_should_not_exist:
            self._assert_snat_namespace_does_not_exist(router)

    def _assert_dvr_snat_gateway(self, router):
        namespace = dvr_snat_ns.SnatNamespace.get_snat_ns_name(
            router.router_id)
        external_port = router.get_ex_gw_port()
        external_device_name = router.get_external_device_name(
            external_port['id'])
        external_device = ip_lib.IPDevice(external_device_name,
                                          namespace=namespace)
        existing_gateway = (
            external_device.route.get_gateway().get('gateway'))
        expected_gateway = external_port['subnets'][0]['gateway_ip']
        self.assertEqual(expected_gateway, existing_gateway)

    def _assert_snat_namespace_does_not_exist(self, router):
        namespace = dvr_snat_ns.SnatNamespace.get_snat_ns_name(
            router.router_id)
        self.assertFalse(self._namespace_exists(namespace))

    def _assert_dvr_floating_ips(self, router):
        # in the fip namespace:
        # Check that the fg-<port-id> (floatingip_agent_gateway)
        # is created with the ip address of the external gateway port
        floating_ips = router.router[l3_constants.FLOATINGIP_KEY]
        self.assertTrue(floating_ips)
        # We need to fetch the floatingip agent gateway port info
        # from the router_info
        floating_agent_gw_port = (
            router.router[l3_constants.FLOATINGIP_AGENT_INTF_KEY])
        self.assertTrue(floating_agent_gw_port)

        external_gw_port = floating_agent_gw_port[0]
        fip_ns = self.agent.get_fip_ns(floating_ips[0]['floating_network_id'])
        fip_ns_name = fip_ns.get_name()
        fg_port_created_successfully = ip_lib.device_exists_with_ips_and_mac(
            fip_ns.get_ext_device_name(external_gw_port['id']),
            [self._port_first_ip_cidr(external_gw_port)],
            external_gw_port['mac_address'],
            namespace=fip_ns_name)
        self.assertTrue(fg_port_created_successfully)
        # Check fpr-router device has been created
        device_name = fip_ns.get_int_device_name(router.router_id)
        fpr_router_device_created_successfully = ip_lib.device_exists(
            device_name, namespace=fip_ns_name)
        self.assertTrue(fpr_router_device_created_successfully)

        # In the router namespace
        # Check rfp-<router-id> is created correctly
        for fip in floating_ips:
            device_name = fip_ns.get_rtr_ext_device_name(router.router_id)
            self.assertTrue(ip_lib.device_exists(
                device_name, namespace=router.ns_name))

    def test_dvr_router_rem_fips_on_restarted_agent(self):
        self.agent.conf.agent_mode = 'dvr_snat'
        router_info = self.generate_dvr_router_info()
        router1 = self._create_router(self.agent, router_info)
        self._add_fip(router1, '192.168.111.12', self.agent.conf.host)
        fip_ns = router1.fip_ns.get_name()
        restarted_agent = neutron_l3_agent.L3NATAgentWithStateReport(
            self.agent.host, self.agent.conf)
        router1.router[l3_constants.FLOATINGIP_KEY] = []
        self._create_router(restarted_agent, router1.router)
        self._assert_dvr_snat_gateway(router1)
        self.assertFalse(self._namespace_exists(fip_ns))

    def test_dvr_router_add_internal_network_set_arp_cache(self):
        # Check that, when the router is set up and there are
        # existing ports on the the uplinked subnet, the ARP
        # cache is properly populated.
        self.agent.conf.agent_mode = 'dvr_snat'
        router_info = test_l3_agent.prepare_router_data()
        router_info['distributed'] = True
        expected_neighbor = '35.4.1.10'
        port_data = {
            'fixed_ips': [{'ip_address': expected_neighbor}],
            'mac_address': 'fa:3e:aa:bb:cc:dd',
            'device_owner': 'compute:None'
        }
        self.agent.plugin_rpc.get_ports_by_subnet.return_value = [port_data]
        router1 = self._create_router(self.agent, router_info)
        internal_device = router1.get_internal_device_name(
            router_info['_interfaces'][0]['id'])
        neighbors = ip_lib.IPDevice(internal_device, router1.ns_name).neigh
        self.assertEqual(expected_neighbor, neighbors.show().split()[0])

    def _assert_rfp_fpr_mtu(self, router, expected_mtu=1500):
        dev_mtu = self.get_device_mtu(
            router.router_id, router.fip_ns.get_rtr_ext_device_name,
            router.ns_name)
        self.assertEqual(expected_mtu, dev_mtu)
        dev_mtu = self.get_device_mtu(
            router.router_id, router.fip_ns.get_int_device_name,
            router.fip_ns.get_name())
        self.assertEqual(expected_mtu, dev_mtu)
