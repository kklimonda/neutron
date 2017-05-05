# Copyright (c) 2015 Mirantis, Inc.
# Copyright (c) 2015 Rackspace, Inc.
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

from oslo_log import log
import pecan
from pecan import request

from neutron._i18n import _LW
from neutron.api.views import versions as versions_view
from neutron import manager
from neutron.pecan_wsgi.controllers import extensions as ext_ctrl
from neutron.pecan_wsgi.controllers import utils

LOG = log.getLogger(__name__)
_VERSION_INFO = {}


def _load_version_info(version_info):
    assert version_info['id'] not in _VERSION_INFO
    _VERSION_INFO[version_info['id']] = version_info


def _get_version_info():
    return _VERSION_INFO.values()


class RootController(object):

    @utils.expose(generic=True)
    def index(self):
        # NOTE(kevinbenton): The pecan framework does not handle
        # any requests to the root because they are intercepted
        # by the 'version' returning wrapper.
        pass

    @utils.when(index, method='GET')
    @utils.when(index, method='HEAD')
    @utils.when(index, method='POST')
    @utils.when(index, method='PATCH')
    @utils.when(index, method='PUT')
    @utils.when(index, method='DELETE')
    def not_supported(self):
        pecan.abort(405)


class V2Controller(object):

    # Same data structure as neutron.api.versions.Versions for API backward
    # compatibility
    version_info = {
        'id': 'v2.0',
        'status': 'CURRENT'
    }
    _load_version_info(version_info)

    extensions = ext_ctrl.ExtensionsController()

    @utils.expose(generic=True)
    def index(self):
        builder = versions_view.get_view_builder(pecan.request)
        return dict(version=builder.build(self.version_info))

    @utils.when(index, method='HEAD')
    @utils.when(index, method='POST')
    @utils.when(index, method='PATCH')
    @utils.when(index, method='PUT')
    @utils.when(index, method='DELETE')
    def not_supported(self):
        pecan.abort(405)

    @utils.expose()
    def _lookup(self, collection, *remainder):
        # if collection exists in the extension to service plugins map then
        # we are assuming that collection is the service plugin and
        # needs to be remapped.
        # Example: https://neutron.endpoint/v2.0/lbaas/loadbalancers
        if (remainder and
                manager.NeutronManager.get_resources_for_path_prefix(
                    collection)):
            collection = remainder[0]
            remainder = remainder[1:]
        controller = manager.NeutronManager.get_controller_for_resource(
            collection)
        if not controller:
            LOG.warning(_LW("No controller found for: %s - returning response "
                            "code 404"), collection)
            pecan.abort(404)
        # Store resource and collection names in pecan request context so that
        # hooks can leverage them if necessary. The following code uses
        # attributes from the controller instance to ensure names have been
        # properly sanitized (eg: replacing dashes with underscores)
        request.context['resource'] = controller.resource
        request.context['collection'] = controller.collection
        # NOTE(blogan): initialize a dict to store the ids of the items walked
        # in the path for example: /networks/1234 would cause uri_identifiers
        # to contain: {'network_id': '1234'}
        # This is for backwards compatibility with legacy extensions that
        # defined their own controllers and expected kwargs to be passed in
        # with the uri_identifiers
        request.context['uri_identifiers'] = {}
        return controller, remainder


# This controller cannot be specified directly as a member of RootController
# as its path is not a valid python identifier
pecan.route(RootController, 'v2.0', V2Controller())
