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
import mock

from neutron.api.rpc.callbacks import exceptions
from neutron.api.rpc.callbacks import resources
from neutron.api.rpc.callbacks import version_manager
from neutron.tests import base


TEST_RESOURCE_TYPE = 'TestResourceType'
TEST_VERSION_A = '1.11'
TEST_VERSION_B = '1.12'

TEST_RESOURCE_TYPE_2 = 'AnotherResource'

AGENT_HOST_1 = 'host-1'
AGENT_HOST_2 = 'host-2'
AGENT_TYPE_1 = 'dhcp-agent'
AGENT_TYPE_2 = 'openvswitch-agent'
CONSUMER_1 = version_manager.AgentConsumer(AGENT_TYPE_1, AGENT_HOST_1)
CONSUMER_2 = version_manager.AgentConsumer(AGENT_TYPE_2, AGENT_HOST_2)


class ResourceConsumerTrackerTest(base.BaseTestCase):

    def test_consumer_set_versions(self):
        cv = version_manager.ResourceConsumerTracker()

        cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_A})
        self.assertIn(TEST_VERSION_A,
                      cv.get_resource_versions(TEST_RESOURCE_TYPE))

    def test_consumer_updates_version(self):
        cv = version_manager.ResourceConsumerTracker()

        for version in [TEST_VERSION_A, TEST_VERSION_B]:
            cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: version})

        self.assertEqual(set([TEST_VERSION_B]),
                         cv.get_resource_versions(TEST_RESOURCE_TYPE))

    def test_multiple_consumer_version_update(self):
        cv = version_manager.ResourceConsumerTracker()

        cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_A})
        cv.set_versions(CONSUMER_2, {TEST_RESOURCE_TYPE: TEST_VERSION_A})
        cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_B})

        self.assertEqual(set([TEST_VERSION_A, TEST_VERSION_B]),
                         cv.get_resource_versions(TEST_RESOURCE_TYPE))

    def test_consumer_downgrades_removing_resource(self):
        cv = version_manager.ResourceConsumerTracker()

        cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_B,
                                     TEST_RESOURCE_TYPE_2: TEST_VERSION_A})
        cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_A})

        self.assertEqual(set(),
                         cv.get_resource_versions(TEST_RESOURCE_TYPE_2))
        self.assertEqual(set([TEST_VERSION_A]),
                         cv.get_resource_versions(TEST_RESOURCE_TYPE))

    def test_consumer_downgrades_stops_reporting(self):
        cv = version_manager.ResourceConsumerTracker()

        cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_B,
                                     TEST_RESOURCE_TYPE_2: TEST_VERSION_A})
        cv.set_versions(CONSUMER_1, {})

        for resource_type in [TEST_RESOURCE_TYPE, TEST_RESOURCE_TYPE_2]:
            self.assertEqual(set(),
                             cv.get_resource_versions(resource_type))

    def test_compatibility_liberty_sriov_and_ovs_agents(self):

        def _fake_local_versions(self):
            local_versions = collections.defaultdict(set)
            local_versions[resources.QOS_POLICY].add('1.11')
            return local_versions

        for agent_type in version_manager.NON_REPORTING_AGENT_TYPES:
            consumer_id = version_manager.AgentConsumer(agent_type,
                                                        AGENT_HOST_1)

            cv = version_manager.ResourceConsumerTracker()
            cv._get_local_resource_versions = _fake_local_versions
            cv._versions = _fake_local_versions(mock.ANY)

            cv.set_versions(consumer_id, {})

            self.assertEqual(set(['1.0', '1.11']),
                             cv.get_resource_versions(resources.QOS_POLICY))

    def test_different_adds_triggers_recalculation(self):
        cv = version_manager.ResourceConsumerTracker()

        for version in [TEST_VERSION_A, TEST_VERSION_B]:
            cv.set_versions(CONSUMER_1, {TEST_RESOURCE_TYPE: version})

        self.assertTrue(cv._needs_recalculation)
        cv._recalculate_versions = mock.Mock()
        cv.get_resource_versions(TEST_RESOURCE_TYPE)
        cv._recalculate_versions.assert_called_once_with()


class CachedResourceConsumerTrackerTest(base.BaseTestCase):

    def test_exception_with_no_callback(self):
        cached_tracker = version_manager.CachedResourceConsumerTracker()
        self.assertRaises(
            exceptions.VersionsCallbackNotFound,
            cached_tracker.get_resource_versions, [mock.ANY])

    def _set_consumer_versions_callback(self, cached_tracker):
        def consumer_versions(rct):
            rct.set_versions(CONSUMER_1,
                             {TEST_RESOURCE_TYPE: TEST_VERSION_A})

        cached_tracker.set_consumer_versions_callback(consumer_versions)

    def test_consumer_versions_callback(self):
        cached_tracker = version_manager.CachedResourceConsumerTracker()
        self._set_consumer_versions_callback(cached_tracker)

        self.assertIn(TEST_VERSION_A,
                      cached_tracker.get_resource_versions(
                          TEST_RESOURCE_TYPE))

    def test_update_versions(self):
        cached_tracker = version_manager.CachedResourceConsumerTracker()
        self._set_consumer_versions_callback(cached_tracker)

        initial_versions = cached_tracker.get_resource_versions(
            TEST_RESOURCE_TYPE)

        initial_versions_2 = cached_tracker.get_resource_versions(
            TEST_RESOURCE_TYPE_2)

        cached_tracker.update_versions(
            CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_B,
                         TEST_RESOURCE_TYPE_2: TEST_VERSION_A})

        final_versions = cached_tracker.get_resource_versions(
            TEST_RESOURCE_TYPE)
        final_versions_2 = cached_tracker.get_resource_versions(
            TEST_RESOURCE_TYPE_2)

        self.assertNotEqual(initial_versions, final_versions)
        self.assertNotEqual(initial_versions_2, final_versions_2)

    def test_versions_ttl(self):
        self.refreshed = False

        def consumer_versions_callback(consumer_tracker):
            consumer_tracker.set_versions(
                CONSUMER_1, {TEST_RESOURCE_TYPE: TEST_VERSION_A})
            self.refreshed = True

        cached_tracker = version_manager.CachedResourceConsumerTracker()
        cached_tracker.set_consumer_versions_callback(
            consumer_versions_callback)
        with mock.patch('time.time') as time_patch:
            time_patch.return_value = 1
            cached_tracker.get_resource_versions(TEST_RESOURCE_TYPE)
            self.assertTrue(self.refreshed)
            self.refreshed = False

            time_patch.return_value = 2
            cached_tracker.get_resource_versions(TEST_RESOURCE_TYPE)
            self.assertFalse(self.refreshed)

            time_patch.return_value = 2 + version_manager.VERSIONS_TTL
            cached_tracker.get_resource_versions(TEST_RESOURCE_TYPE)
            self.assertTrue(self.refreshed)
