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

from tempest.lib import exceptions as lib_exc
from tempest import test

from neutron.tests.tempest.api import base


class TagTestJSON(base.BaseAdminNetworkTest):

    @classmethod
    @test.requires_ext(extension="tag", service="network")
    def resource_setup(cls):
        super(TagTestJSON, cls).resource_setup()
        cls.res_id = cls._create_resource()

    def _get_and_compare_tags(self, tags):
        res_body = self.client.get_tags(self.resource, self.res_id)
        self.assertItemsEqual(tags, res_body['tags'])

    def _test_tag_operations(self):
        # create and get tags
        tags = ['red', 'blue']
        res_body = self.client.update_tags(self.resource, self.res_id, tags)
        self.assertItemsEqual(tags, res_body['tags'])
        self._get_and_compare_tags(tags)

        # add a tag
        self.client.update_tag(self.resource, self.res_id, 'green')
        self._get_and_compare_tags(['red', 'blue', 'green'])

        # update tag exist
        self.client.update_tag(self.resource, self.res_id, 'red')
        self._get_and_compare_tags(['red', 'blue', 'green'])

        # replace tags
        tags = ['red', 'yellow', 'purple']
        res_body = self.client.update_tags(self.resource, self.res_id, tags)
        self.assertItemsEqual(tags, res_body['tags'])
        self._get_and_compare_tags(tags)

        # get tag
        self.client.get_tag(self.resource, self.res_id, 'red')

        # get tag not exist
        self.assertRaises(lib_exc.NotFound, self.client.get_tag,
                          self.resource, self.res_id, 'green')

        # delete tag
        self.client.delete_tag(self.resource, self.res_id, 'red')
        self._get_and_compare_tags(['yellow', 'purple'])

        # delete tag not exist
        self.assertRaises(lib_exc.NotFound, self.client.delete_tag,
                          self.resource, self.res_id, 'green')

        # delete tags
        self.client.delete_tags(self.resource, self.res_id)
        self._get_and_compare_tags([])


class TagNetworkTestJSON(TagTestJSON):
    resource = 'networks'

    @classmethod
    def _create_resource(cls):
        network = cls.create_network()
        return network['id']

    @test.attr(type='smoke')
    @test.idempotent_id('5621062d-fbfb-4437-9d69-138c78ea4188')
    def test_network_tags(self):
        self._test_tag_operations()


class TagFilterTestJSON(base.BaseAdminNetworkTest):
    credentials = ['primary', 'alt', 'admin']
    resource = 'networks'

    @classmethod
    @test.requires_ext(extension="tag", service="network")
    def resource_setup(cls):
        super(TagFilterTestJSON, cls).resource_setup()

        res1_id = cls._create_resource('tag-res1')
        res2_id = cls._create_resource('tag-res2')
        res3_id = cls._create_resource('tag-res3')
        res4_id = cls._create_resource('tag-res4')
        # tag-res5: a resource without tags
        cls._create_resource('tag-res5')

        cls.client.update_tags(cls.resource, res1_id, ['red'])
        cls.client.update_tags(cls.resource, res2_id, ['red', 'blue'])
        cls.client.update_tags(cls.resource, res3_id,
                               ['red', 'blue', 'green'])
        cls.client.update_tags(cls.resource, res4_id, ['green'])

    @classmethod
    def setup_clients(cls):
        super(TagFilterTestJSON, cls).setup_clients()
        cls.client = cls.alt_manager.network_client

    def _assertEqualResources(self, expected, res):
        actual = [n['name'] for n in res if n['name'].startswith('tag-res')]
        self.assertEqual(set(expected), set(actual))

    def _test_filter_tags(self):
        # tags single
        filters = {'tags': 'red'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res1', 'tag-res2', 'tag-res3'], res)

        # tags multi
        filters = {'tags': 'red,blue'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res2', 'tag-res3'], res)

        # tags-any single
        filters = {'tags-any': 'blue'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res2', 'tag-res3'], res)

        # tags-any multi
        filters = {'tags-any': 'red,blue'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res1', 'tag-res2', 'tag-res3'], res)

        # not-tags single
        filters = {'not-tags': 'red'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res4', 'tag-res5'], res)

        # not-tags multi
        filters = {'not-tags': 'red,blue'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res1', 'tag-res4', 'tag-res5'], res)

        # not-tags-any single
        filters = {'not-tags-any': 'blue'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res1', 'tag-res4', 'tag-res5'], res)

        # not-tags-any multi
        filters = {'not-tags-any': 'red,blue'}
        res = self._list_resource(filters)
        self._assertEqualResources(['tag-res4', 'tag-res5'], res)


class TagFilterNetworkTestJSON(TagFilterTestJSON):
    resource = 'networks'

    @classmethod
    def _create_resource(cls, name):
        res = cls.create_network(network_name=name)
        return res['id']

    def _list_resource(self, filters):
        res = self.client.list_networks(**filters)
        return res['networks']

    @test.attr(type='smoke')
    @test.idempotent_id('a66b5cca-7db2-40f5-a33d-8ac9f864e53e')
    def test_filter_network_tags(self):
        self._test_filter_tags()
