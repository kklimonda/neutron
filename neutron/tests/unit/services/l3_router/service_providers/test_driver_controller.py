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

import mock
from neutron_lib import constants
from neutron_lib import context
from neutron_lib import exceptions as lib_exc
from neutron_lib.plugins import directory
from oslo_utils import uuidutils
import testtools

from neutron.plugins.common import constants as p_cons
from neutron.services.l3_router.service_providers import driver_controller
from neutron.services import provider_configuration
from neutron.tests import base
from neutron.tests.unit import testlib_api


DB_PLUGIN_KLASS = 'neutron.db.db_base_plugin_v2.NeutronDbPluginV2'


class TestDriverController(testlib_api.SqlTestCase):

    def setUp(self):
        super(TestDriverController, self).setUp()
        self.setup_coreplugin(DB_PLUGIN_KLASS)
        self.fake_l3 = mock.Mock()
        self.dc = driver_controller.DriverController(self.fake_l3)
        self.fake_l3.l3_driver_controller = self.dc
        self.ctx = context.get_admin_context()

    def _return_provider_for_flavor(self, provider):
        self.dc._flavor_plugin_ref = mock.Mock()
        self.dc._flavor_plugin_ref.get_flavor.return_value = {'id': 'abc'}
        provider = {'provider': provider}
        self.dc._flavor_plugin_ref.get_flavor_next_provider.return_value = [
            provider]

    def test_uses_scheduler(self):
        self._return_provider_for_flavor('dvrha')
        router_db = mock.Mock()
        flavor_id = uuidutils.generate_uuid()
        router_id = uuidutils.generate_uuid()
        router = dict(id=router_id, flavor_id=flavor_id)
        self.dc._set_router_provider('router', 'PRECOMMIT_CREATE', self,
                                     self.ctx, router, router_db)
        self.assertTrue(self.dc.uses_scheduler(self.ctx, router_id))
        self.dc.drivers['dvrha'].use_integrated_agent_scheduler = False
        self.assertFalse(self.dc.uses_scheduler(self.ctx, router_id))

    def test_driver_owns_router(self):
        self._return_provider_for_flavor('dvrha')
        router_db = mock.Mock()
        flavor_id = uuidutils.generate_uuid()
        r1 = uuidutils.generate_uuid()
        r2 = uuidutils.generate_uuid()
        router = dict(id=r1, flavor_id=flavor_id)
        self.dc._set_router_provider('router', 'PRECOMMIT_CREATE', self,
                                     self.ctx, router, router_db)
        self.assertTrue(self.dc.drivers['dvrha'].owns_router(self.ctx, r1))
        self.assertFalse(self.dc.drivers['dvr'].owns_router(self.ctx, r1))
        self.assertFalse(self.dc.drivers['dvr'].owns_router(self.ctx, r2))
        self.assertFalse(self.dc.drivers['dvr'].owns_router(self.ctx, None))

    def test__set_router_provider_flavor_specified(self):
        self._return_provider_for_flavor('dvrha')
        router_db = mock.Mock()
        flavor_id = uuidutils.generate_uuid()
        router_id = uuidutils.generate_uuid()
        router = dict(id=router_id, flavor_id=flavor_id)
        self.dc._set_router_provider('router', 'PRECOMMIT_CREATE', self,
                                     self.ctx, router, router_db)
        self.assertEqual(flavor_id, router_db.flavor_id)
        self.assertEqual(self.dc.drivers['dvrha'],
                         self.dc.get_provider_for_router(self.ctx,
                                                         router_id))

    def test__update_router_provider_invalid(self):
        test_dc = driver_controller.DriverController(self.fake_l3)
        with mock.patch.object(test_dc, "get_provider_for_router"):
            with mock.patch.object(
                driver_controller,
                "_ensure_driver_supports_request") as _ensure:
                _ensure.side_effect = lib_exc.InvalidInput(
                    error_message='message')
                self.assertRaises(
                    lib_exc.InvalidInput,
                    test_dc._update_router_provider,
                    None, None, None, None,
                    None, {'name': 'testname'},
                    {'flavor_id': 'old_fid'}, None)

    def test__set_router_provider_attr_lookups(self):
        # ensure correct drivers are looked up based on attrs
        router_id1 = uuidutils.generate_uuid()
        router_id2 = uuidutils.generate_uuid()
        router_id3 = uuidutils.generate_uuid()
        router_id4 = uuidutils.generate_uuid()
        router_id5 = uuidutils.generate_uuid()
        router_id6 = uuidutils.generate_uuid()
        router_id7 = uuidutils.generate_uuid()
        router_id8 = uuidutils.generate_uuid()
        router_id9 = uuidutils.generate_uuid()
        cases = [
            ('dvrha', dict(id=router_id1, distributed=True, ha=True)),
            ('dvr', dict(id=router_id2, distributed=True, ha=False)),
            ('ha', dict(id=router_id3, distributed=False, ha=True)),
            ('single_node', dict(id=router_id4, distributed=False,
                                 ha=False)),
            ('ha', dict(id=router_id5, ha=True,
                        distributed=constants.ATTR_NOT_SPECIFIED)),
            ('dvr', dict(id=router_id6, distributed=True,
                        ha=constants.ATTR_NOT_SPECIFIED)),
            ('single_node', dict(id=router_id7, ha=False,
                                 distributed=constants.ATTR_NOT_SPECIFIED)),
            ('single_node', dict(id=router_id8, distributed=False,
                                 ha=constants.ATTR_NOT_SPECIFIED)),
            ('single_node', dict(id=router_id9,
                                 distributed=constants.ATTR_NOT_SPECIFIED,
                                 ha=constants.ATTR_NOT_SPECIFIED)),
        ]
        for driver, body in cases:
            self.dc._set_router_provider('router', 'PRECOMMIT_CREATE', self,
                                         self.ctx, body, mock.Mock())
            self.assertEqual(self.dc.drivers[driver],
                             self.dc.get_provider_for_router(self.ctx,
                                                             body['id']),
                             'Expecting %s for body %s' % (driver, body))

    def test__clear_router_provider(self):
        # ensure correct drivers are looked up based on attrs
        router_id1 = uuidutils.generate_uuid()
        body = dict(id=router_id1, distributed=True, ha=True)
        self.dc._set_router_provider('router', 'PRECOMMIT_CREATE', self,
                                     self.ctx, body, mock.Mock())
        self.assertEqual(self.dc.drivers['dvrha'],
                         self.dc.get_provider_for_router(self.ctx,
                                                         body['id']))
        self.dc._clear_router_provider('router', 'PRECOMMIT_DELETE', self,
                                       self.ctx, body['id'])
        with testtools.ExpectedException(ValueError):
            # if association was cleared, get_router will be called
            self.fake_l3.get_router.side_effect = ValueError
            self.dc.get_provider_for_router(self.ctx, body['id'])

    def test__flavor_plugin(self):
        directory.add_plugin(p_cons.FLAVORS, mock.Mock())
        _dc = driver_controller.DriverController(self.fake_l3)
        self.assertEqual(
            directory.get_plugin(p_cons.FLAVORS), _dc._flavor_plugin)


class Test_LegacyPlusProviderConfiguration(base.BaseTestCase):

    @mock.patch.object(provider_configuration.ProviderConfiguration,
                       "add_provider")
    def test__update_router_provider_invalid(self, mock_method):
            mock_method.side_effect = lib_exc.Invalid(message='message')
            driver_controller._LegacyPlusProviderConfiguration()
