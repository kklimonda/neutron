# Copyright (c) 2016 Intel Corporation.
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

from oslo_versionedobjects import base as obj_base
from oslo_versionedobjects import fields as obj_fields
from sqlalchemy import func

from neutron.agent.common import utils
from neutron.db.models import agent as agent_model
from neutron.db.models import l3agent as rb_model
from neutron.objects import base
from neutron.objects import common_types
from neutron.objects import utils as obj_utils


@obj_base.VersionedObjectRegistry.register
class Agent(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = agent_model.Agent

    fields = {
        'id': common_types.UUIDField(),
        'agent_type': obj_fields.StringField(),
        'binary': obj_fields.StringField(),
        'topic': obj_fields.StringField(),
        'host': obj_fields.StringField(),
        'availability_zone': obj_fields.StringField(nullable=True),
        'admin_state_up': obj_fields.BooleanField(default=True),
        'started_at': obj_fields.DateTimeField(tzinfo_aware=False),
        'created_at': obj_fields.DateTimeField(tzinfo_aware=False),
        'heartbeat_timestamp': obj_fields.DateTimeField(tzinfo_aware=False),
        'description': obj_fields.StringField(nullable=True),
        'configurations': common_types.DictOfMiscValuesField(),
        'resource_versions': common_types.DictOfMiscValuesField(nullable=True),
        'load': obj_fields.IntegerField(default=0),
    }

    @classmethod
    def modify_fields_to_db(cls, fields):
        result = super(Agent, cls).modify_fields_to_db(fields)
        if ('configurations' in result and
                not isinstance(result['configurations'],
                               obj_utils.StringMatchingFilterObj)):
            # dump configuration into string, set '' if empty '{}'
            result['configurations'] = (
                cls.filter_to_json_str(result['configurations'], default=''))
        if ('resource_versions' in result and
                not isinstance(result['resource_versions'],
                               obj_utils.StringMatchingFilterObj)):
            # dump resource version into string, set None if empty '{}' or None
            result['resource_versions'] = (
                cls.filter_to_json_str(result['resource_versions']))
        return result

    @classmethod
    def modify_fields_from_db(cls, db_obj):
        fields = super(Agent, cls).modify_fields_from_db(db_obj)
        if 'configurations' in fields:
            # load string from DB, set {} if configuration is ''
            fields['configurations'] = (
                cls.load_json_from_str(fields['configurations'], default={}))
        if 'resource_versions' in fields:
            # load string from DB, set None if resource_version is None or ''
            fields['resource_versions'] = (
                cls.load_json_from_str(fields['resource_versions']))
        return fields

    @property
    def is_active(self):
        return not utils.is_agent_down(self.heartbeat_timestamp)

    # TODO(ihrachys) reuse query builder from
    # get_l3_agents_ordered_by_num_routers
    @classmethod
    def get_l3_agent_with_min_routers(cls, context, agent_ids):
        """Return l3 agent with the least number of routers."""
        with context.session.begin(subtransactions=True):
            query = context.session.query(
                agent_model.Agent,
                func.count(
                    rb_model.RouterL3AgentBinding.router_id
                ).label('count')).outerjoin(
                    rb_model.RouterL3AgentBinding).group_by(
                    agent_model.Agent.id,
                    rb_model.RouterL3AgentBinding
                    .l3_agent_id).order_by('count')
            res = query.filter(agent_model.Agent.id.in_(agent_ids)).first()
        agent_obj = cls._load_object(context, res[0])
        return agent_obj

    @classmethod
    def get_l3_agents_ordered_by_num_routers(cls, context, agent_ids):
        with context.session.begin(subtransactions=True):
            query = (context.session.query(agent_model.Agent, func.count(
                rb_model.RouterL3AgentBinding.router_id)
                .label('count')).
                outerjoin(rb_model.RouterL3AgentBinding).
                group_by(agent_model.Agent.id).
                filter(agent_model.Agent.id.in_(agent_ids)).
                order_by('count'))
        agents = [cls._load_object(context, record[0]) for record in query]

        return agents
