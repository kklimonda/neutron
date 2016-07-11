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

import collections
import copy
import pprint
import time

from neutron_lib import constants
from oslo_log import log as logging
from oslo_utils import importutils

from neutron.api.rpc.callbacks import exceptions

LOG = logging.getLogger(__name__)

VERSIONS_TTL = 60

# This is the list of agents that started using this rpc push/pull mechanism
# for versioned objects, but at that time stable/liberty, they were not
# reporting versions, so we need to assume they need QosPolicy 1.0
#TODO(mangelajo): Remove this logic in Newton, since those agents will be
#                 already reporting From N to O
NON_REPORTING_AGENT_TYPES = [constants.AGENT_TYPE_OVS,
                             constants.AGENT_TYPE_NIC_SWITCH]


# NOTE(mangelajo): if we import this globally we end up with a (very
#                  long) circular dependency, this can be fixed if we
#                  stop importing all exposed classes in
#                  neutron.api.rpc.callbacks.resources and provide
#                  a decorator to expose classes
def _import_resources():
    return importutils.import_module('neutron.api.rpc.callbacks.resources')


AgentConsumer = collections.namedtuple('AgentConsumer', ['agent_type',
                                                         'host'])
AgentConsumer.__repr__ = lambda self: '%s@%s' % self


class ResourceConsumerTracker(object):
    """Class to be provided back by consumer_versions_callback.

    This class is responsible for fetching the local versions of
    resources, and letting the callback register every consumer's
    resource version.

    Later on, this class can also be used to recalculate, for each
    resource type, the collection of versions that are local or
    known by one or more consumers.
    """

    def __init__(self):
        # Initialize with the local (server) versions, as we always want
        # to send those. Agents, as they upgrade, will need the latest version,
        # and there is a corner case we'd not be covering otherwise:
        #   1) one or several neutron-servers get disconnected from rpc (while
        #      running)
        #   2) a new agent comes up, with the latest version and it reports
        #      2 ways:
        #     a) via status report (which will be stored in the database)
        #     b) via fanout call to all neutron servers, this way, all of them
        #        get their version set updated right away without the need to
        #        re-fetch anything from the database.
        #   3) the neutron-servers get back online to the rpc bus, but they
        #      lost the fanout message.
        #
        # TODO(mangelajo) To cover this case we may need a callback from oslo
        # messaging to get notified about disconnections/reconnections to the
        # rpc bus, invalidating the consumer version cache when we receive such
        # callback.
        self._versions = self._get_local_resource_versions()
        self._versions_by_consumer = collections.defaultdict(dict)
        self._needs_recalculation = False

    def _get_local_resource_versions(self):
        resources = _import_resources()
        local_resource_versions = collections.defaultdict(set)
        for resource_type, version in (
                resources.LOCAL_RESOURCE_VERSIONS.items()):
            local_resource_versions[resource_type].add(version)
        return local_resource_versions

    # TODO(mangelajo): add locking with _recalculate_versions if we ever
    #                  move out of green threads.
    def _set_version(self, consumer, resource_type, version):
        """Set or update a consumer resource type version."""
        self._versions[resource_type].add(version)
        consumer_versions = self._versions_by_consumer[consumer]
        prev_version = consumer_versions.get(resource_type, None)
        if version:
            consumer_versions[resource_type] = version
        else:
            consumer_versions.pop(resource_type, None)

        if prev_version != version:
            # If a version got updated/changed in a consumer, we need to
            # recalculate the main dictionary of versions based on the
            # new _versions_by_consumer.
            # We defer the recalculation until every consumer version has
            # been set for all of its resource types.
            self._needs_recalculation = True
            LOG.debug("Version for resource type %(resource_type)s changed "
                      "%(prev_version)s to %(version)s on "
                      "consumer %(consumer)s",
                      {'resource_type': resource_type,
                       'version': version,
                       'prev_version': prev_version,
                       'consumer': consumer})

    def set_versions(self, consumer, versions):
        """Set or update an specific consumer resource types."""
        for resource_type, resource_version in versions.items():
            self._set_version(consumer, resource_type,
                             resource_version)

        if versions:
            self._cleanup_removed_versions(consumer, versions)
        else:
            self._handle_no_set_versions(consumer)

    def _cleanup_removed_versions(self, consumer, versions):
        """Check if any version report has been removed, and cleanup."""
        prev_resource_types = set(
            self._versions_by_consumer[consumer].keys())
        cur_resource_types = set(versions.keys())
        removed_resource_types = prev_resource_types - cur_resource_types
        for resource_type in removed_resource_types:
            self._set_version(consumer, resource_type, None)

    def _handle_no_set_versions(self, consumer):
        """Handle consumers reporting no versions."""
        if isinstance(consumer, AgentConsumer):
            if consumer.agent_type in NON_REPORTING_AGENT_TYPES:
                resources = _import_resources()
                self._versions_by_consumer[consumer] = {
                    resources.QOS_POLICY: '1.0'}
                self._versions[resources.QOS_POLICY].add('1.0')
                return

        if self._versions_by_consumer[consumer]:
            self._needs_recalculation = True
        self._versions_by_consumer[consumer] = {}

    def get_resource_versions(self, resource_type):
        """Fetch the versions necessary to notify all consumers."""
        if self._needs_recalculation:
            self._recalculate_versions()
            self._needs_recalculation = False

        return copy.copy(self._versions[resource_type])

    def report(self):
        """Output debug information about the consumer versions."""
        #TODO(mangelajo): report only when pushed_versions differ from
        #                 previous reports.
        format = lambda versions: pprint.pformat(dict(versions), indent=4)
        debug_dict = {'pushed_versions': format(self._versions),
                      'consumer_versions': format(self._versions_by_consumer)}

        LOG.debug('Tracked resource versions report:\n'
                  'pushed versions:\n%(pushed_versions)s\n\n'
                  'consumer versions:\n%(consumer_versions)s\n', debug_dict)

    # TODO(mangelajo): Add locking if we ever move out of greenthreads.
    def _recalculate_versions(self):
        """Recalculate the _versions set.

        Re-fetch the local (server) versions and expand with consumers'
        versions.
        """
        versions = self._get_local_resource_versions()
        for versions_dict in self._versions_by_consumer.values():
            for res_type, res_version in versions_dict.items():
                versions[res_type].add(res_version)
        self._versions = versions


