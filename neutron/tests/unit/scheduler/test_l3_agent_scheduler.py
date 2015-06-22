# Copyright (c) 2013 OpenStack Foundation
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

import contextlib
import datetime
import uuid

import mock
import testscenarios

from oslo_config import cfg
from oslo_db import exception as db_exc
from oslo_utils import importutils
from oslo_utils import timeutils
from sqlalchemy.orm import query

from neutron.common import constants
from neutron.common import topics
from neutron import context as q_context
from neutron.db import agents_db
from neutron.db import common_db_mixin
from neutron.db import db_base_plugin_v2 as db_v2
from neutron.db import l3_agentschedulers_db
from neutron.db import l3_db
from neutron.db import l3_dvrscheduler_db
from neutron.db import l3_hamode_db
from neutron.db import l3_hascheduler_db
from neutron.extensions import l3agentscheduler as l3agent
from neutron import manager
from neutron.scheduler import l3_agent_scheduler
from neutron.tests import base
from neutron.tests.unit.db import test_db_base_plugin_v2
from neutron.tests.unit.extensions import test_l3
from neutron.tests.unit import testlib_api

# the below code is required for the following reason
# (as documented in testscenarios)
"""Multiply tests depending on their 'scenarios' attribute.
   This can be assigned to 'load_tests' in any test module to make this
   automatically work across tests in the module.
"""
load_tests = testscenarios.load_tests_apply_scenarios

HOST_DVR = 'my_l3_host_dvr'
DVR_L3_AGENT = {
    'binary': 'neutron-l3-agent',
    'host': HOST_DVR,
    'topic': topics.L3_AGENT,
    'configurations': {'agent_mode': 'dvr'},
    'agent_type': constants.AGENT_TYPE_L3,
    'start_flag': True
}

HOST_DVR_SNAT = 'my_l3_host_dvr_snat'
DVR_SNAT_L3_AGENT = {
    'binary': 'neutron-l3-agent',
    'host': HOST_DVR_SNAT,
    'topic': topics.L3_AGENT,
    'configurations': {'agent_mode': 'dvr_snat'},
    'agent_type': constants.AGENT_TYPE_L3,
    'start_flag': True
}


class FakeL3Scheduler(l3_agent_scheduler.L3Scheduler):

    def schedule(self):
        pass

    def _choose_router_agent(self):
        pass

    def _choose_router_agents_for_ha(self):
        pass


class L3SchedulerBaseTestCase(base.BaseTestCase):

    def setUp(self):
        super(L3SchedulerBaseTestCase, self).setUp()
        self.scheduler = FakeL3Scheduler()
        self.plugin = mock.Mock()

    def test_auto_schedule_routers(self):
        self.plugin.get_enabled_agent_on_host.return_value = [mock.ANY]
        with contextlib.nested(
            mock.patch.object(self.scheduler, 'get_routers_to_schedule'),
            mock.patch.object(self.scheduler, 'get_routers_can_schedule')) as (
            gs, gr):
            result = self.scheduler.auto_schedule_routers(
                self.plugin, mock.ANY, mock.ANY, mock.ANY)
        self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
        self.assertTrue(result)
        self.assertTrue(gs.called)
        self.assertTrue(gr.called)

    def test_auto_schedule_routers_no_agents(self):
        self.plugin.get_enabled_agent_on_host.return_value = None
        result = self.scheduler.auto_schedule_routers(
            self.plugin, mock.ANY, mock.ANY, mock.ANY)
        self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
        self.assertFalse(result)

    def test_auto_schedule_routers_no_unscheduled_routers(self):
        type(self.plugin).supported_extension_aliases = (
            mock.PropertyMock(return_value=[]))
        with mock.patch.object(self.scheduler,
                               'get_routers_to_schedule') as mock_routers:
            mock_routers.return_value = []
            result = self.scheduler.auto_schedule_routers(
                self.plugin, mock.ANY, mock.ANY, mock.ANY)
        self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
        self.assertFalse(result)

    def test_auto_schedule_routers_no_target_routers(self):
        self.plugin.get_enabled_agent_on_host.return_value = [mock.ANY]
        with contextlib.nested(
            mock.patch.object(self.scheduler, 'get_routers_to_schedule'),
            mock.patch.object(self.scheduler, 'get_routers_can_schedule')) as (
            mock_unscheduled_routers, mock_target_routers):
            mock_unscheduled_routers.return_value = mock.ANY
            mock_target_routers.return_value = None
            result = self.scheduler.auto_schedule_routers(
                self.plugin, mock.ANY, mock.ANY, mock.ANY)
        self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
        self.assertFalse(result)

    def test_get_routers_to_schedule_with_router_ids(self):
        router_ids = ['foo_router_1', 'foo_router_2']
        expected_routers = [
            {'id': 'foo_router1'}, {'id': 'foo_router_2'}
        ]
        self.plugin.get_routers.return_value = expected_routers
        with mock.patch.object(self.scheduler,
                               'filter_unscheduled_routers') as mock_filter:
            mock_filter.return_value = expected_routers
            unscheduled_routers = self.scheduler.get_routers_to_schedule(
                mock.ANY, self.plugin, router_ids)
        mock_filter.assert_called_once_with(
            mock.ANY, self.plugin, expected_routers)
        self.assertEqual(expected_routers, unscheduled_routers)

    def test_get_routers_to_schedule_without_router_ids(self):
        expected_routers = [
            {'id': 'foo_router1'}, {'id': 'foo_router_2'}
        ]
        with mock.patch.object(self.scheduler,
                               'get_unscheduled_routers') as mock_get:
            mock_get.return_value = expected_routers
            unscheduled_routers = self.scheduler.get_routers_to_schedule(
                mock.ANY, self.plugin)
        mock_get.assert_called_once_with(mock.ANY, self.plugin)
        self.assertEqual(expected_routers, unscheduled_routers)

    def test_get_routers_to_schedule_exclude_distributed(self):
        routers = [
            {'id': 'foo_router1', 'distributed': True}, {'id': 'foo_router_2'}
        ]
        expected_routers = [{'id': 'foo_router_2'}]
        with mock.patch.object(self.scheduler,
                               'get_unscheduled_routers') as mock_get:
            mock_get.return_value = routers
            unscheduled_routers = self.scheduler.get_routers_to_schedule(
                mock.ANY, self.plugin,
                router_ids=None, exclude_distributed=True)
        mock_get.assert_called_once_with(mock.ANY, self.plugin)
        self.assertEqual(expected_routers, unscheduled_routers)

    def _test_get_routers_can_schedule(self, routers, agent, target_routers):
        self.plugin.get_l3_agent_candidates.return_value = agent
        result = self.scheduler.get_routers_can_schedule(
            mock.ANY, self.plugin, routers, mock.ANY)
        self.assertEqual(target_routers, result)

    def _test_filter_unscheduled_routers(self, routers, agents, expected):
        self.plugin.get_l3_agents_hosting_routers.return_value = agents
        unscheduled_routers = self.scheduler.filter_unscheduled_routers(
            mock.ANY, self.plugin, routers)
        self.assertEqual(expected, unscheduled_routers)

    def test_filter_unscheduled_routers_already_scheduled(self):
        self._test_filter_unscheduled_routers(
            [{'id': 'foo_router1'}, {'id': 'foo_router_2'}],
            [{'id': 'foo_agent_id'}], [])

    def test_filter_unscheduled_routers_non_scheduled(self):
        self._test_filter_unscheduled_routers(
            [{'id': 'foo_router1'}, {'id': 'foo_router_2'}],
            None, [{'id': 'foo_router1'}, {'id': 'foo_router_2'}])

    def test_get_routers_can_schedule_with_compat_agent(self):
        routers = [{'id': 'foo_router'}]
        self._test_get_routers_can_schedule(routers, mock.ANY, routers)

    def test_get_routers_can_schedule_with_no_compat_agent(self):
        routers = [{'id': 'foo_router'}]
        self._test_get_routers_can_schedule(routers, None, [])

    def test_bind_routers_centralized(self):
        routers = [{'id': 'foo_router'}]
        with mock.patch.object(self.scheduler, 'bind_router') as mock_bind:
            self.scheduler.bind_routers(mock.ANY, mock.ANY, routers, mock.ANY)
        mock_bind.assert_called_once_with(mock.ANY, 'foo_router', mock.ANY)

    def _test_bind_routers_ha(self, has_binding):
        routers = [{'id': 'foo_router', 'ha': True, 'tenant_id': '42'}]
        agent = agents_db.Agent(id='foo_agent')
        with contextlib.nested(
            mock.patch.object(self.scheduler, 'router_has_binding',
                              return_value=has_binding),
            mock.patch.object(self.scheduler, 'create_ha_router_binding')) as (
                mock_has_binding, mock_bind):
            self.scheduler.bind_routers(mock.ANY, mock.ANY, routers, agent)
        mock_has_binding.assert_called_once_with(mock.ANY, 'foo_router',
                                                 'foo_agent')
        self.assertEqual(not has_binding, mock_bind.called)

    def test_bind_routers_ha_has_binding(self):
        self._test_bind_routers_ha(has_binding=True)

    def test_bind_routers_ha_no_binding(self):
        self._test_bind_routers_ha(has_binding=False)


