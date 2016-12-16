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

from neutron.objects import base as obj_base
from neutron.objects import network
from neutron.objects.qos import policy
from neutron.tests.unit.objects import test_base as obj_test_base
from neutron.tests.unit import testlib_api


class NetworkPortSecurityIfaceObjTestCase(
        obj_test_base.BaseObjectIfaceTestCase):
    _test_class = network.NetworkPortSecurity


class NetworkPortSecurityDbObjTestCase(obj_test_base.BaseDbObjectTestCase,
                                       testlib_api.SqlTestCase):
    _test_class = network.NetworkPortSecurity

    def setUp(self):
        super(NetworkPortSecurityDbObjTestCase, self).setUp()
        self.update_obj_fields({'id': lambda: self._create_network().id})


class NetworkSegmentIfaceObjTestCase(obj_test_base.BaseObjectIfaceTestCase):
    _test_class = network.NetworkSegment

    def setUp(self):
        super(NetworkSegmentIfaceObjTestCase, self).setUp()
        # TODO(ihrachys): we should not need to duplicate that in every single
        # place, instead we should move the default pager into the base class
        # attribute and pull it from there for testing matters. Leaving it for
        # a follow up.
        self.pager_map[self._test_class.obj_name()] = (
            obj_base.Pager(
                sorts=[('network_id', True), ('segment_index', True)]))


class NetworkSegmentDbObjTestCase(obj_test_base.BaseDbObjectTestCase,
                                  testlib_api.SqlTestCase):
    _test_class = network.NetworkSegment

    def setUp(self):
        super(NetworkSegmentDbObjTestCase, self).setUp()
        network = self._create_network()
        self.update_obj_fields({'network_id': network.id})


class NetworkObjectIfaceTestCase(obj_test_base.BaseObjectIfaceTestCase):
    _test_class = network.Network

    def setUp(self):
        super(NetworkObjectIfaceTestCase, self).setUp()
        self.pager_map[network.NetworkSegment.obj_name()] = (
            obj_base.Pager(
                sorts=[('network_id', True), ('segment_index', True)]))


class NetworkDbObjectTestCase(obj_test_base.BaseDbObjectTestCase,
                              testlib_api.SqlTestCase):
    _test_class = network.Network

    def test_qos_policy_id(self):
        policy_obj = policy.QosPolicy(self.context)
        policy_obj.create()

        obj = self._make_object(self.obj_fields[0])
        obj.qos_policy_id = policy_obj.id
        obj.create()

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual(policy_obj.id, obj.qos_policy_id)

        policy_obj2 = policy.QosPolicy(self.context)
        policy_obj2.create()

        obj.qos_policy_id = policy_obj2.id
        obj.update()

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual(policy_obj2.id, obj.qos_policy_id)

        obj.qos_policy_id = None
        obj.update()

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertIsNone(obj.qos_policy_id)

    def test__attach_qos_policy(self):
        obj = self._make_object(self.obj_fields[0])
        obj.create()

        policy_obj = policy.QosPolicy(self.context)
        policy_obj.create()
        obj._attach_qos_policy(policy_obj.id)

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual(policy_obj.id, obj.qos_policy_id)

        policy_obj2 = policy.QosPolicy(self.context)
        policy_obj2.create()
        obj._attach_qos_policy(policy_obj2.id)

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual(policy_obj2.id, obj.qos_policy_id)

    def test_dns_domain(self):
        obj = self._make_object(self.obj_fields[0])
        obj.dns_domain = 'foo.com'
        obj.create()

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual('foo.com', obj.dns_domain)

        obj.dns_domain = 'bar.com'
        obj.update()

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual('bar.com', obj.dns_domain)

        obj.dns_domain = None
        obj.update()

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertIsNone(obj.dns_domain)

    def test__set_dns_domain(self):
        obj = self._make_object(self.obj_fields[0])
        obj.create()

        obj._set_dns_domain('foo.com')

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual('foo.com', obj.dns_domain)

        obj._set_dns_domain('bar.com')

        obj = network.Network.get_object(self.context, id=obj.id)
        self.assertEqual('bar.com', obj.dns_domain)


class SegmentHostMappingIfaceObjectTestCase(
    obj_test_base.BaseObjectIfaceTestCase):

    _test_class = network.SegmentHostMapping


class SegmentHostMappingDbObjectTestCase(obj_test_base.BaseDbObjectTestCase,
                                         testlib_api.SqlTestCase):

    _test_class = network.SegmentHostMapping

    def setUp(self):
        super(SegmentHostMappingDbObjectTestCase, self).setUp()
        self._create_test_network()
        self._create_test_segment(network=self._network)
        self.update_obj_fields({'segment_id': self._segment['id']})


class NetworkDNSDomainIfaceObjectTestcase(
        obj_test_base.BaseObjectIfaceTestCase):

    _test_class = network.NetworkDNSDomain


class NetworkDNSDomainDbObjectTestcase(obj_test_base.BaseDbObjectTestCase,
                                       testlib_api.SqlTestCase):

    _test_class = network.NetworkDNSDomain

    def setUp(self):
        super(NetworkDNSDomainDbObjectTestcase, self).setUp()
        self.update_obj_fields(
            {'network_id': lambda: self._create_network().id})
