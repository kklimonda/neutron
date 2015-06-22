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

import netaddr

from neutron.common import constants
from neutron import ipam
from neutron.openstack.common import uuidutils
from neutron.tests import base


class IpamSubnetRequestTestCase(base.BaseTestCase):

    def setUp(self):
        super(IpamSubnetRequestTestCase, self).setUp()
        self.tenant_id = uuidutils.generate_uuid()
        self.subnet_id = uuidutils.generate_uuid()


class TestIpamSubnetRequests(IpamSubnetRequestTestCase):

    def test_subnet_request(self):
        pool = ipam.SubnetRequest(self.tenant_id,
                                  self.subnet_id)
        self.assertEqual(self.tenant_id, pool.tenant_id)
        self.assertEqual(self.subnet_id, pool.subnet_id)
        self.assertEqual(None, pool.gateway_ip)
        self.assertEqual(None, pool.allocation_pools)

    def test_subnet_request_gateway(self):
        request = ipam.SubnetRequest(self.tenant_id,
                                     self.subnet_id,
                                     gateway_ip='1.2.3.1')
        self.assertEqual('1.2.3.1', str(request.gateway_ip))

    def test_subnet_request_bad_gateway(self):
        self.assertRaises(netaddr.core.AddrFormatError,
                          ipam.SubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          gateway_ip='1.2.3.')

    def test_subnet_request_with_range(self):
        allocation_pools = [netaddr.IPRange('1.2.3.4', '1.2.3.5'),
                            netaddr.IPRange('1.2.3.7', '1.2.3.9')]
        request = ipam.SubnetRequest(self.tenant_id,
                                     self.subnet_id,
                                     allocation_pools=allocation_pools)
        self.assertEqual(allocation_pools, request.allocation_pools)

    def test_subnet_request_range_not_list(self):
        self.assertRaises(TypeError,
                          ipam.SubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          allocation_pools=1)

    def test_subnet_request_bad_range(self):
        self.assertRaises(TypeError,
                          ipam.SubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          allocation_pools=['1.2.3.4'])

    def test_subnet_request_different_versions(self):
        pools = [netaddr.IPRange('0.0.0.1', '0.0.0.2'),
                 netaddr.IPRange('::1', '::2')]
        self.assertRaises(ValueError,
                          ipam.SubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          allocation_pools=pools)

    def test_subnet_request_overlap(self):
        pools = [netaddr.IPRange('0.0.0.10', '0.0.0.20'),
                 netaddr.IPRange('0.0.0.8', '0.0.0.10')]
        self.assertRaises(ValueError,
                          ipam.SubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          allocation_pools=pools)


class TestIpamAnySubnetRequest(IpamSubnetRequestTestCase):

    def test_subnet_request(self):
        request = ipam.AnySubnetRequest(self.tenant_id,
                                        self.subnet_id,
                                        constants.IPv4,
                                        24,
                                        gateway_ip='0.0.0.1')
        self.assertEqual(24, request.prefixlen)

    def test_subnet_request_bad_prefix_type(self):
        self.assertRaises(netaddr.core.AddrFormatError,
                          ipam.AnySubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          constants.IPv4,
                          'A')

    def test_subnet_request_bad_prefix(self):
        self.assertRaises(netaddr.core.AddrFormatError,
                          ipam.AnySubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          constants.IPv4,
                          33)
        self.assertRaises(netaddr.core.AddrFormatError,
                          ipam.AnySubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          constants.IPv6,
                          129)

    def test_subnet_request_bad_gateway(self):
        self.assertRaises(ValueError,
                          ipam.AnySubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          constants.IPv6,
                          64,
                          gateway_ip='2000::1')

    def test_subnet_request_allocation_pool_wrong_version(self):
        pools = [netaddr.IPRange('0.0.0.4', '0.0.0.5')]
        self.assertRaises(ValueError,
                          ipam.AnySubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          constants.IPv6,
                          64,
                          allocation_pools=pools)

    def test_subnet_request_allocation_pool_not_in_net(self):
        pools = [netaddr.IPRange('0.0.0.64', '0.0.0.128')]
        self.assertRaises(ValueError,
                          ipam.AnySubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          constants.IPv4,
                          25,
                          allocation_pools=pools)


class TestIpamSpecificSubnetRequest(IpamSubnetRequestTestCase):

    def test_subnet_request(self):
        request = ipam.SpecificSubnetRequest(self.tenant_id,
                                             self.subnet_id,
                                             '1.2.3.0/24',
                                             gateway_ip='1.2.3.1')
        self.assertEqual(24, request.prefixlen)
        self.assertEqual(netaddr.IPAddress('1.2.3.1'), request.gateway_ip)
        self.assertEqual(netaddr.IPNetwork('1.2.3.0/24'), request.subnet)

    def test_subnet_request_bad_gateway(self):
        self.assertRaises(ValueError,
                          ipam.SpecificSubnetRequest,
                          self.tenant_id,
                          self.subnet_id,
                          '2001::1',
                          gateway_ip='2000::1')


class TestAddressRequest(base.BaseTestCase):

    # This class doesn't test much.  At least running through all of the
    # constructors may shake out some trivial bugs.
    def test_specific_address_ipv6(self):
        request = ipam.SpecificAddressRequest('2000::45')
        self.assertEqual(netaddr.IPAddress('2000::45'), request.address)

    def test_specific_address_ipv4(self):
        request = ipam.SpecificAddressRequest('1.2.3.32')
        self.assertEqual(netaddr.IPAddress('1.2.3.32'), request.address)

    def test_any_address(self):
        ipam.AnyAddressRequest()