class L3SchedulerBaseMixin(object):

    def _register_l3_agent(self, host, agent_mode='legacy', plugin=None):
        if not plugin:
            plugin = self.plugin

        agent = {
            'binary': 'neutron-l3-agent',
            'host': host,
            'topic': topics.L3_AGENT,
            'configurations': {'agent_mode': agent_mode},
            'agent_type': constants.AGENT_TYPE_L3,
            'start_flag': True
        }
        callback = agents_db.AgentExtRpcCallback()
        callback.report_state(self.adminContext,
                              agent_state={'agent_state': agent},
                              time=timeutils.strtime())
        agent_db = plugin.get_agents_db(self.adminContext,
                                        filters={'host': [agent['host']]})
        return agent_db[0]

    def _register_l3_agents(self, plugin=None):
        self.agent1 = self._register_l3_agent('host_1', plugin=plugin)
        self.agent_id1 = self.agent1.id

        self.agent2 = self._register_l3_agent('host_2', plugin=plugin)
        self.agent_id2 = self.agent2.id

    def _register_l3_dvr_agents(self):
        callback = agents_db.AgentExtRpcCallback()
        callback.report_state(self.adminContext,
                              agent_state={'agent_state': DVR_L3_AGENT},
                              time=timeutils.strtime())
        agent_db = self.plugin.get_agents_db(self.adminContext,
                                             filters={'host': [HOST_DVR]})
        self.l3_dvr_agent = agent_db[0]
        self.l3_dvr_agent_id = agent_db[0].id
        callback.report_state(self.adminContext,
                              agent_state={'agent_state': DVR_SNAT_L3_AGENT},
                              time=timeutils.strtime())
        agent_db = self.plugin.get_agents_db(self.adminContext,
                                             filters={'host': [HOST_DVR_SNAT]})
        self.l3_dvr_snat_id = agent_db[0].id
        self.l3_dvr_snat_agent = agent_db[0]

    def _set_l3_agent_admin_state(self, context, agent_id, state=True):
        update = {'agent': {'admin_state_up': state}}
        self.plugin.update_agent(context, agent_id, update)

    def _set_l3_agent_dead(self, agent_id):
        update = {
            'agent': {
                'heartbeat_timestamp':
                timeutils.utcnow() - datetime.timedelta(hours=1)}}
        self.plugin.update_agent(self.adminContext, agent_id, update)

    @contextlib.contextmanager
    def router_with_ext_gw(self, name='router1', admin_state_up=True,
                           fmt=None, tenant_id=str(uuid.uuid4()),
                           external_gateway_info=None,
                           subnet=None, set_context=False,
                           **kwargs):
        router = self._make_router(fmt or self.fmt, tenant_id, name,
                                   admin_state_up, external_gateway_info,
                                   set_context, **kwargs)
        self._add_external_gateway_to_router(
            router['router']['id'],
            subnet['subnet']['network_id'])

        yield router

        self._remove_external_gateway_from_router(
            router['router']['id'], subnet['subnet']['network_id'])
        self._delete('routers', router['router']['id'])