class CachedResourceConsumerTracker(object):
    """This class takes care of the caching logic of versions."""

    def __init__(self):
        self._consumer_versions_callback = None
        # This is TTL expiration time, 0 means it will be expired at start
        self._expires_at = 0
        self._versions = ResourceConsumerTracker()

    def _update_consumer_versions(self):
        if self._consumer_versions_callback:
            new_tracker = ResourceConsumerTracker()
            self._consumer_versions_callback(new_tracker)
            self._versions = new_tracker
            self._versions.report()
        else:
            raise exceptions.VersionsCallbackNotFound()

    def _check_expiration(self):
        if time.time() > self._expires_at:
            self._update_consumer_versions()
            self._expires_at = time.time() + VERSIONS_TTL

    def set_consumer_versions_callback(self, callback):
        self._consumer_versions_callback = callback

    def get_resource_versions(self, resource_type):
        self._check_expiration()
        return self._versions.get_resource_versions(resource_type)

    def update_versions(self, consumer, resource_versions):
        self._versions.set_versions(consumer, resource_versions)

    def report(self):
        self._check_expiration()
        self._versions.report()

_cached_version_tracker = None


#NOTE(ajo): add locking if we ever stop using greenthreads
def _get_cached_tracker():
    global _cached_version_tracker
    if not _cached_version_tracker:
        _cached_version_tracker = CachedResourceConsumerTracker()
    return _cached_version_tracker


def set_consumer_versions_callback(callback):
    """Register a callback to retrieve the system consumer versions.

    Specific consumer logic has been decoupled from this, so we could reuse
    in other places.

    The callback will receive a ResourceConsumerTracker object,
    and the ResourceConsumerTracker methods must be used to provide
    each consumer versions. Consumer ids can be obtained from this
    module via the next functions:
        * get_agent_consumer_id
    """
    _get_cached_tracker().set_consumer_versions_callback(callback)


def get_resource_versions(resource_type):
    """Return the set of versions expected by the consumers of a resource."""
    return _get_cached_tracker().get_resource_versions(resource_type)


def update_versions(consumer, resource_versions):
    """Update the resources' versions for a consumer id."""
    _get_cached_tracker().update_versions(consumer, resource_versions)


def report():
    """Report resource versions in debug logs."""
    _get_cached_tracker().report()
