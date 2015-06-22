# Copyright (c) 2015 Thales Services SAS
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
#

import abc

import fixtures
import netaddr
import six

from neutron.agent.linux import bridge_lib
from neutron.agent.linux import ip_lib
from neutron.agent.linux import ovs_lib
from neutron.common import constants as n_const
from neutron.openstack.common import uuidutils
from neutron.tests import base as tests_base
from neutron.tests.common import base as common_base
from neutron.tests import tools

NS_PREFIX = 'func-'
BR_PREFIX = 'test-br'
PORT_PREFIX = 'test-port'
VETH0_PREFIX = 'test-veth0'
VETH1_PREFIX = 'test-veth1'


def get_rand_port_name():
    return tests_base.get_rand_name(max_length=n_const.DEVICE_NAME_MAX_LEN,
                                    prefix=PORT_PREFIX)


def increment_ip_cidr(ip_cidr, offset=1):
    """Increment ip_cidr offset times.

    example: increment_ip_cidr("1.2.3.4/24", 2) ==> "1.2.3.6/24"
    """
    net0 = netaddr.IPNetwork(ip_cidr)
    net = netaddr.IPNetwork(ip_cidr)
    net.value += offset
    if not net0.network < net.ip < net0.broadcast:
        tools.fail(
            'Incorrect ip_cidr,offset tuple (%s,%s): "incremented" ip_cidr is '
            'outside ip_cidr' % (ip_cidr, offset))
    return str(net)


def set_namespace_gateway(port_dev, gateway_ip):
    """Set gateway for the namespace associated to the port."""
    if not port_dev.namespace:
        tools.fail('tests should not change test machine gateway')
    port_dev.route.add_gateway(gateway_ip)


class NamespaceFixture(fixtures.Fixture):
    """Create a namespace.

    :ivar ip_wrapper: created namespace
    :type ip_wrapper: IPWrapper
    :ivar name: created namespace name
    :type name: str
    """

    def __init__(self, prefix=NS_PREFIX):
        super(NamespaceFixture, self).__init__()
        self.prefix = prefix

    def setUp(self):
        super(NamespaceFixture, self).setUp()
        ip = ip_lib.IPWrapper()
        self.name = self.prefix + uuidutils.generate_uuid()
        self.ip_wrapper = ip.ensure_namespace(self.name)
        self.addCleanup(self.destroy)

    def destroy(self):
        if self.ip_wrapper.netns.exists(self.name):
            self.ip_wrapper.netns.delete(self.name)


class VethFixture(fixtures.Fixture):
    """Create a veth.

    :ivar ports: created veth ports
    :type ports: IPDevice 2-uplet
    """

    def setUp(self):
        super(VethFixture, self).setUp()
        ip_wrapper = ip_lib.IPWrapper()

        def _create_veth(name0):
            name1 = name0.replace(VETH0_PREFIX, VETH1_PREFIX)
            return ip_wrapper.add_veth(name0, name1)

        self.ports = common_base.create_resource(VETH0_PREFIX, _create_veth)
        self.addCleanup(self.destroy)

    def destroy(self):
        for port in self.ports:
            ip_wrapper = ip_lib.IPWrapper(port.namespace)
            try:
                ip_wrapper.del_veth(port.name)
                break
            except RuntimeError:
                # NOTE(cbrandily): It seems a veth is automagically deleted
                # when a namespace owning a veth endpoint is deleted.
                pass


@six.add_metaclass(abc.ABCMeta)
class PortFixture(fixtures.Fixture):
    """Create a port.

    :ivar port: created port
    :type port: IPDevice
    :ivar bridge: port bridge
    """

    def __init__(self, bridge=None, namespace=None):
        self.bridge = bridge
        self.namespace = namespace

    @abc.abstractmethod
    def _create_bridge_fixture(self):
        pass

    @abc.abstractmethod
    def setUp(self):
        super(PortFixture, self).setUp()
        if not self.bridge:
            self.bridge = self.useFixture(self._create_bridge_fixture()).bridge


