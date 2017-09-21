# Copyright (c) 2016 OpenStack Foundation.  All rights reserved.
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

from neutron.db import api as db_api
from neutron.db.models import dns as dns_models
from neutron.db.models import segment as segment_model
from neutron.db import models_v2
from neutron.db.port_security import models as ps_models
from neutron.db.qos import models as qos_models
from neutron.db import rbac_db_models
from neutron.extensions import availability_zone as az_ext
from neutron.objects import base
from neutron.objects import common_types
from neutron.objects.db import api as obj_db_api
from neutron.objects.extensions import port_security as base_ps
from neutron.objects import rbac_db


@obj_base.VersionedObjectRegistry.register
class NetworkSegment(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = segment_model.NetworkSegment

    fields = {
        'id': common_types.UUIDField(),
        'network_id': common_types.UUIDField(),
        'name': obj_fields.StringField(),
        'network_type': obj_fields.StringField(),
        'physical_network': obj_fields.StringField(nullable=True),
        'segmentation_id': obj_fields.IntegerField(nullable=True),
        'is_dynamic': obj_fields.BooleanField(default=False),
        'segment_index': obj_fields.IntegerField(default=0)
    }

    foreign_keys = {
        'Network': {'network_id': 'id'},
        'PortBindingLevel': {'id': 'segment_id'},
    }

    @classmethod
    def get_objects(cls, context, _pager=None, **kwargs):
        if not _pager:
            _pager = base.Pager()
        if not _pager.sorts:
            # (NOTE) True means ASC, False is DESC
            _pager.sorts = [
                (field, True) for field in ('network_id', 'segment_index')
            ]
        return super(NetworkSegment, cls).get_objects(context, _pager,
                                                      **kwargs)


@obj_base.VersionedObjectRegistry.register
class NetworkPortSecurity(base_ps._PortSecurity):
    # Version 1.0: Initial version
    VERSION = "1.0"

    db_model = ps_models.NetworkSecurityBinding

    fields_need_translation = {'id': 'network_id'}


@obj_base.VersionedObjectRegistry.register
class Network(rbac_db.NeutronRbacObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    rbac_db_model = rbac_db_models.NetworkRBAC
    db_model = models_v2.Network

    fields = {
        'id': common_types.UUIDField(),
        'project_id': obj_fields.StringField(nullable=True),
        'name': obj_fields.StringField(nullable=True),
        'status': obj_fields.StringField(nullable=True),
        'admin_state_up': obj_fields.BooleanField(nullable=True),
        'vlan_transparent': obj_fields.BooleanField(nullable=True),
        # TODO(ihrachys): consider converting to a field of stricter type
        'availability_zone_hints': obj_fields.ListOfStringsField(
            nullable=True),
        'shared': obj_fields.BooleanField(default=False),

        'mtu': obj_fields.IntegerField(nullable=True),

        # TODO(ihrachys): consider exposing availability zones

        # TODO(ihrachys): consider converting to boolean
        'security': obj_fields.ObjectField(
            'NetworkPortSecurity', nullable=True),
        'segments': obj_fields.ListOfObjectsField(
            'NetworkSegment', nullable=True),
        'dns_domain': common_types.DomainNameField(nullable=True),
        'qos_policy_id': common_types.UUIDField(nullable=True, default=None),

        # TODO(ihrachys): add support for tags, probably through a base class
        # since it's a feature that will probably later be added for other
        # resources too

        # TODO(ihrachys): expose external network attributes
    }

    synthetic_fields = [
        'dns_domain',
        # MTU is not stored in the database any more, it's a synthetic field
        # that may be used by plugins to provide a canonical representation for
        # the resource
        'mtu',
        'qos_policy_id',
        'security',
        'segments',
    ]

    fields_need_translation = {
        'security': 'port_security',
    }

    def create(self):
        fields = self.obj_get_changes()
        with db_api.autonested_transaction(self.obj_context.session):
            dns_domain = self.dns_domain
            qos_policy_id = self.qos_policy_id
            super(Network, self).create()
            if 'dns_domain' in fields:
                self._set_dns_domain(dns_domain)
            if 'qos_policy_id' in fields:
                self._attach_qos_policy(qos_policy_id)

    def update(self):
        fields = self.obj_get_changes()
        with db_api.autonested_transaction(self.obj_context.session):
            super(Network, self).update()
            if 'dns_domain' in fields:
                self._set_dns_domain(fields['dns_domain'])
            if 'qos_policy_id' in fields:
                self._attach_qos_policy(fields['qos_policy_id'])

    def _attach_qos_policy(self, qos_policy_id):
        # TODO(ihrachys): introduce an object for the binding to isolate
        # database access in a single place, currently scattered between port
        # and policy objects
        obj_db_api.delete_objects(
            self.obj_context, qos_models.QosNetworkPolicyBinding,
            network_id=self.id,
        )
        if qos_policy_id:
            obj_db_api.create_object(
                self.obj_context, qos_models.QosNetworkPolicyBinding,
                {'network_id': self.id, 'policy_id': qos_policy_id}
            )
        self.qos_policy_id = qos_policy_id
        self.obj_reset_changes(['qos_policy_id'])

    def _set_dns_domain(self, dns_domain):
        NetworkDNSDomain.delete_objects(self.obj_context, network_id=self.id)
        if dns_domain:
            NetworkDNSDomain(self.obj_context,
                network_id=self.id, dns_domain=dns_domain).create()
        self.dns_domain = dns_domain
        self.obj_reset_changes(['dns_domain'])

    @classmethod
    def modify_fields_from_db(cls, db_obj):
        result = super(Network, cls).modify_fields_from_db(db_obj)
        if az_ext.AZ_HINTS in result:
            result[az_ext.AZ_HINTS] = (
                az_ext.convert_az_string_to_list(result[az_ext.AZ_HINTS]))
        return result

    @classmethod
    def modify_fields_to_db(cls, fields):
        result = super(Network, cls).modify_fields_to_db(fields)
        if az_ext.AZ_HINTS in result:
            result[az_ext.AZ_HINTS] = (
                az_ext.convert_az_list_to_string(result[az_ext.AZ_HINTS]))
        return result

    def from_db_object(self, *objs):
        super(Network, self).from_db_object(*objs)
        for db_obj in objs:
            # extract domain name
            if db_obj.get('dns_domain'):
                self.dns_domain = (
                    db_obj.dns_domain.dns_domain
                )
            else:
                self.dns_domain = None
            self.obj_reset_changes(['dns_domain'])

            # extract qos policy binding
            if db_obj.get('qos_policy_binding'):
                self.qos_policy_id = (
                    db_obj.qos_policy_binding.policy_id
                )
            else:
                self.qos_policy_id = None
            self.obj_reset_changes(['qos_policy_id'])

    @classmethod
    def get_bound_tenant_ids(cls, context, policy_id):
        # TODO(ihrachys): provide actual implementation
        return set()


@obj_base.VersionedObjectRegistry.register
class SegmentHostMapping(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = segment_model.SegmentHostMapping

    fields = {
        'segment_id': common_types.UUIDField(),
        'host': obj_fields.StringField(),
    }

    primary_keys = ['segment_id', 'host']


@obj_base.VersionedObjectRegistry.register
class NetworkDNSDomain(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = dns_models.NetworkDNSDomain

    primary_keys = ['network_id']

    fields = {
        'network_id': common_types.UUIDField(),
        'dns_domain': common_types.DomainNameField(),
    }

    @classmethod
    def get_net_dns_from_port(cls, context, port_id):
        net_dns = context.session.query(cls.db_model).join(
            models_v2.Port, cls.db_model.network_id ==
            models_v2.Port.network_id).filter_by(
                id=port_id).one_or_none()
        if net_dns is None:
            return None
        return super(NetworkDNSDomain, cls)._load_object(context, net_dns)
