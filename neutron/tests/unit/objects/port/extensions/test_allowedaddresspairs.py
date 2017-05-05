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

import itertools

from neutron import context
from neutron.objects.port.extensions import allowedaddresspairs
from neutron.tests.unit.objects import test_base as obj_test_base
from neutron.tests.unit import testlib_api


class AllowedAddrPairsIfaceObjTestCase(obj_test_base.BaseObjectIfaceTestCase):

    _test_class = allowedaddresspairs.AllowedAddressPair


#TODO(mhickey): Add common base db test class specifically for port extensions
class AllowedAddrPairsDbObjTestCase(obj_test_base.BaseDbObjectTestCase,
                                    testlib_api.SqlTestCase):

    _test_class = allowedaddresspairs.AllowedAddressPair

    def setUp(self):
        super(AllowedAddrPairsDbObjTestCase, self).setUp()
        self.context = context.get_admin_context()
        self._create_test_network()
        self._create_test_port(self._network)
        for obj in itertools.chain(self.db_objs, self.obj_fields, self.objs):
            obj['port_id'] = self._port['id']
