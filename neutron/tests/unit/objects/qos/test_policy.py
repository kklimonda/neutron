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
from oslo_versionedobjects import exception
import testtools

from neutron.common import exceptions as n_exc
from neutron.db import models_v2
from neutron.objects.db import api as db_api
from neutron.objects import network as net_obj
from neutron.objects.qos import policy
from neutron.objects.qos import rule
from neutron.services.qos import qos_consts
from neutron.tests.unit.objects import test_base
from neutron.tests.unit import testlib_api


RULE_OBJ_CLS = {
    qos_consts.RULE_TYPE_BANDWIDTH_LIMIT: rule.QosBandwidthLimitRule,
    qos_consts.RULE_TYPE_DSCP_MARKING: rule.QosDscpMarkingRule,
    qos_consts.RULE_TYPE_MINIMUM_BANDWIDTH: rule.QosMinimumBandwidthRule,
}


class QosPolicyObjectTestCase(test_base.BaseObjectIfaceTestCase):

    _test_class = policy.QosPolicy

    def setUp(self):
        super(QosPolicyObjectTestCase, self).setUp()
        # qos_policy_ids will be incorrect, but we don't care in this test
        self.db_qos_bandwidth_rules = [
            self.get_random_db_fields(rule.QosBandwidthLimitRule)
            for _ in range(3)]

        self.db_qos_dscp_rules = [
            self.get_random_db_fields(rule.QosDscpMarkingRule)
            for _ in range(3)]

        self.db_qos_minimum_bandwidth_rules = [
            self.get_random_db_fields(rule.QosMinimumBandwidthRule)
            for _ in range(3)]

        self.model_map.update({
            self._test_class.db_model: self.db_objs,
            self._test_class.port_binding_model: [],
            self._test_class.network_binding_model: [],
            rule.QosBandwidthLimitRule.db_model: self.db_qos_bandwidth_rules,
            rule.QosDscpMarkingRule.db_model: self.db_qos_dscp_rules,
            rule.QosMinimumBandwidthRule.db_model:
                self.db_qos_minimum_bandwidth_rules})

    # TODO(ihrachys): stop overriding those test cases, instead base test cases
    # should be expanded if there are missing bits there to support QoS objects
    def test_get_objects(self):
        admin_context = self.context.elevated()
        with mock.patch.object(self.context, 'elevated',
                               return_value=admin_context) as context_mock:
            objs = self._test_class.get_objects(self.context)
        context_mock.assert_called_once_with()
        self.get_objects_mock.assert_any_call(
            admin_context, self._test_class.db_model, _pager=None)
        self.assertItemsEqual(
            [test_base.get_obj_persistent_fields(obj) for obj in self.objs],
            [test_base.get_obj_persistent_fields(obj) for obj in objs])

    def test_get_objects_valid_fields(self):
        admin_context = self.context.elevated()

        with mock.patch.object(
            db_api, 'get_objects',
            return_value=[self.db_objs[0]]) as get_objects_mock:

            with mock.patch.object(
                self.context,
                'elevated',
                return_value=admin_context) as context_mock:

                objs = self._test_class.get_objects(
                    self.context,
                    **self.valid_field_filter)
                context_mock.assert_called_once_with()
            get_objects_mock.assert_any_call(
                admin_context, self._test_class.db_model, _pager=None,
                **self.valid_field_filter)
        self._check_equal(self.objs[0], objs[0])

    def test_get_object(self):
        admin_context = self.context.elevated()
        with mock.patch.object(
                db_api, 'get_object',
                return_value=self.db_objs[0]) as get_object_mock:
            with mock.patch.object(self.context,
                                   'elevated',
                                   return_value=admin_context) as context_mock:
                obj = self._test_class.get_object(self.context, id='fake_id')
                self.assertTrue(self._is_test_class(obj))
                self._check_equal(self.objs[0], obj)
                context_mock.assert_called_once_with()
                get_object_mock.assert_called_once_with(
                    admin_context, self._test_class.db_model, id='fake_id')

    def test_to_dict_makes_primitive_field_value(self):
        # is_shared_with_tenant requires DB
        with mock.patch.object(self._test_class, 'is_shared_with_tenant',
                               return_value=False):
            (super(QosPolicyObjectTestCase, self).
             test_to_dict_makes_primitive_field_value())