class L3SchedulerTestBaseMixin(object):

    def _test_add_router_to_l3_agent(self,
                                     distributed=False,
                                     already_scheduled=False,
                                     external_gw=None):
        agent_id = self.agent_id1
        agent = self.agent1
        if distributed:
            self._register_l3_dvr_agents()
            agent_id = self.l3_dvr_snat_id
            agent = self.l3_dvr_snat_agent
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        router['router']['distributed'] = distributed
        router['router']['external_gateway_info'] = external_gw
        if already_scheduled:
            self._test_schedule_bind_router(agent, router)
        with contextlib.nested(
            mock.patch.object(self, "validate_agent_router_combination"),
            mock.patch.object(self, "create_router_to_agent_binding"),
            mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                       return_value=router['router'])
        ) as (valid, auto_s, gr):
            self.add_router_to_l3_agent(self.adminContext, agent_id,
                                        router['router']['id'])
            self.assertNotEqual(already_scheduled, auto_s.called)

    def test__unbind_router_removes_binding(self):
        agent_id = self.agent_id1
        agent = self.agent1
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        self._test_schedule_bind_router(agent, router)
        self._unbind_router(self.adminContext,
                            router['router']['id'],
                            agent_id)
        bindings = self._get_l3_bindings_hosting_routers(
            self.adminContext, [router['router']['id']])
        self.assertEqual(0, len(bindings))

    def _create_router_for_l3_agent_dvr_test(self,
                                             distributed=False,
                                             external_gw=None):
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        router['router']['distributed'] = distributed
        router['router']['external_gateway_info'] = external_gw
        return router

    def _prepare_l3_agent_dvr_move_exceptions(self,
                                              distributed=False,
                                              external_gw=None,
                                              agent_id=None,
                                              expected_exception=None):
        router = self._create_router_for_l3_agent_dvr_test(
            distributed=distributed, external_gw=external_gw)
        with contextlib.nested(
            mock.patch.object(self, "create_router_to_agent_binding"),
            mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                       return_value=router['router'])):
            self.assertRaises(expected_exception,
                              self.add_router_to_l3_agent,
                              self.adminContext, agent_id,
                              router['router']['id'])

    def test_add_router_to_l3_agent_mismatch_error_dvr_to_legacy(self):
        self._register_l3_agents()
        self._prepare_l3_agent_dvr_move_exceptions(
            distributed=True,
            agent_id=self.agent_id1,
            expected_exception=l3agent.RouterL3AgentMismatch)

    def test_add_router_to_l3_agent_mismatch_error_legacy_to_dvr(self):
        self._register_l3_dvr_agents()
        self._prepare_l3_agent_dvr_move_exceptions(
            agent_id=self.l3_dvr_agent_id,
            expected_exception=l3agent.RouterL3AgentMismatch)

    def test_add_router_to_l3_agent_mismatch_error_dvr_to_dvr(self):
        self._register_l3_dvr_agents()
        self._prepare_l3_agent_dvr_move_exceptions(
            distributed=True,
            agent_id=self.l3_dvr_agent_id,
            expected_exception=l3agent.DVRL3CannotAssignToDvrAgent)

    def test_add_router_to_l3_agent_dvr_to_snat(self):
        external_gw_info = {
            "network_id": str(uuid.uuid4()),
            "enable_snat": True
        }
        self._register_l3_dvr_agents()
        agent_id = self.l3_dvr_snat_id
        agent = self.l3_dvr_snat_agent
        router = self._create_router_for_l3_agent_dvr_test(
            distributed=True,
            external_gw=external_gw_info)
        with contextlib.nested(
            mock.patch.object(self, "validate_agent_router_combination"),
            mock.patch.object(self, "create_router_to_agent_binding"),
            mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                       return_value=router['router'])
        ) as (valid_agent_rtr, rtr_agent_binding, get_rtr):

            self.add_router_to_l3_agent(self.adminContext, agent_id,
                                        router['router']['id'])
            rtr_agent_binding.assert_called_once_with(
                self.adminContext, agent, router['router'])

    def test_add_router_to_l3_agent(self):
        self._test_add_router_to_l3_agent()

    def test_add_distributed_router_to_l3_agent(self):
        external_gw_info = {
            "network_id": str(uuid.uuid4()),
            "enable_snat": True
        }
        self._test_add_router_to_l3_agent(distributed=True,
                                          external_gw=external_gw_info)

    def test_add_router_to_l3_agent_already_scheduled(self):
        self._test_add_router_to_l3_agent(already_scheduled=True)

    def test_add_distributed_router_to_l3_agent_already_scheduled(self):
        external_gw_info = {
            "network_id": str(uuid.uuid4()),
            "enable_snat": True
        }
        self._test_add_router_to_l3_agent(distributed=True,
                                          already_scheduled=True,
                                          external_gw=external_gw_info)

    def _prepare_schedule_dvr_tests(self):
        scheduler = l3_agent_scheduler.ChanceScheduler()
        agent = agents_db.Agent()
        agent.admin_state_up = True
        agent.heartbeat_timestamp = timeutils.utcnow()
        plugin = mock.Mock()
        plugin.get_l3_agents_hosting_routers.return_value = []
        plugin.get_l3_agents.return_value = [agent]
        plugin.get_l3_agent_candidates.return_value = [agent]

        return scheduler, agent, plugin

    def test_schedule_dvr_router_without_snatbinding_and_no_gw(self):
        scheduler, agent, plugin = self._prepare_schedule_dvr_tests()
        sync_router = {
            'id': 'foo_router_id',
            'distributed': True
        }
        plugin.get_router.return_value = sync_router
        with contextlib.nested(
            mock.patch.object(scheduler, 'bind_router'),
            mock.patch.object(
                plugin, 'get_snat_bindings', return_value=False)
        ):
                scheduler._schedule_router(
                    plugin, self.adminContext, 'foo_router_id', None)
        expected_calls = [
            mock.call.get_router(mock.ANY, 'foo_router_id'),
            mock.call.get_l3_agents_hosting_routers(
                mock.ANY, ['foo_router_id'], admin_state_up=True),
            mock.call.get_l3_agents(mock.ANY, active=True),
            mock.call.get_l3_agent_candidates(mock.ANY, sync_router, [agent]),
        ]
        plugin.assert_has_calls(expected_calls)

    def test_schedule_dvr_router_with_snatbinding_no_gw(self):
        scheduler, agent, plugin = self._prepare_schedule_dvr_tests()
        sync_router = {'id': 'foo_router_id',
                       'distributed': True}
        plugin.get_router.return_value = sync_router
        with mock.patch.object(plugin, 'get_snat_bindings', return_value=True):
                scheduler._schedule_router(
                    plugin, self.adminContext, 'foo_router_id', None)
        expected_calls = [
            mock.call.get_router(mock.ANY, 'foo_router_id'),
            mock.call.unbind_snat_servicenode(mock.ANY, 'foo_router_id'),
        ]
        plugin.assert_has_calls(expected_calls)

    def test_schedule_router_distributed(self):
        scheduler, agent, plugin = self._prepare_schedule_dvr_tests()
        sync_router = {
            'id': 'foo_router_id',
            'distributed': True,
            'external_gateway_info': {
                'network_id': str(uuid.uuid4()),
                'enable_snat': True
            }
        }
        plugin.get_router.return_value = sync_router
        with mock.patch.object(
            plugin, 'get_snat_bindings', return_value=False):
                scheduler._schedule_router(
                    plugin, self.adminContext, 'foo_router_id', None)
        expected_calls = [
            mock.call.get_router(mock.ANY, 'foo_router_id'),
            mock.call.schedule_snat_router(
                mock.ANY, 'foo_router_id', sync_router),
        ]
        plugin.assert_has_calls(expected_calls)

    def _test_schedule_bind_router(self, agent, router):
        ctx = self.adminContext
        session = ctx.session
        db = l3_agentschedulers_db.RouterL3AgentBinding
        scheduler = l3_agent_scheduler.ChanceScheduler()

        rid = router['router']['id']
        scheduler.bind_router(ctx, rid, agent)
        results = (session.query(db).filter_by(router_id=rid).all())
        self.assertTrue(len(results) > 0)
        self.assertIn(agent.id, [bind.l3_agent_id for bind in results])

    def test_bind_new_router(self):
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        with mock.patch.object(l3_agent_scheduler.LOG, 'debug') as flog:
            self._test_schedule_bind_router(self.agent1, router)
            self.assertEqual(1, flog.call_count)
            args, kwargs = flog.call_args
            self.assertIn('is scheduled', args[0])

    def test_bind_absent_router(self):
        scheduler = l3_agent_scheduler.ChanceScheduler()
        # checking that bind_router() is not throwing
        # when supplied with router_id of non-existing router
        scheduler.bind_router(self.adminContext, "dummyID", self.agent1)

    def test_bind_existing_router(self):
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        self._test_schedule_bind_router(self.agent1, router)
        with mock.patch.object(l3_agent_scheduler.LOG, 'debug') as flog:
            self._test_schedule_bind_router(self.agent1, router)
            self.assertEqual(1, flog.call_count)
            args, kwargs = flog.call_args
            self.assertIn('has already been scheduled', args[0])

    def _check_get_l3_agent_candidates(
            self, router, agent_list, exp_host, count=1):
        candidates = self.get_l3_agent_candidates(self.adminContext,
                                                  router, agent_list)
        self.assertEqual(len(candidates), count)
        if count:
            self.assertEqual(candidates[0]['host'], exp_host)

    def test_get_l3_agent_candidates_legacy(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        agent_list = [self.agent1, self.l3_dvr_agent]

        # test legacy agent_mode case: only legacy agent should be candidate
        router['distributed'] = False
        exp_host = 'host_1'
        self._check_get_l3_agent_candidates(router, agent_list, exp_host)

    def test_get_l3_agent_candidates_dvr(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        agent_list = [self.agent1, self.l3_dvr_agent]
        # test dvr agent_mode case only dvr agent should be candidate
        router['distributed'] = True
        exp_host = DVR_L3_AGENT.get('host')
        self.check_ports_exist_on_l3agent = mock.Mock(return_value=True)
        self._check_get_l3_agent_candidates(router, agent_list, exp_host)

    def test_get_l3_agent_candidates_dvr_no_vms(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        agent_list = [self.agent1, self.l3_dvr_agent]
        exp_host = DVR_L3_AGENT.get('host')
        router['distributed'] = True
        # Test no VMs present case
        self.check_ports_exist_on_l3agent = mock.Mock(return_value=False)
        self._check_get_l3_agent_candidates(
            router, agent_list, exp_host, count=0)

    def test_get_l3_agent_candidates_dvr_snat(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        router['distributed'] = True

        agent_list = [self.l3_dvr_snat_agent]
        exp_host = DVR_SNAT_L3_AGENT.get('host')
        self.check_ports_exist_on_l3agent = mock.Mock(return_value=True)
        self._check_get_l3_agent_candidates(router, agent_list, exp_host)

    def test_get_l3_agent_candidates_dvr_snat_no_vms(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        router['distributed'] = True

        agent_list = [self.l3_dvr_snat_agent]
        exp_host = DVR_SNAT_L3_AGENT.get('host')
        self.check_ports_exist_on_l3agent = mock.Mock(return_value=False)
        # Test no VMs present case
        self.check_ports_exist_on_l3agent.return_value = False
        self._check_get_l3_agent_candidates(
            router, agent_list, exp_host, count=0)

    def test_get_l3_agent_candidates_centralized(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        # check centralized test case
        router['distributed'] = False
        exp_host = DVR_SNAT_L3_AGENT.get('host')
        agent_list = [self.l3_dvr_snat_agent]
        self._check_get_l3_agent_candidates(router, agent_list, exp_host)

    def _prepare_check_ports_exist_tests(self):
        l3_agent = agents_db.Agent()
        l3_agent.admin_state_up = True
        l3_agent.host = 'host_1'
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        self.plugin.get_ports = mock.Mock(return_value=[])
        self.get_subnet_ids_on_router = mock.Mock(return_value=[])
        return l3_agent, router

    def test_check_ports_exist_on_l3agent_no_subnets(self):
        l3_agent, router = self._prepare_check_ports_exist_tests()
        # no subnets
        val = self.check_ports_exist_on_l3agent(self.adminContext,
                                                l3_agent, router['id'])
        self.assertFalse(val)

    def test_check_ports_exist_on_l3agent_with_dhcp_enabled_subnets(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        router['distributed'] = True

        agent_list = [self.l3_dvr_snat_agent]
        subnet = {'id': str(uuid.uuid4()),
                  'enable_dhcp': True}

        self.get_subnet_ids_on_router = mock.Mock(
            return_value=[subnet['id']])

        self.plugin.get_subnet = mock.Mock(return_value=subnet)
        self.plugin.get_ports = mock.Mock()
        val = self.check_ports_exist_on_l3agent(
            self.adminContext, agent_list[0], router['id'])
        self.assertTrue(val)
        self.assertFalse(self.plugin.get_ports.called)

    def test_check_ports_exist_on_l3agent_if_no_subnets_then_return(self):
        l3_agent, router = self._prepare_check_ports_exist_tests()
        with mock.patch.object(manager.NeutronManager,
                               'get_plugin') as getp:
            getp.return_value = self.plugin
            # no subnets and operation is remove_router_interface,
            # so return immediately without calling get_ports
            self.check_ports_exist_on_l3agent(self.adminContext,
                                          l3_agent, router['id'])
        self.assertFalse(self.plugin.get_ports.called)

    def test_check_ports_exist_on_l3agent_no_subnet_match(self):
        l3_agent, router = self._prepare_check_ports_exist_tests()
        # no matching subnet
        self.plugin.get_subnet_ids_on_router = mock.Mock(
            return_value=[str(uuid.uuid4())])
        val = self.check_ports_exist_on_l3agent(self.adminContext,
                                                l3_agent, router['id'])
        self.assertFalse(val)

    def test_check_ports_exist_on_l3agent_subnet_match(self):
        l3_agent, router = self._prepare_check_ports_exist_tests()
        # matching subnet
        port = {'subnet_id': str(uuid.uuid4()),
                'binding:host_id': 'host_1',
                'device_owner': 'compute:',
                'id': 1234}
        subnet = {'id': str(uuid.uuid4()),
                  'enable_dhcp': False}
        self.plugin.get_ports.return_value = [port]
        self.get_subnet_ids_on_router = mock.Mock(
            return_value=[port['subnet_id']])
        self.plugin.get_subnet = mock.Mock(return_value=subnet)
        val = self.check_ports_exist_on_l3agent(self.adminContext,
                                                l3_agent, router['id'])
        self.assertTrue(val)

    def test_get_l3_agents_hosting_routers(self):
        agent = self._register_l3_agent('host_6')
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        ctx = self.adminContext
        router_id = router['router']['id']
        self.plugin.router_scheduler.bind_router(ctx, router_id, agent)
        agents = self.get_l3_agents_hosting_routers(ctx,
                                                    [router_id])
        self.assertEqual([agent.id], [agt.id for agt in agents])
        agents = self.get_l3_agents_hosting_routers(ctx,
                                                    [router_id],
                                                    admin_state_up=True)
        self.assertEqual([agent.id], [agt.id for agt in agents])

        self._set_l3_agent_admin_state(ctx, agent.id, False)
        agents = self.get_l3_agents_hosting_routers(ctx,
                                                    [router_id])
        self.assertEqual([agent.id], [agt.id for agt in agents])
        agents = self.get_l3_agents_hosting_routers(ctx,
                                                    [router_id],
                                                    admin_state_up=True)
        self.assertEqual([], agents)


class L3SchedulerTestCase(l3_agentschedulers_db.L3AgentSchedulerDbMixin,
                          l3_db.L3_NAT_db_mixin,
                          common_db_mixin.CommonDbMixin,
                          test_db_base_plugin_v2.NeutronDbPluginV2TestCase,
                          test_l3.L3NatTestCaseMixin,
                          L3SchedulerBaseMixin,
                          L3SchedulerTestBaseMixin):

    def setUp(self):
        self.mock_rescheduling = False
        ext_mgr = test_l3.L3TestExtensionManager()
        plugin_str = ('neutron.tests.unit.extensions.test_l3.'
                      'TestL3NatIntAgentSchedulingPlugin')
        super(L3SchedulerTestCase, self).setUp(plugin=plugin_str,
                                               ext_mgr=ext_mgr)

        self.adminContext = q_context.get_admin_context()
        self.plugin = manager.NeutronManager.get_plugin()
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.ChanceScheduler'
        )
        self._register_l3_agents()


class L3AgentChanceSchedulerTestCase(L3SchedulerTestCase):

    def test_random_scheduling(self):
        random_patch = mock.patch('random.choice')
        random_mock = random_patch.start()

        def side_effect(seq):
            return seq[0]
        random_mock.side_effect = side_effect

        with self.subnet() as subnet:
            self._set_net_external(subnet['subnet']['network_id'])
            with self.router_with_ext_gw(name='r1', subnet=subnet) as r1:
                agents = self.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)

                self.assertEqual(len(agents), 1)
                self.assertEqual(random_mock.call_count, 1)

                with self.router_with_ext_gw(name='r2', subnet=subnet) as r2:
                    agents = self.get_l3_agents_hosting_routers(
                        self.adminContext, [r2['router']['id']],
                        admin_state_up=True)

                    self.assertEqual(len(agents), 1)
                    self.assertEqual(random_mock.call_count, 2)

        random_patch.stop()

    def test_scheduler_auto_schedule_when_agent_added(self):
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id1, False)
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id2, False)

        with self.subnet() as subnet:
            self._set_net_external(subnet['subnet']['network_id'])
            with self.router_with_ext_gw(name='r1', subnet=subnet) as r1:
                agents = self.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)
                self.assertEqual(0, len(agents))

                self._set_l3_agent_admin_state(self.adminContext,
                                               self.agent_id1, True)
                self.plugin.auto_schedule_routers(self.adminContext,
                                                  'host_1',
                                                  [r1['router']['id']])

                agents = self.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)
                self.assertEqual('host_1', agents[0]['host'])


class L3AgentLeastRoutersSchedulerTestCase(L3SchedulerTestCase):
    def setUp(self):
        super(L3AgentLeastRoutersSchedulerTestCase, self).setUp()
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.LeastRoutersScheduler'
        )

    def test_scheduler(self):
        # disable one agent to force the scheduling to the only one.
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id2, False)

        with self.subnet() as subnet:
            self._set_net_external(subnet['subnet']['network_id'])
            with self.router_with_ext_gw(name='r1', subnet=subnet) as r1:
                agents = self.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)
                self.assertEqual(len(agents), 1)

                agent_id1 = agents[0]['id']

                with self.router_with_ext_gw(name='r2', subnet=subnet) as r2:
                    agents = self.get_l3_agents_hosting_routers(
                        self.adminContext, [r2['router']['id']],
                        admin_state_up=True)
                    self.assertEqual(len(agents), 1)

                    agent_id2 = agents[0]['id']

                    self.assertEqual(agent_id1, agent_id2)

                    # re-enable the second agent to see whether the next router
                    # spawned will be on this one.
                    self._set_l3_agent_admin_state(self.adminContext,
                                                   self.agent_id2, True)

                    with self.router_with_ext_gw(name='r3',
                                                 subnet=subnet) as r3:
                        agents = self.get_l3_agents_hosting_routers(
                            self.adminContext, [r3['router']['id']],
                            admin_state_up=True)
                        self.assertEqual(len(agents), 1)

                        agent_id3 = agents[0]['id']

                        self.assertNotEqual(agent_id1, agent_id3)


