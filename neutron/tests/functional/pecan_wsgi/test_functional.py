# Copyright (c) 2015 Mirantis, Inc.
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

import os

import mock
from oslo_config import cfg
from oslo_serialization import jsonutils
from oslo_utils import uuidutils
from pecan import request
from pecan import set_config
from pecan.testing import load_test_app
import testtools

from neutron.api import extensions
from neutron.api.v2 import attributes
from neutron.common import exceptions as n_exc
from neutron import context
from neutron import manager
from neutron.pecan_wsgi.controllers import root as controllers
from neutron.tests.unit import testlib_api


class PecanFunctionalTest(testlib_api.SqlTestCase):

    def setUp(self):
        self.setup_coreplugin('neutron.plugins.ml2.plugin.Ml2Plugin')
        super(PecanFunctionalTest, self).setUp()
        self.addCleanup(extensions.PluginAwareExtensionManager.clear_instance)
        self.addCleanup(set_config, {}, overwrite=True)
        self.set_config_overrides()
        self.setup_app()

    def setup_app(self):
        self.app = load_test_app(os.path.join(
            os.path.dirname(__file__),
            'config.py'
        ))
        self._gen_port()

    def _gen_port(self):
        pl = manager.NeutronManager.get_plugin()
        network_id = pl.create_network(context.get_admin_context(), {
            'network':
            {'name': 'pecannet', 'tenant_id': 'tenid', 'shared': False,
             'admin_state_up': True, 'status': 'ACTIVE'}})['id']
        self.port = pl.create_port(context.get_admin_context(), {
            'port':
            {'tenant_id': 'tenid', 'network_id': network_id,
             'fixed_ips': attributes.ATTR_NOT_SPECIFIED,
             'mac_address': '00:11:22:33:44:55',
             'admin_state_up': True, 'device_id': 'FF',
             'device_owner': 'pecan', 'name': 'pecan'}})

    def set_config_overrides(self):
        cfg.CONF.set_override('auth_strategy', 'noauth')


class TestV2Controller(PecanFunctionalTest):

    def test_get(self):
        response = self.app.get('/v2.0/ports.json')
        self.assertEqual(response.status_int, 200)

    def test_post(self):
        response = self.app.post_json('/v2.0/ports.json',
            params={'port': {'network_id': self.port['network_id'],
                             'admin_state_up': True,
                             'tenant_id': 'tenid'}},
            headers={'X-Tenant-Id': 'tenid'})
        self.assertEqual(response.status_int, 201)

    def test_put(self):
        response = self.app.put_json('/v2.0/ports/%s.json' % self.port['id'],
                                     params={'port': {'name': 'test'}},
                                     headers={'X-Tenant-Id': 'tenid'})
        self.assertEqual(response.status_int, 200)

    def test_delete(self):
        response = self.app.delete('/v2.0/ports/%s.json' % self.port['id'],
                                   headers={'X-Tenant-Id': 'tenid'})
        self.assertEqual(response.status_int, 204)

    def test_plugin_initialized(self):
        self.assertIsNotNone(manager.NeutronManager._instance)

    def test_get_extensions(self):
        response = self.app.get('/v2.0/extensions.json')
        self.assertEqual(response.status_int, 200)

    def test_get_specific_extension(self):
        response = self.app.get('/v2.0/extensions/allowed-address-pairs.json')
        self.assertEqual(response.status_int, 200)


class TestErrors(PecanFunctionalTest):

    def test_404(self):
        response = self.app.get('/assert_called_once', expect_errors=True)
        self.assertEqual(response.status_int, 404)

    def test_bad_method(self):
        response = self.app.patch('/v2.0/ports/44.json',
                                  expect_errors=True)
        self.assertEqual(response.status_int, 405)


class TestRequestID(PecanFunctionalTest):

    def test_request_id(self):
        response = self.app.get('/')
        self.assertIn('x-openstack-request-id', response.headers)
        self.assertTrue(
            response.headers['x-openstack-request-id'].startswith('req-'))
        id_part = response.headers['x-openstack-request-id'].split('req-')[1]
        self.assertTrue(uuidutils.is_uuid_like(id_part))


class TestKeystoneAuth(PecanFunctionalTest):

    def set_config_overrides(self):
        # default auth strategy is keystone so we pass
        pass

    def test_auth_enforced(self):
        response = self.app.get('/', expect_errors=True)
        self.assertEqual(response.status_int, 401)


