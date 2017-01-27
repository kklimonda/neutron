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

from neutron_lib import constants
import six

from neutron.db.availability_zone import router as router_az_db
from neutron.db import common_db_mixin
from neutron.db import l3_agentschedulers_db
from neutron.db import l3_db
from neutron.extensions import l3
from neutron.extensions import router_availability_zone as router_az
from neutron.tests.unit.extensions import test_availability_zone as test_az
from neutron.tests.unit.extensions import test_l3


class AZL3ExtensionManager(test_az.AZExtensionManager):

    def get_resources(self):
        return (super(AZL3ExtensionManager, self).get_resources() +
                l3.L3.get_resources())


class AZRouterTestPlugin(common_db_mixin.CommonDbMixin,
                         l3_db.L3_NAT_db_mixin,
                         router_az_db.RouterAvailabilityZoneMixin,
                         l3_agentschedulers_db.AZL3AgentSchedulerDbMixin):
    supported_extension_aliases = ["router", "l3_agent_scheduler",
                                   "router_availability_zone"]

    @classmethod
    def get_plugin_type(cls):
        return constants.L3

    def get_plugin_description(self):
        return "L3 Routing Service Plugin for testing"


class TestAZRouterCase(test_az.AZTestCommon, test_l3.L3NatTestCaseMixin):
    def setUp(self):
        plugin = ('neutron.tests.unit.extensions.'
                  'test_availability_zone.AZTestPlugin')
        l3_plugin = ('neutron.tests.unit.extensions.'
                     'test_router_availability_zone.AZRouterTestPlugin')
        service_plugins = {'l3_plugin_name': l3_plugin}

        self._backup()
        l3.RESOURCE_ATTRIBUTE_MAP['routers'].update(
            router_az.EXTENDED_ATTRIBUTES_2_0['routers'])
        ext_mgr = AZL3ExtensionManager()
        super(TestAZRouterCase, self).setUp(plugin=plugin, ext_mgr=ext_mgr,
                                            service_plugins=service_plugins)

    def _backup(self):
        self.contents_backup = {}
        for res, attrs in six.iteritems(l3.RESOURCE_ATTRIBUTE_MAP):
            self.contents_backup[res] = attrs.copy()
        self.addCleanup(self._restore)

    def _restore(self):
        l3.RESOURCE_ATTRIBUTE_MAP = self.contents_backup

    def test_create_router_with_az(self):
        self._register_azs()
        az_hints = ['nova2']
        with self.router(availability_zone_hints=az_hints) as router:
            res = self._show('routers', router['router']['id'])
            self.assertItemsEqual(az_hints,
                                  res['router']['availability_zone_hints'])

    def test_create_router_with_azs(self):
        self._register_azs()
        az_hints = ['nova2', 'nova3']
        with self.router(availability_zone_hints=az_hints) as router:
            res = self._show('routers', router['router']['id'])
            self.assertItemsEqual(az_hints,
                                  res['router']['availability_zone_hints'])

    def test_create_router_without_az(self):
        with self.router() as router:
            res = self._show('routers', router['router']['id'])
            self.assertEqual([], res['router']['availability_zone_hints'])

    def test_create_router_with_empty_az(self):
        with self.router(availability_zone_hints=[]) as router:
            res = self._show('routers', router['router']['id'])
            self.assertEqual([], res['router']['availability_zone_hints'])

    def test_create_router_with_none_existing_az(self):
        res = self._create_router(self.fmt, 'tenant_id',
                                  availability_zone_hints=['nova4'])
        self.assertEqual(404, res.status_int)