class L3DvrScheduler(l3_db.L3_NAT_db_mixin,
                     l3_dvrscheduler_db.L3_DVRsch_db_mixin):
    pass


class L3DvrSchedulerTestCase(testlib_api.SqlTestCase):

    def setUp(self):
        plugin = 'neutron.plugins.ml2.plugin.Ml2Plugin'
        self.setup_coreplugin(plugin)
        super(L3DvrSchedulerTestCase, self).setUp()
        self.adminContext = q_context.get_admin_context()
        self.dut = L3DvrScheduler()

    def test__notify_port_delete(self):
        plugin = manager.NeutronManager.get_plugin()
        l3plugin = mock.Mock()
        l3plugin.supported_extension_aliases = [
            'router', constants.L3_AGENT_SCHEDULER_EXT_ALIAS,
            constants.L3_DISTRIBUTED_EXT_ALIAS
        ]
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            kwargs = {
                'context': self.adminContext,
                'port': mock.ANY,
                'removed_routers': [
                    {'agent_id': 'foo_agent', 'router_id': 'foo_id'},
                ],
            }
            l3_dvrscheduler_db._notify_port_delete(
                'port', 'after_delete', plugin, **kwargs)
            l3plugin.dvr_vmarp_table_update.assert_called_once_with(
                self.adminContext, mock.ANY, 'del')
            l3plugin.remove_router_from_l3_agent.assert_called_once_with(
                self.adminContext, 'foo_agent', 'foo_id')

    def test_dvr_update_router_addvm(self):
        port = {
                'device_id': 'abcd',
                'device_owner': 'compute:nova',
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.3'
                    }
                ]
        }
        dvr_port = {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'device_owner': 'network:router_interface_distributed',
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.1'
                    }
                ]
        }
        r1 = {
              'id': 'r1',
              'distributed': True,
        }

        with contextlib.nested(
            mock.patch('neutron.db.db_base_plugin_v2.NeutronDbPluginV2'
                       '.get_ports', return_value=[dvr_port]),
            mock.patch('neutron.manager.NeutronManager.get_service_plugins',
                       return_value=mock.Mock()),
            mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                       return_value=r1),
            mock.patch('neutron.api.rpc.agentnotifiers.l3_rpc_agent_api'
                       '.L3AgentNotifyAPI')):
            self.dut.dvr_update_router_addvm(self.adminContext, port)

    def test_get_dvr_routers_by_portid(self):
        dvr_port = {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'device_owner': 'network:router_interface_distributed',
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.1'
                    }
                ]
        }
        r1 = {
              'id': 'r1',
              'distributed': True,
        }

        with contextlib.nested(
            mock.patch('neutron.db.db_base_plugin_v2.NeutronDbPluginV2'
                       '.get_port', return_value=dvr_port),
            mock.patch('neutron.db.db_base_plugin_v2.NeutronDbPluginV2'
                       '.get_ports', return_value=[dvr_port])):
            router_id = self.dut.get_dvr_routers_by_portid(self.adminContext,
                                                           dvr_port['id'])
            self.assertEqual(router_id.pop(), r1['id'])

    def test_get_subnet_ids_on_router(self):
        dvr_port = {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'device_owner': 'network:router_interface_distributed',
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.1'
                    }
                ]
        }
        r1 = {
              'id': 'r1',
              'distributed': True,
        }

        with contextlib.nested(
            mock.patch('neutron.db.db_base_plugin_v2.NeutronDbPluginV2'
                       '.get_ports', return_value=[dvr_port])):
            sub_ids = self.dut.get_subnet_ids_on_router(self.adminContext,
                                                        r1['id'])
            self.assertEqual(sub_ids.pop(),
                            dvr_port.get('fixed_ips').pop(0).get('subnet_id'))

    def test_check_ports_active_on_host_and_subnet(self):
        dvr_port = {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'status': 'ACTIVE',
                'binding:host_id': 'thisHost',
                'device_owner': 'compute:nova',
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.1'
                    }
                ]
        }
        r1 = {
              'id': 'r1',
              'distributed': True,
        }
        with contextlib.nested(
            mock.patch('neutron.db.db_base_plugin_v2.NeutronDbPluginV2'
                       '.get_ports', return_value=[dvr_port]),
            mock.patch('neutron.manager.NeutronManager.get_service_plugins',
                       return_value=mock.Mock()),
            mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                       return_value=r1),
            mock.patch('neutron.api.rpc.agentnotifiers.l3_rpc_agent_api'
                       '.L3AgentNotifyAPI')):
            sub_ids = self.dut.get_subnet_ids_on_router(self.adminContext,
                                                        r1['id'])
            result = self.dut.check_ports_active_on_host_and_subnet(
                                                    self.adminContext,
                                                    'thisHost', 'dvr_port1',
                                                    sub_ids)
            self.assertFalse(result)

    def _test_dvr_serviced_port_exists_on_subnet(self, port):
        with mock.patch('neutron.db.db_base_plugin_v2.NeutronDbPluginV2.'
                        'get_ports', return_value=[port]):
            result = self.dut.check_ports_active_on_host_and_subnet(
                                                    self.adminContext,
                                                    'thisHost',
                                                    'dvr1-intf-id',
                                                    'my-subnet-id')
            self.assertTrue(result)

    def test_dvr_serviced_vip_port_exists_on_subnet(self):
        vip_port = {
                'id': 'lbaas-vip-port1',
                'device_id': 'vip-pool-id',
                'status': 'ACTIVE',
                'binding:host_id': 'thisHost',
                'device_owner': constants.DEVICE_OWNER_LOADBALANCER,
                'fixed_ips': [
                    {
                        'subnet_id': 'my-subnet-id',
                        'ip_address': '10.10.10.1'
                    }
                ]
        }
        self._test_dvr_serviced_port_exists_on_subnet(port=vip_port)

    def test_dvr_serviced_dhcp_port_exists_on_subnet(self):
        dhcp_port = {
                'id': 'dhcp-port1',
                'device_id': 'dhcp-net-id',
                'status': 'ACTIVE',
                'binding:host_id': 'thisHost',
                'device_owner': constants.DEVICE_OWNER_DHCP,
                'fixed_ips': [
                    {
                        'subnet_id': 'my-subnet-id',
                        'ip_address': '10.10.10.2'
                    }
                ]
        }
        self._test_dvr_serviced_port_exists_on_subnet(port=dhcp_port)

    def _prepare_schedule_snat_tests(self):
        agent = agents_db.Agent()
        agent.admin_state_up = True
        agent.heartbeat_timestamp = timeutils.utcnow()
        router = {
            'id': 'foo_router_id',
            'distributed': True,
            'external_gateway_info': {
                'network_id': str(uuid.uuid4()),
                'enable_snat': True
            }
        }
        return agent, router

    def test_schedule_snat_router_duplicate_entry(self):
        self._prepare_schedule_snat_tests()
        with contextlib.nested(
            mock.patch.object(self.dut, 'get_l3_agents'),
            mock.patch.object(self.dut, 'get_snat_candidates'),
            mock.patch.object(self.dut, 'bind_snat_servicenode',
                              side_effect=db_exc.DBDuplicateEntry()),
            mock.patch.object(self.dut, 'bind_dvr_router_servicenode')
        ) as (mock_gl3, mock_snat_canidates, mock_bind_snat, mock_bind_dvr):
            self.dut.schedule_snat_router(self.adminContext, 'foo', 'bar')
        self.assertTrue(mock_bind_snat.called)
        self.assertFalse(mock_bind_dvr.called)

    def test_schedule_snat_router_return_value(self):
        agent, router = self._prepare_schedule_snat_tests()
        with contextlib.nested(
            mock.patch.object(self.dut, 'get_l3_agents'),
            mock.patch.object(self.dut, 'get_snat_candidates'),
            mock.patch.object(self.dut, 'bind_snat_servicenode'),
            mock.patch.object(self.dut, 'bind_dvr_router_servicenode')
        ) as (mock_gl3, mock_snat_canidates, mock_bind_snat, mock_bind_dvr):
            mock_snat_canidates.return_value = [agent]
            mock_bind_snat.return_value = [agent]
            mock_bind_dvr.return_value = [agent]
            chosen_agent = self.dut.schedule_snat_router(
                self.adminContext, 'foo_router_id', router)
        self.assertEqual(chosen_agent, [agent])

    def test_schedule_router_unbind_snat_servicenode_negativetest(self):
        router = {
            'id': 'foo_router_id',
            'distributed': True
        }
        with contextlib.nested(
            mock.patch.object(self.dut, 'get_router'),
            mock.patch.object(self.dut, 'get_snat_bindings'),
            mock.patch.object(self.dut, 'unbind_snat_servicenode')
        ) as (mock_rd, mock_snat_bind, mock_unbind):
            mock_rd.return_value = router
            mock_snat_bind.return_value = False
            self.dut.schedule_snat_router(
                self.adminContext, 'foo_router_id', router)
        self.assertFalse(mock_unbind.called)

    def test_schedule_snat_router_with_snat_candidates(self):
        agent, router = self._prepare_schedule_snat_tests()
        with contextlib.nested(
            mock.patch.object(query.Query, 'first'),
            mock.patch.object(self.dut, 'get_l3_agents'),
            mock.patch.object(self.dut, 'get_snat_candidates'),
            mock.patch.object(self.dut, 'get_router'),
            mock.patch.object(self.dut, 'bind_dvr_router_servicenode'),
            mock.patch.object(self.dut, 'bind_snat_servicenode')) as (
                mock_query, mock_agents,
                mock_candidates, mock_rd, mock_dvr, mock_bind):
            mock_rd.return_value = router
            mock_query.return_value = []
            mock_agents.return_value = [agent]
            mock_candidates.return_value = [agent]
            self.dut.schedule_snat_router(
                self.adminContext, 'foo_router_id', mock.ANY)
        mock_bind.assert_called_once_with(
            self.adminContext, 'foo_router_id', [agent])

    def test_unbind_snat_servicenode(self):
        router_id = 'foo_router_id'
        core_plugin = mock.PropertyMock()
        type(self.dut)._core_plugin = core_plugin
        (self.dut._core_plugin.get_ports_on_host_by_subnet.
         return_value) = []
        core_plugin.reset_mock()
        l3_notifier = mock.PropertyMock()
        type(self.dut).l3_rpc_notifier = l3_notifier
        binding = l3_dvrscheduler_db.CentralizedSnatL3AgentBinding(
            router_id=router_id, l3_agent_id='foo_l3_agent_id',
            l3_agent=agents_db.Agent())
        with contextlib.nested(
            mock.patch.object(query.Query, 'one'),
            mock.patch.object(self.adminContext.session, 'delete'),
            mock.patch.object(query.Query, 'delete'),
            mock.patch.object(self.dut, 'get_subnet_ids_on_router')) as (
                mock_query, mock_session, mock_delete, mock_get_subnets):
            mock_query.return_value = binding
            mock_get_subnets.return_value = ['foo_subnet_id']
            self.dut.unbind_snat_servicenode(self.adminContext, router_id)
        mock_get_subnets.assert_called_with(self.adminContext, router_id)
        self.assertTrue(mock_session.call_count)
        self.assertTrue(mock_delete.call_count)
        core_plugin.assert_called_once_with()
        l3_notifier.assert_called_once_with()