class OVSBridgeFixture(fixtures.Fixture):
    """Create an OVS bridge.

    :ivar bridge: created bridge
    :type bridge: OVSBridge
    """

    def setUp(self):
        super(OVSBridgeFixture, self).setUp()
        ovs = ovs_lib.BaseOVS()
        self.bridge = common_base.create_resource(BR_PREFIX, ovs.add_bridge)
        self.addCleanup(self.bridge.destroy)


class OVSPortFixture(PortFixture):

    def _create_bridge_fixture(self):
        return OVSBridgeFixture()

    def setUp(self):
        super(OVSPortFixture, self).setUp()

        port_name = common_base.create_resource(PORT_PREFIX, self.create_port)
        self.addCleanup(self.bridge.delete_port, port_name)
        self.port = ip_lib.IPDevice(port_name)

        ns_ip_wrapper = ip_lib.IPWrapper(self.namespace)
        ns_ip_wrapper.add_device_to_namespace(self.port)
        self.port.link.set_up()

    def create_port(self, name):
        self.bridge.add_port(name, ('type', 'internal'))
        return name


class LinuxBridgeFixture(fixtures.Fixture):
    """Create a linux bridge.

    :ivar bridge: created bridge
    :type bridge: BridgeDevice
    :ivar namespace: created bridge namespace
    :type namespace: str
    """

    def setUp(self):
        super(LinuxBridgeFixture, self).setUp()

        self.namespace = self.useFixture(NamespaceFixture()).name
        self.bridge = common_base.create_resource(
            BR_PREFIX,
            bridge_lib.BridgeDevice.addbr,
            namespace=self.namespace)
        self.addCleanup(self.bridge.delbr)
        self.bridge.link.set_up()
        self.addCleanup(self.bridge.link.set_down)


class LinuxBridgePortFixture(PortFixture):
    """Create a linux bridge port.

    :ivar port: created port
    :type port: IPDevice
    :ivar br_port: bridge side veth peer port
    :type br_port: IPDevice
    """

    def _create_bridge_fixture(self):
        return LinuxBridgeFixture()

    def setUp(self):
        super(LinuxBridgePortFixture, self).setUp()
        self.port, self.br_port = self.useFixture(VethFixture()).ports

        # bridge side
        br_ip_wrapper = ip_lib.IPWrapper(self.bridge.namespace)
        br_ip_wrapper.add_device_to_namespace(self.br_port)
        self.bridge.addif(self.br_port)
        self.br_port.link.set_up()

        # port side
        ns_ip_wrapper = ip_lib.IPWrapper(self.namespace)
        ns_ip_wrapper.add_device_to_namespace(self.port)
        self.port.link.set_up()


class VethBridge(object):

    def __init__(self, ports):
        self.ports = ports
        self.unallocated_ports = set(self.ports)

    def allocate_port(self):
        try:
            return self.unallocated_ports.pop()
        except KeyError:
            tools.fail('All FakeBridge ports (%s) are already allocated.' %
                       len(self.ports))


class VethBridgeFixture(fixtures.Fixture):
    """Simulate a bridge with a veth.

    :ivar bridge: created bridge
    :type bridge: FakeBridge
    """

    def setUp(self):
        super(VethBridgeFixture, self).setUp()
        ports = self.useFixture(VethFixture()).ports
        self.bridge = VethBridge(ports)


class VethPortFixture(PortFixture):
    """Create a veth bridge port.

    :ivar port: created port
    :type port: IPDevice
    """

    def _create_bridge_fixture(self):
        return VethBridgeFixture()

    def setUp(self):
        super(VethPortFixture, self).setUp()
        self.port = self.bridge.allocate_port()

        ns_ip_wrapper = ip_lib.IPWrapper(self.namespace)
        ns_ip_wrapper.add_device_to_namespace(self.port)
        self.port.link.set_up()
