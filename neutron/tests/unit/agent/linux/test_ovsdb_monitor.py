# Copyright 2013 Red Hat, Inc.
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

from neutron.agent.common import ovs_lib
from neutron.agent.linux import ovsdb_monitor
from neutron.tests import base


class TestOvsdbMonitor(base.BaseTestCase):

    def test___init__(self):
        ovsdb_monitor.OvsdbMonitor('Interface')

    def test___init___with_columns(self):
        columns = ['col1', 'col2']
        with mock.patch(
            'neutron.agent.linux.async_process.AsyncProcess.__init__') as init:
            ovsdb_monitor.OvsdbMonitor('Interface', columns=columns)
            cmd = init.call_args_list[0][0][0]
            self.assertEqual('col1,col2', cmd[-1])

    def test___init___with_format(self):
        with mock.patch(
            'neutron.agent.linux.async_process.AsyncProcess.__init__') as init:
            ovsdb_monitor.OvsdbMonitor('Interface', format='blob')
            cmd = init.call_args_list[0][0][0]
            self.assertEqual('--format=blob', cmd[-1])


class TestSimpleInterfaceMonitor(base.BaseTestCase):

    def setUp(self):
        super(TestSimpleInterfaceMonitor, self).setUp()
        self.monitor = ovsdb_monitor.SimpleInterfaceMonitor()

    def test_has_updates_is_false_if_active_with_no_output(self):
        target = ('neutron.agent.linux.ovsdb_monitor.SimpleInterfaceMonitor'
                  '.is_active')
        with mock.patch(target, return_value=True):
            self.assertFalse(self.monitor.has_updates)

    def test_has_updates_after_calling_get_events_is_false(self):
        with mock.patch.object(
                self.monitor, 'process_events') as process_events:
            self.monitor.new_events = {'added': ['foo'], 'removed': ['foo1']}
            self.assertTrue(self.monitor.has_updates)
            self.monitor.get_events()
            self.assertTrue(process_events.called)
            self.assertFalse(self.monitor.has_updates)

    def process_event_unassigned_of_port(self):
        output = '{"data":[["e040fbec-0579-4990-8324-d338da33ae88","insert",'
        output += '"m50",["set",[]],["map",[]]]],"headings":["row","action",'
        output += '"name","ofport","external_ids"]}'
        with mock.patch.object(
                self.monitor, 'iter_stdout', return_value=[output]):
            self.monitor.process_events()
            self.assertEqual(self.monitor.new_events['added'][0]['ofport'],
                             ovs_lib.UNASSIGNED_OFPORT)