class L3HAPlugin(db_v2.NeutronDbPluginV2,
                 l3_hamode_db.L3_HA_NAT_db_mixin,
                 l3_hascheduler_db.L3_HA_scheduler_db_mixin):
    supported_extension_aliases = ["l3-ha"]


class L3HATestCaseMixin(testlib_api.SqlTestCase,
                        L3SchedulerBaseMixin):

    def setUp(self):
        super(L3HATestCaseMixin, self).setUp()

        self.adminContext = q_context.get_admin_context()
        self.plugin = L3HAPlugin()

        self.setup_coreplugin('neutron.plugins.ml2.plugin.Ml2Plugin')
        mock.patch.object(l3_hamode_db.L3_HA_NAT_db_mixin,
                          '_notify_ha_interfaces_updated').start()

        cfg.CONF.set_override('max_l3_agents_per_router', 0)
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.ChanceScheduler'
        )

        self._register_l3_agents()

    def _create_ha_router(self, ha=True, tenant_id='tenant1'):
        self.adminContext.tenant_id = tenant_id
        router = {'name': 'router1', 'admin_state_up': True}
        if ha is not None:
            router['ha'] = ha
        return self.plugin.create_router(self.adminContext,
                                         {'router': router})


class L3_HA_scheduler_db_mixinTestCase(L3HATestCaseMixin):

    def _register_l3_agents(self, plugin=None):
        super(L3_HA_scheduler_db_mixinTestCase,
              self)._register_l3_agents(plugin=plugin)

        self.agent3 = self._register_l3_agent('host_3', plugin=plugin)
        self.agent_id3 = self.agent3.id

        self.agent4 = self._register_l3_agent('host_4', plugin=plugin)
        self.agent_id4 = self.agent4.id

    def test_get_ha_routers_l3_agents_count(self):
        router1 = self._create_ha_router()
        router2 = self._create_ha_router()
        router3 = self._create_ha_router(ha=False)
        self.plugin.schedule_router(self.adminContext, router1['id'])
        self.plugin.schedule_router(self.adminContext, router2['id'])
        self.plugin.schedule_router(self.adminContext, router3['id'])
        result = self.plugin.get_ha_routers_l3_agents_count(
            self.adminContext).all()

        self.assertEqual(2, len(result))
        self.assertIn((router1['id'], router1['tenant_id'], 4), result)
        self.assertIn((router2['id'], router2['tenant_id'], 4), result)
        self.assertNotIn((router3['id'], router3['tenant_id'], mock.ANY),
                         result)

    def test_get_ordered_l3_agents_by_num_routers(self):
        router1 = self._create_ha_router()
        router2 = self._create_ha_router()
        router3 = self._create_ha_router(ha=False)
        router4 = self._create_ha_router(ha=False)

        # Agent 1 will host 0 routers, agent 2 will host 1, agent 3 will
        # host 2, and agent 4 will host 3.
        self.plugin.schedule_router(self.adminContext, router1['id'],
                                    candidates=[self.agent2, self.agent4])
        self.plugin.schedule_router(self.adminContext, router2['id'],
                                    candidates=[self.agent3, self.agent4])
        self.plugin.schedule_router(self.adminContext, router3['id'],
                                    candidates=[self.agent3])
        self.plugin.schedule_router(self.adminContext, router4['id'],
                                    candidates=[self.agent4])

        agent_ids = [self.agent_id1, self.agent_id2, self.agent_id3,
                     self.agent_id4]
        result = self.plugin.get_l3_agents_ordered_by_num_routers(
            self.adminContext, agent_ids)

        self.assertEqual(agent_ids, [record['id'] for record in result])