class QosPolicyDbObjectTestCase(test_base.BaseDbObjectTestCase,
                                testlib_api.SqlTestCase):

    _test_class = policy.QosPolicy

    def setUp(self):
        super(QosPolicyDbObjectTestCase, self).setUp()
        self._create_test_network()
        self._create_test_port(self._network)

    def _create_test_policy(self):
        self.objs[0].create()
        return self.objs[0]

    def _create_test_policy_with_rules(self, rule_type, reload_rules=False):
        policy_obj = self._create_test_policy()
        rules = []
        for obj_cls in (RULE_OBJ_CLS.get(rule_type)
                        for rule_type in rule_type):
            rule_fields = self.get_random_object_fields(obj_cls=obj_cls)
            rule_fields['qos_policy_id'] = policy_obj.id
            rule_obj = obj_cls(self.context, **rule_fields)
            rule_obj.create()
            rules.append(rule_obj)

        if reload_rules:
            policy_obj.reload_rules()
        return policy_obj, rules

    def test_attach_network_get_network_policy(self):

        obj = self._create_test_policy()

        policy_obj = policy.QosPolicy.get_network_policy(self.context,
                                                         self._network['id'])
        self.assertIsNone(policy_obj)

        # Now attach policy and repeat
        obj.attach_network(self._network['id'])

        policy_obj = policy.QosPolicy.get_network_policy(self.context,
                                                         self._network['id'])
        self.assertEqual(obj, policy_obj)

    def test_attach_network_nonexistent_network(self):

        obj = self._create_test_policy()
        self.assertRaises(n_exc.NetworkQosBindingNotFound,
                          obj.attach_network, 'non-existent-network')

    def test_attach_network_get_policy_network(self):

        obj = self._create_test_policy()
        obj.attach_network(self._network['id'])

        networks = obj.get_bound_networks()
        self.assertEqual(1, len(networks))
        self.assertEqual(self._network['id'], networks[0])

    def test_attach_and_get_multiple_policy_networks(self):

        net1_id = self._network['id']
        net2 = net_obj.Network(self.context,
                               name='test-network2')
        net2.create()
        net2_id = net2['id']

        obj = self._create_test_policy()
        obj.attach_network(net1_id)
        obj.attach_network(net2_id)

        networks = obj.get_bound_networks()
        self.assertEqual(2, len(networks))
        self.assertIn(net1_id, networks)
        self.assertIn(net2_id, networks)

    def test_attach_port_nonexistent_port(self):

        obj = self._create_test_policy()
        self.assertRaises(n_exc.PortQosBindingNotFound,
                          obj.attach_port, 'non-existent-port')

    def test_attach_network_nonexistent_policy(self):

        policy_obj = self._make_object(self.obj_fields[0])
        self.assertRaises(n_exc.NetworkQosBindingNotFound,
                          policy_obj.attach_network, self._network['id'])

    def test_attach_port_nonexistent_policy(self):

        policy_obj = self._make_object(self.obj_fields[0])
        self.assertRaises(n_exc.PortQosBindingNotFound,
                          policy_obj.attach_port, self._port['id'])

    def test_attach_port_get_port_policy(self):

        obj = self._create_test_policy()

        policy_obj = policy.QosPolicy.get_network_policy(self.context,
                                                         self._network['id'])

        self.assertIsNone(policy_obj)

        # Now attach policy and repeat
        obj.attach_port(self._port['id'])

        policy_obj = policy.QosPolicy.get_port_policy(self.context,
                                                      self._port['id'])
        self.assertEqual(obj, policy_obj)

    def test_attach_and_get_multiple_policy_ports(self):

        port1_id = self._port['id']
        port2 = db_api.create_object(self.context, models_v2.Port,
                                     {'tenant_id': 'fake_tenant_id',
                                     'name': 'test-port2',
                                     'network_id': self._network['id'],
                                     'mac_address': 'fake_mac2',
                                     'admin_state_up': True,
                                     'status': 'ACTIVE',
                                     'device_id': 'fake_device',
                                     'device_owner': 'fake_owner'})
        port2_id = port2['id']

        obj = self._create_test_policy()
        obj.attach_port(port1_id)
        obj.attach_port(port2_id)

        ports = obj.get_bound_ports()
        self.assertEqual(2, len(ports))
        self.assertIn(port1_id, ports)
        self.assertIn(port2_id, ports)

    def test_attach_port_get_policy_port(self):

        obj = self._create_test_policy()
        obj.attach_port(self._port['id'])

        ports = obj.get_bound_ports()
        self.assertEqual(1, len(ports))
        self.assertEqual(self._port['id'], ports[0])

    def test_detach_port(self):
        obj = self._create_test_policy()
        obj.attach_port(self._port['id'])
        obj.detach_port(self._port['id'])

        policy_obj = policy.QosPolicy.get_port_policy(self.context,
                                                      self._port['id'])
        self.assertIsNone(policy_obj)

    def test_detach_network(self):
        obj = self._create_test_policy()
        obj.attach_network(self._network['id'])
        obj.detach_network(self._network['id'])

        policy_obj = policy.QosPolicy.get_network_policy(self.context,
                                                         self._network['id'])
        self.assertIsNone(policy_obj)

    def test_detach_port_nonexistent_port(self):
        obj = self._create_test_policy()
        self.assertRaises(n_exc.PortQosBindingNotFound,
                          obj.detach_port, 'non-existent-port')

    def test_detach_network_nonexistent_network(self):
        obj = self._create_test_policy()
        self.assertRaises(n_exc.NetworkQosBindingNotFound,
                          obj.detach_network, 'non-existent-port')

    def test_detach_port_nonexistent_policy(self):
        policy_obj = self._make_object(self.obj_fields[0])
        self.assertRaises(n_exc.PortQosBindingNotFound,
                          policy_obj.detach_port, self._port['id'])

    def test_detach_network_nonexistent_policy(self):
        policy_obj = self._make_object(self.obj_fields[0])
        self.assertRaises(n_exc.NetworkQosBindingNotFound,
                          policy_obj.detach_network, self._network['id'])

    def test_synthetic_rule_fields(self):
        policy_obj, rule_obj = self._create_test_policy_with_rules(
            [qos_consts.RULE_TYPE_BANDWIDTH_LIMIT])
        policy_obj = policy.QosPolicy.get_object(self.context,
                                                 id=policy_obj.id)
        self.assertEqual(rule_obj, policy_obj.rules)

    def test_get_object_fetches_rules_non_lazily(self):
        policy_obj, rule_obj = self._create_test_policy_with_rules(
            [qos_consts.RULE_TYPE_BANDWIDTH_LIMIT])
        policy_obj = policy.QosPolicy.get_object(self.context,
                                                 id=policy_obj.id)
        self.assertEqual(rule_obj, policy_obj.rules)

        primitive = policy_obj.obj_to_primitive()
        self.assertNotEqual([], (primitive['versioned_object.data']['rules']))

    def test_to_dict_returns_rules_as_dicts(self):
        policy_obj, rule_obj = self._create_test_policy_with_rules(
            [qos_consts.RULE_TYPE_BANDWIDTH_LIMIT])
        policy_obj = policy.QosPolicy.get_object(self.context,
                                                 id=policy_obj.id)

        obj_dict = policy_obj.to_dict()
        rule_dict = rule_obj[0].to_dict()

        # first make sure that to_dict() is still sane and does not return
        # objects
        for obj in (rule_dict, obj_dict):
            self.assertIsInstance(obj, dict)

        self.assertEqual(rule_dict, obj_dict['rules'][0])

    def test_shared_default(self):
        obj = self._make_object(self.obj_fields[0])
        self.assertFalse(obj.shared)

    def test_delete_not_allowed_if_policy_in_use_by_port(self):
        obj = self._create_test_policy()
        obj.attach_port(self._port['id'])

        self.assertRaises(n_exc.QosPolicyInUse, obj.delete)

        obj.detach_port(self._port['id'])
        obj.delete()

    def test_delete_not_allowed_if_policy_in_use_by_network(self):
        obj = self._create_test_policy()
        obj.attach_network(self._network['id'])

        self.assertRaises(n_exc.QosPolicyInUse, obj.delete)

        obj.detach_network(self._network['id'])
        obj.delete()

    def test_reload_rules_reloads_rules(self):
        policy_obj, rule_obj = self._create_test_policy_with_rules(
            [qos_consts.RULE_TYPE_BANDWIDTH_LIMIT])
        self.assertEqual([], policy_obj.rules)

        policy_obj.reload_rules()
        self.assertEqual(rule_obj, policy_obj.rules)

    def test_get_bound_tenant_ids_returns_set_of_tenant_ids(self):
        obj = self._create_test_policy()
        obj.attach_port(self._port['id'])
        ids = self._test_class.get_bound_tenant_ids(self.context, obj['id'])
        self.assertEqual(ids.pop(), self._port.project_id)
        self.assertEqual(len(ids), 0)

        obj.detach_port(self._port['id'])
        obj.delete()

    @staticmethod
    def _policy_through_version(obj, version):
        primitive = obj.obj_to_primitive(target_version=version)
        return policy.QosPolicy.clean_obj_from_primitive(primitive)

    def test_object_version(self):
        policy_obj, rule_objs = self._create_test_policy_with_rules(
            RULE_OBJ_CLS.keys(), reload_rules=True)

        policy_obj_v1_2 = self._policy_through_version(
            policy_obj, policy.QosPolicy.VERSION)

        for rule_obj in rule_objs:
            self.assertIn(rule_obj, policy_obj_v1_2.rules)

    def test_object_version_degradation_1_3_to_1_2_null_description(self):
        policy_obj = self._create_test_policy()
        policy_obj.description = None
        with testtools.ExpectedException(exception.IncompatibleObjectVersion):
            policy_obj.obj_to_primitive('1.2')

    def test_object_version_degradation_to_1_0(self):
        #NOTE(mangelajo): we should not check .VERSION, since that's the
        #                 local version on the class definition
        policy_obj, rule_objs = self._create_test_policy_with_rules(
            [qos_consts.RULE_TYPE_BANDWIDTH_LIMIT,
             qos_consts.RULE_TYPE_DSCP_MARKING], reload_rules=True)

        policy_obj_v1_0 = self._policy_through_version(policy_obj, '1.0')

        self.assertIn(rule_objs[0], policy_obj_v1_0.rules)
        self.assertNotIn(rule_objs[1], policy_obj_v1_0.rules)

    def test_object_version_degradation_1_2_to_1_1(self):
        #NOTE(mangelajo): we should not check .VERSION, since that's the
        #                 local version on the class definition
        policy_obj, rule_objs = self._create_test_policy_with_rules(
            [qos_consts.RULE_TYPE_BANDWIDTH_LIMIT,
             qos_consts.RULE_TYPE_DSCP_MARKING,
             qos_consts.RULE_TYPE_MINIMUM_BANDWIDTH], reload_rules=True)

        policy_obj_v1_1 = self._policy_through_version(policy_obj, '1.1')

        self.assertIn(rule_objs[0], policy_obj_v1_1.rules)
        self.assertIn(rule_objs[1], policy_obj_v1_1.rules)
        self.assertNotIn(rule_objs[2], policy_obj_v1_1.rules)

    def test_v1_4_to_v1_3_drops_project_id(self):
        policy_new = self._create_test_policy()

        policy_v1_3 = policy_new.obj_to_primitive(target_version='1.3')
        self.assertNotIn('project_id', policy_v1_3['versioned_object.data'])
        self.assertIn('tenant_id', policy_v1_3['versioned_object.data'])

    def test_filter_by_shared(self):
        policy_obj = policy.QosPolicy(
            self.context, name='shared-policy', shared=True)
        policy_obj.create()

        policy_obj = policy.QosPolicy(
            self.context, name='private-policy', shared=False)
        policy_obj.create()

        shared_policies = policy.QosPolicy.get_objects(
            self.context, shared=True)
        self.assertEqual(1, len(shared_policies))
        self.assertEqual('shared-policy', shared_policies[0].name)

        private_policies = policy.QosPolicy.get_objects(
            self.context, shared=False)
        self.assertEqual(1, len(private_policies))
        self.assertEqual('private-policy', private_policies[0].name)

    def test_get_objects_queries_constant(self):
        # NOTE(korzen) QoSPolicy is using extra queries to reload rules.
        # QoSPolicy currently cannot be loaded using constant queries number.
        # It can be reworked in follow-up patch.
        pass