class TestInvalidAuth(PecanFunctionalTest):
    def setup_app(self):
        # disable normal app setup since it will fail
        pass

    def test_invalid_auth_strategy(self):
        cfg.CONF.set_override('auth_strategy', 'badvalue')
        with testtools.ExpectedException(n_exc.InvalidConfigurationOption):
            load_test_app(os.path.join(os.path.dirname(__file__), 'config.py'))


class TestExceptionTranslationHook(PecanFunctionalTest):

    def test_neutron_nonfound_to_webob_exception(self):
        # this endpoint raises a Neutron notfound exception. make sure it gets
        # translated into a 404 error
        with mock.patch(
            'neutron.pecan_wsgi.controllers.root.CollectionsController.get',
            side_effect=n_exc.NotFound()
        ):
            response = self.app.get('/v2.0/ports.json', expect_errors=True)
            self.assertEqual(response.status_int, 404)

    def test_unexpected_exception(self):
        with mock.patch(
            'neutron.pecan_wsgi.controllers.root.CollectionsController.get',
            side_effect=ValueError('secretpassword')
        ):
            response = self.app.get('/v2.0/ports.json', expect_errors=True)
            self.assertNotIn(response.body, 'secretpassword')
            self.assertEqual(response.status_int, 500)


class TestRequestPopulatingHooks(PecanFunctionalTest):

    def setUp(self):
        super(TestRequestPopulatingHooks, self).setUp()

        # request.context is thread-local storage so it has to be accessed by
        # the controller. We can capture it into a list here to assert on after
        # the request finishes.

        def capture_request_details(*args, **kwargs):
            self.req_stash = {
                'context': request.context['neutron_context'],
                'resource_type': request.context['resource'],
            }
        mock.patch(
            'neutron.pecan_wsgi.controllers.root.CollectionsController.get',
            side_effect=capture_request_details
        ).start()

    # TODO(kevinbenton): add context tests for X-Roles etc

    def test_context_set_in_request(self):
        self.app.get('/v2.0/ports.json',
                     headers={'X-Tenant-Id': 'tenant_id'})
        self.assertEqual('tenant_id', self.req_stash['context'].tenant_id)

    def test_core_resource_identified(self):
        self.app.get('/v2.0/ports.json')
        self.assertEqual('port', self.req_stash['resource_type'])

    def test_service_plugin_identified(self):
        # TODO(kevinbenton): fix the unit test setup to include an l3 plugin
        self.skipTest("A dummy l3 plugin needs to be setup")
        self.app.get('/v2.0/routers.json')
        self.assertEqual('router', self.req_stash['resource_type'])
        # make sure the core plugin was identified as the handler for ports
        self.assertEqual(
            manager.NeutronManager.get_service_plugins()['L3_ROUTER_NAT'],
            self.req_stash['plugin'])


class TestEnforcementHooks(PecanFunctionalTest):

    def test_network_ownership_check(self):
        # TODO(kevinbenton): get a scenario that passes attribute population
        self.skipTest("Attribute population blocks this test as-is")
        response = self.app.post_json('/v2.0/ports.json',
            params={'port': {'network_id': self.port['network_id'],
                             'admin_state_up': True,
                             'tenant_id': 'tenid2'}},
            headers={'X-Tenant-Id': 'tenid'})
        self.assertEqual(response.status_int, 200)

    def test_quota_enforcement(self):
        # TODO(kevinbenton): this test should do something
        pass

    def test_policy_enforcement(self):
        # TODO(kevinbenton): this test should do something
        pass


class TestRootController(PecanFunctionalTest):
    """Test version listing on root URI."""

    def test_get(self):
        response = self.app.get('/')
        self.assertEqual(response.status_int, 200)
        json_body = jsonutils.loads(response.body)
        versions = json_body.get('versions')
        self.assertEqual(1, len(versions))
        for (attr, value) in controllers.V2Controller.version_info.items():
            self.assertIn(attr, versions[0])
            self.assertEqual(value, versions[0][attr])

    def _test_method_returns_405(self, method):
        api_method = getattr(self.app, method)
        response = api_method('/', expect_errors=True)
        self.assertEqual(response.status_int, 405)

    def test_post(self):
        self._test_method_returns_405('post')

    def test_put(self):
        self._test_method_returns_405('put')

    def test_patch(self):
        self._test_method_returns_405('patch')

    def test_delete(self):
        self._test_method_returns_405('delete')

    def test_head(self):
        self._test_method_returns_405('head')