class L3AgentSchedulerDbMixinTestCase(L3HATestCaseMixin):

    def test_reschedule_ha_routers_from_down_agents(self):
        router = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, router['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))
        self._set_l3_agent_dead(self.agent_id1)
        with mock.patch.object(self.plugin, 'reschedule_router') as reschedule:
            self.plugin.reschedule_routers_from_down_agents()
            self.assertFalse(reschedule.called)

    def test_list_l3_agents_hosting_ha_router(self):
        router = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, router['id'])

        agents = self.plugin.list_l3_agents_hosting_router(
            self.adminContext, router['id'])['agents']
        for agent in agents:
            self.assertEqual('standby', agent['ha_state'])

        self.plugin.update_routers_states(
            self.adminContext, {router['id']: 'active'}, self.agent1.host)
        agents = self.plugin.list_l3_agents_hosting_router(
            self.adminContext, router['id'])['agents']
        for agent in agents:
            expected_state = ('active' if agent['host'] == self.agent1.host
                              else 'standby')
            self.assertEqual(expected_state, agent['ha_state'])

    def test_list_l3_agents_hosting_legacy_router(self):
        router = self._create_ha_router(ha=False)
        self.plugin.schedule_router(self.adminContext, router['id'])

        agents = self.plugin.list_l3_agents_hosting_router(
            self.adminContext, router['id'])['agents']
        for agent in agents:
            self.assertIsNone(agent['ha_state'])

    def test_get_agents_dict_for_router_unscheduled_returns_empty_list(self):
        self.assertEqual({'agents': []},
                         self.plugin._get_agents_dict_for_router([]))


class L3HAChanceSchedulerTestCase(L3HATestCaseMixin):

    def test_scheduler_with_ha_enabled(self):
        router = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, router['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))

        for agent in agents:
            sync_data = self.plugin.get_ha_sync_data_for_host(
                self.adminContext, router_ids=[router['id']],
                host=agent.host)
            self.assertEqual(1, len(sync_data))
            interface = sync_data[0][constants.HA_INTERFACE_KEY]
            self.assertIsNotNone(interface)

    def test_auto_schedule(self):
        router = self._create_ha_router()
        self.plugin.auto_schedule_routers(
            self.adminContext, self.agent1.host, None)
        self.plugin.auto_schedule_routers(
            self.adminContext, self.agent2.host, None)
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']])
        self.assertEqual(2, len(agents))

    def test_auto_schedule_specific_router_when_agent_added(self):
        self._auto_schedule_when_agent_added(True)

    def test_auto_schedule_all_routers_when_agent_added(self):
        self._auto_schedule_when_agent_added(False)

    def _auto_schedule_when_agent_added(self, specific_router):
        router = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, router['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))
        agent_ids = [agent['id'] for agent in agents]
        self.assertIn(self.agent_id1, agent_ids)
        self.assertIn(self.agent_id2, agent_ids)

        agent = self._register_l3_agent('host_3')
        self.agent_id3 = agent.id
        routers_to_auto_schedule = [router['id']] if specific_router else []
        self.plugin.auto_schedule_routers(self.adminContext,
                                          'host_3',
                                          routers_to_auto_schedule)

        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        self.assertEqual(3, len(agents))

        # Simulate agent restart to make sure we don't try to re-bind
        self.plugin.auto_schedule_routers(self.adminContext,
                                          'host_3',
                                          routers_to_auto_schedule)

    def test_scheduler_with_ha_enabled_not_enough_agent(self):
        r1 = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, r1['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))

        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id2, False)

        r2 = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, r2['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r2['id']],
            admin_state_up=True)
        self.assertEqual(0, len(agents))

        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id2, True)


class L3HALeastRoutersSchedulerTestCase(L3HATestCaseMixin):

    def _register_l3_agents(self, plugin=None):
        super(L3HALeastRoutersSchedulerTestCase,
              self)._register_l3_agents(plugin=plugin)

        agent = self._register_l3_agent('host_3', plugin=plugin)
        self.agent_id3 = agent.id

        agent = self._register_l3_agent('host_4', plugin=plugin)
        self.agent_id4 = agent.id

    def setUp(self):
        super(L3HALeastRoutersSchedulerTestCase, self).setUp()
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.LeastRoutersScheduler'
        )

    def test_scheduler(self):
        cfg.CONF.set_override('max_l3_agents_per_router', 2)

        # disable the third agent to be sure that the router will
        # be scheduled of the two firsts
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id3, False)
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id4, False)

        r1 = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, r1['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))
        agent_ids = [agent['id'] for agent in agents]
        self.assertIn(self.agent_id1, agent_ids)
        self.assertIn(self.agent_id2, agent_ids)

        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id3, True)
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id4, True)

        r2 = self._create_ha_router()
        self.plugin.schedule_router(self.adminContext, r2['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r2['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))
        agent_ids = [agent['id'] for agent in agents]
        self.assertIn(self.agent_id3, agent_ids)
        self.assertIn(self.agent_id4, agent_ids)


class TestGetL3AgentsWithAgentModeFilter(testlib_api.SqlTestCase,
                                         L3SchedulerBaseMixin):
    """Test cases to test get_l3_agents.

    This class tests the L3AgentSchedulerDbMixin.get_l3_agents()
    for the 'agent_mode' filter with various values.

    5 l3 agents are registered in the order - legacy, dvr_snat, dvr, fake_mode
    and legacy
    """

    scenarios = [
        ('no filter',
            dict(agent_modes=[],
                 expected_agent_modes=['legacy', 'dvr_snat', 'dvr',
                                       'fake_mode', 'legacy'])),

        ('legacy',
            dict(agent_modes=['legacy'],
                 expected_agent_modes=['legacy', 'legacy'])),

        ('dvr_snat',
            dict(agent_modes=['dvr_snat'],
                 expected_agent_modes=['dvr_snat'])),

        ('dvr ',
            dict(agent_modes=['dvr'],
                 expected_agent_modes=['dvr'])),

        ('legacy and dvr snat',
            dict(agent_modes=['legacy', 'dvr_snat', 'legacy'],
                 expected_agent_modes=['legacy', 'dvr_snat', 'legacy'])),

        ('legacy and dvr',
            dict(agent_modes=['legacy', 'dvr'],
                 expected_agent_modes=['legacy', 'dvr', 'legacy'])),

        ('dvr_snat and dvr',
            dict(agent_modes=['dvr_snat', 'dvr'],
                 expected_agent_modes=['dvr_snat', 'dvr'])),

        ('legacy, dvr_snat and dvr',
            dict(agent_modes=['legacy', 'dvr_snat', 'dvr'],
                 expected_agent_modes=['legacy', 'dvr_snat', 'dvr',
                                       'legacy'])),

        ('invalid',
            dict(agent_modes=['invalid'],
                 expected_agent_modes=[])),
    ]

    def setUp(self):
        super(TestGetL3AgentsWithAgentModeFilter, self).setUp()
        self.plugin = L3HAPlugin()
        self.setup_coreplugin('neutron.plugins.ml2.plugin.Ml2Plugin')
        self.adminContext = q_context.get_admin_context()
        hosts = ['host_1', 'host_2', 'host_3', 'host_4', 'host_5']
        agent_modes = ['legacy', 'dvr_snat', 'dvr', 'fake_mode', 'legacy']
        for host, agent_mode in zip(hosts, agent_modes):
            self._register_l3_agent(host, agent_mode, self.plugin)

    def _get_agent_mode(self, agent):
        agent_conf = self.plugin.get_configuration_dict(agent)
        return agent_conf.get('agent_mode', 'None')

    def test_get_l3_agents(self):
        l3_agents = self.plugin.get_l3_agents(
            self.adminContext, filters={'agent_modes': self.agent_modes})
        self.assertEqual(len(self.expected_agent_modes), len(l3_agents))
        returned_agent_modes = [self._get_agent_mode(agent)
                                for agent in l3_agents]
        self.assertEqual(self.expected_agent_modes, returned_agent_modes)
