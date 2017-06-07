# Copyright (C) 2014 eNovance SAS <licensing@enovance.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#

import functools

import netaddr
from neutron_lib.api import validators
from neutron_lib import constants
from neutron_lib import exceptions as n_exc
from oslo_config import cfg
from oslo_db import exception as db_exc
from oslo_log import helpers as log_helpers
from oslo_log import log as logging
from oslo_utils import excutils
import six
import sqlalchemy as sa
from sqlalchemy import exc as sql_exc
from sqlalchemy import orm

from neutron._i18n import _, _LE, _LI, _LW
from neutron.api.v2 import attributes
from neutron.callbacks import events
from neutron.callbacks import registry
from neutron.callbacks import resources
from neutron.common import _deprecate
from neutron.common import constants as n_const
from neutron.common import utils as n_utils
from neutron.db import _utils as db_utils
from neutron.db import api as db_api
from neutron.db.availability_zone import router as router_az_db
from neutron.db import l3_dvr_db
from neutron.db.l3_dvr_db import is_distributed_router
from neutron.db.models import agent as agent_model
from neutron.db.models import l3 as l3_models
from neutron.db.models import l3_attrs
from neutron.db.models import l3ha as l3ha_model
from neutron.extensions import l3
from neutron.extensions import l3_ext_ha_mode as l3_ha
from neutron.extensions import portbindings
from neutron.extensions import providernet
from neutron.plugins.common import utils as p_utils


_deprecate._moved_global('L3HARouterAgentPortBinding', new_module=l3ha_model)
_deprecate._moved_global('L3HARouterNetwork', new_module=l3ha_model)
_deprecate._moved_global('L3HARouterVRIdAllocation', new_module=l3ha_model)

VR_ID_RANGE = set(range(1, 255))
MAX_ALLOCATION_TRIES = 10
UNLIMITED_AGENTS_PER_ROUTER = 0

LOG = logging.getLogger(__name__)

L3_HA_OPTS = [
    cfg.BoolOpt('l3_ha',
                default=False,
                help=_('Enable HA mode for virtual routers.')),
    cfg.IntOpt('max_l3_agents_per_router',
               default=3,
               help=_("Maximum number of L3 agents which a HA router will be "
                      "scheduled on. If it is set to 0 then the router will "
                      "be scheduled on every agent.")),
    cfg.StrOpt('l3_ha_net_cidr',
               default=n_const.L3_HA_NET_CIDR,
               help=_('Subnet used for the l3 HA admin network.')),
    cfg.StrOpt('l3_ha_network_type', default='',
               help=_("The network type to use when creating the HA network "
                      "for an HA router. By default or if empty, the first "
                      "'tenant_network_types' is used. This is helpful when "
                      "the VRRP traffic should use a specific network which "
                      "is not the default one.")),
    cfg.StrOpt('l3_ha_network_physical_name', default='',
               help=_("The physical network name with which the HA network "
                      "can be created."))
]
cfg.CONF.register_opts(L3_HA_OPTS)


class L3_HA_NAT_db_mixin(l3_dvr_db.L3_NAT_with_dvr_db_mixin,
                         router_az_db.RouterAvailabilityZoneMixin):
    """Mixin class to add high availability capability to routers."""

    def _verify_configuration(self):
        self.ha_cidr = cfg.CONF.l3_ha_net_cidr
        try:
            net = netaddr.IPNetwork(self.ha_cidr)
        except netaddr.AddrFormatError:
            raise l3_ha.HANetworkCIDRNotValid(cidr=self.ha_cidr)
        if ('/' not in self.ha_cidr or net.network != net.ip):
            raise l3_ha.HANetworkCIDRNotValid(cidr=self.ha_cidr)

        self._check_num_agents_per_router()

    def _check_num_agents_per_router(self):
        max_agents = cfg.CONF.max_l3_agents_per_router

        if max_agents != UNLIMITED_AGENTS_PER_ROUTER and max_agents < 1:
            raise l3_ha.HAMaximumAgentsNumberNotValid(max_agents=max_agents)

    def __new__(cls, *args, **kwargs):
        inst = super(L3_HA_NAT_db_mixin, cls).__new__(cls, *args, **kwargs)
        inst._verify_configuration()
        registry.subscribe(inst._release_router_vr_id,
                           resources.ROUTER, events.PRECOMMIT_DELETE)
        registry.subscribe(inst._cleanup_ha_network,
                           resources.ROUTER, events.AFTER_DELETE)
        registry.subscribe(inst._precommit_router_create,
                           resources.ROUTER, events.PRECOMMIT_CREATE)
        registry.subscribe(inst._before_router_create,
                           resources.ROUTER, events.BEFORE_CREATE)
        registry.subscribe(inst._after_router_create,
                           resources.ROUTER, events.AFTER_CREATE)
        registry.subscribe(inst._validate_migration,
                           resources.ROUTER, events.PRECOMMIT_UPDATE)
        registry.subscribe(inst._reconfigure_ha_resources,
                           resources.ROUTER, events.AFTER_UPDATE)
        return inst

    def get_ha_network(self, context, tenant_id):
        return (context.session.query(l3ha_model.L3HARouterNetwork).
                filter(l3ha_model.L3HARouterNetwork.tenant_id == tenant_id).
                first())

    def _get_allocated_vr_id(self, context, network_id):
        with context.session.begin(subtransactions=True):
            query = (context.session.query(
                l3ha_model.L3HARouterVRIdAllocation).
                filter(l3ha_model.L3HARouterVRIdAllocation.network_id ==
                       network_id))

            allocated_vr_ids = set(a.vr_id for a in query) - set([0])

        return allocated_vr_ids

    @db_api.retry_if_session_inactive()
    def _ensure_vr_id(self, context, router_db, ha_network):
        router_id = router_db.id
        network_id = ha_network.network_id

        # TODO(kevinbenton): let decorator handle duplicate retry
        # like in review.openstack.org/#/c/367179/1/neutron/db/l3_hamode_db.py
        for count in range(MAX_ALLOCATION_TRIES):
            try:
                # NOTE(kevinbenton): we disallow subtransactions because the
                # retry logic will bust any parent transactions
                with context.session.begin():
                    if router_db.extra_attributes.ha_vr_id:
                        LOG.debug(
                            "Router %(router_id)s has already been "
                            "allocated a ha_vr_id %(ha_vr_id)d!",
                            {'router_id': router_id,
                             'ha_vr_id': router_db.extra_attributes.ha_vr_id})
                        return

                    allocated_vr_ids = self._get_allocated_vr_id(context,
                                                                 network_id)
                    available_vr_ids = VR_ID_RANGE - allocated_vr_ids

                    if not available_vr_ids:
                        raise l3_ha.NoVRIDAvailable(router_id=router_id)

                    allocation = l3ha_model.L3HARouterVRIdAllocation()
                    allocation.network_id = network_id
                    allocation.vr_id = available_vr_ids.pop()

                    context.session.add(allocation)
                    router_db.extra_attributes.ha_vr_id = allocation.vr_id
                    LOG.debug(
                        "Router %(router_id)s has been allocated a ha_vr_id "
                        "%(ha_vr_id)d.",
                        {'router_id': router_id, 'ha_vr_id': allocation.vr_id})

                    return allocation.vr_id

            except db_exc.DBDuplicateEntry:
                LOG.info(_LI("Attempt %(count)s to allocate a VRID in the "
                             "network %(network)s for the router %(router)s"),
                         {'count': count, 'network': network_id,
                          'router': router_id})

        raise l3_ha.MaxVRIDAllocationTriesReached(
            network_id=network_id, router_id=router_id,
            max_tries=MAX_ALLOCATION_TRIES)

    @db_api.retry_if_session_inactive()
    def _delete_vr_id_allocation(self, context, ha_network, vr_id):
        with context.session.begin(subtransactions=True):
            context.session.query(
                l3ha_model.L3HARouterVRIdAllocation).filter_by(
                    network_id=ha_network.network_id, vr_id=vr_id).delete()

    def _create_ha_subnet(self, context, network_id, tenant_id):
        args = {'network_id': network_id,
                'tenant_id': '',
                'name': n_const.HA_SUBNET_NAME % tenant_id,
                'ip_version': 4,
                'cidr': cfg.CONF.l3_ha_net_cidr,
                'enable_dhcp': False,
                'gateway_ip': None}
        return p_utils.create_subnet(self._core_plugin, context,
                                     {'subnet': args})

    def _create_ha_network_tenant_binding(self, context, tenant_id,
                                          network_id):
        with context.session.begin():
            ha_network = l3ha_model.L3HARouterNetwork(
                tenant_id=tenant_id, network_id=network_id)
            context.session.add(ha_network)
        # we need to check if someone else just inserted at exactly the
        # same time as us because there is no constrain in L3HARouterNetwork
        # that prevents multiple networks per tenant
        with context.session.begin(subtransactions=True):
            items = (context.session.query(l3ha_model.L3HARouterNetwork).
                     filter_by(tenant_id=tenant_id).all())
            if len(items) > 1:
                # we need to throw an error so our network is deleted
                # and the process is started over where the existing
                # network will be selected.
                raise db_exc.DBDuplicateEntry(columns=['tenant_id'])
        return ha_network

    def _add_ha_network_settings(self, network):
        if cfg.CONF.l3_ha_network_type:
            network[providernet.NETWORK_TYPE] = cfg.CONF.l3_ha_network_type

        if cfg.CONF.l3_ha_network_physical_name:
            network[providernet.PHYSICAL_NETWORK] = (
                cfg.CONF.l3_ha_network_physical_name)

    def _create_ha_network(self, context, tenant_id):
        admin_ctx = context.elevated()

        args = {'network':
                {'name': n_const.HA_NETWORK_NAME % tenant_id,
                 'tenant_id': '',
                 'shared': False,
                 'admin_state_up': True}}
        self._add_ha_network_settings(args['network'])
        creation = functools.partial(p_utils.create_network,
                                     self._core_plugin, admin_ctx, args)
        content = functools.partial(self._create_ha_network_tenant_binding,
                                    admin_ctx, tenant_id)
        deletion = functools.partial(self._core_plugin.delete_network,
                                     admin_ctx)

        network, ha_network = db_utils.safe_creation(
            context, creation, deletion, content, transaction=False)
        try:
            self._create_ha_subnet(admin_ctx, network['id'], tenant_id)
        except Exception:
            with excutils.save_and_reraise_exception():
                self._core_plugin.delete_network(admin_ctx, network['id'])

        return ha_network

    def get_number_of_agents_for_scheduling(self, context):
        """Return number of agents on which the router will be scheduled."""

        num_agents = len(self.get_l3_agents(context, active=True,
            filters={'agent_modes': [constants.L3_AGENT_MODE_LEGACY,
                                     constants.L3_AGENT_MODE_DVR_SNAT]}))
        max_agents = cfg.CONF.max_l3_agents_per_router
        if max_agents:
            if max_agents > num_agents:
                LOG.info(_LI("Number of active agents lower than "
                             "max_l3_agents_per_router. L3 agents "
                             "available: %s"), num_agents)
            else:
                num_agents = max_agents

        return num_agents

    @db_api.retry_if_session_inactive()
    def _create_ha_port_binding(self, context, router_id, port_id):
        try:
            with context.session.begin():
                routerportbinding = l3_models.RouterPort(
                    port_id=port_id, router_id=router_id,
                    port_type=constants.DEVICE_OWNER_ROUTER_HA_INTF)
                context.session.add(routerportbinding)
                portbinding = l3ha_model.L3HARouterAgentPortBinding(
                    port_id=port_id, router_id=router_id)
                context.session.add(portbinding)

            return portbinding
        except db_exc.DBReferenceError as e:
            with excutils.save_and_reraise_exception() as ctxt:
                if isinstance(e.inner_exception, sql_exc.IntegrityError):
                    ctxt.reraise = False
                    LOG.debug(
                        'Failed to create HA router agent PortBinding, '
                        'Router %s has already been removed '
                        'by concurrent operation', router_id)
                    raise l3.RouterNotFound(router_id=router_id)

    def add_ha_port(self, context, router_id, network_id, tenant_id):
        # NOTE(kevinbenton): we have to block any ongoing transactions because
        # our exception handling will try to delete the port using the normal
        # core plugin API. If this function is called inside of a transaction
        # the exception will mangle the state, cause the delete call to fail,
        # and end up relying on the DB rollback to remove the port instead of
        # proper delete_port call.
        if context.session.is_active:
            raise RuntimeError(_('add_ha_port cannot be called inside of a '
                                 'transaction.'))
        args = {'tenant_id': '',
                'network_id': network_id,
                'admin_state_up': True,
                'device_id': router_id,
                'device_owner': constants.DEVICE_OWNER_ROUTER_HA_INTF,
                'name': n_const.HA_PORT_NAME % tenant_id}
        creation = functools.partial(p_utils.create_port, self._core_plugin,
                                     context, {'port': args})
        content = functools.partial(self._create_ha_port_binding, context,
                                    router_id)
        deletion = functools.partial(self._core_plugin.delete_port, context,
                                     l3_port_check=False)
        port, bindings = db_utils.safe_creation(context, creation,
                                                deletion, content,
                                                transaction=False)
        return bindings

    def _delete_ha_interfaces(self, context, router_id):
        admin_ctx = context.elevated()
        device_filter = {'device_id': [router_id],
                         'device_owner':
                         [constants.DEVICE_OWNER_ROUTER_HA_INTF]}
        ports = self._core_plugin.get_ports(admin_ctx, filters=device_filter)

        for port in ports:
            self._core_plugin.delete_port(admin_ctx, port['id'],
                                          l3_port_check=False)

    def delete_ha_interfaces_on_host(self, context, router_id, host):
        admin_ctx = context.elevated()
        port_ids = (binding.port_id for binding
                    in self.get_ha_router_port_bindings(admin_ctx,
                                                        [router_id], host))
        for port_id in port_ids:
            self._core_plugin.delete_port(admin_ctx, port_id,
                                          l3_port_check=False)

    def _notify_router_updated(self, context, router_id):
        self.l3_rpc_notifier.routers_updated(
            context, [router_id], shuffle_agents=True)

    @classmethod
    def _is_ha(cls, router):
        ha = router.get('ha')
        if not validators.is_attr_set(ha):
            ha = cfg.CONF.l3_ha
        return ha

    def _get_device_owner(self, context, router=None):
        """Get device_owner for the specified router."""
        router_is_uuid = isinstance(router, six.string_types)
        if router_is_uuid:
            router = self._get_router(context, router)
        if is_ha_router(router) and not is_distributed_router(router):
            return constants.DEVICE_OWNER_HA_REPLICATED_INT
        return super(L3_HA_NAT_db_mixin,
                     self)._get_device_owner(context, router)

    @n_utils.transaction_guard
    def _ensure_vr_id_and_network(self, context, router_db):
        """Attach vr_id to router while tolerating network deletes."""
        creator = functools.partial(self._ensure_vr_id,
                                    context, router_db)
        dep_getter = functools.partial(self.get_ha_network,
                                       context, router_db.tenant_id)
        dep_creator = functools.partial(self._create_ha_network,
                                        context, router_db.tenant_id)
        dep_deleter = functools.partial(self._delete_ha_network, context)
        dep_id_attr = 'network_id'
        return n_utils.create_object_with_dependency(
            creator, dep_getter, dep_creator, dep_id_attr, dep_deleter)[1]

    @db_api.retry_if_session_inactive()
    def _before_router_create(self, resource, event, trigger,
                              context, router, **kwargs):
        """Event handler to create HA resources before router creation."""
        if not self._is_ha(router):
            return
        # ensure the HA network exists before we start router creation so
        # we can provide meaningful errors back to the user if no network
        # can be allocated
        if not self.get_ha_network(context, router['tenant_id']):
            self._create_ha_network(context, router['tenant_id'])

    def _precommit_router_create(self, resource, event, trigger, context,
                                 router, router_db, **kwargs):
        """Event handler to set ha flag and status on creation."""
        is_ha = self._is_ha(router)
        router['ha'] = is_ha
        self.set_extra_attr_value(context, router_db, 'ha', is_ha)
        if not is_ha:
            return
        # This will throw an exception if there aren't enough agents to
        # handle this HA router
        self.get_number_of_agents_for_scheduling(context)
        ha_net = self.get_ha_network(context, router['tenant_id'])
        if not ha_net:
            # net was deleted, throw a retry to start over to create another
            raise db_exc.RetryRequest(
                    l3_ha.HANetworkConcurrentDeletion(
                        tenant_id=router['tenant_id']))

    def _after_router_create(self, resource, event, trigger, context,
                             router_id, router, router_db, **kwargs):
        if not router['ha']:
            return
        try:
            self.schedule_router(context, router_id)
            router['ha_vr_id'] = router_db.extra_attributes.ha_vr_id
            self._notify_router_updated(context, router_id)
        except Exception as e:
            with excutils.save_and_reraise_exception() as ctx:
                if isinstance(e, l3_ha.NoVRIDAvailable):
                    ctx.reraise = False
                    LOG.warning(_LW("No more VRIDs for router: %s"), e)
                else:
                    LOG.exception(_LE("Failed to schedule HA router %s."),
                                  router_id)
                router['status'] = self._update_router_db(
                    context, router_id,
                    {'status': n_const.ROUTER_STATUS_ERROR})['status']

    def _validate_migration(self, resource, event, trigger, context,
                            router_id, router, router_db, old_router,
                            **kwargs):
        """Event handler on precommit update to validate migration."""

        original_ha_state = old_router['ha']
        requested_ha_state = router.get('ha')

        ha_changed = (requested_ha_state is not None and
                      requested_ha_state != original_ha_state)
        if not ha_changed:
            return

        if router_db.admin_state_up:
            msg = _('Cannot change HA attribute of active routers. Please '
                    'set router admin_state_up to False prior to upgrade')
            raise n_exc.BadRequest(resource='router', msg=msg)

        if requested_ha_state:
            # This will throw HANotEnoughAvailableAgents if there aren't
            # enough l3 agents to handle this router.
            self.get_number_of_agents_for_scheduling(context)
        else:

            ha_network = self.get_ha_network(context,
                                             router_db.tenant_id)
            self._delete_vr_id_allocation(
                context, ha_network, router_db.extra_attributes.ha_vr_id)
            router_db.extra_attributes.ha_vr_id = None
        self.set_extra_attr_value(context, router_db, 'ha', requested_ha_state)

    def _reconfigure_ha_resources(self, resource, event, trigger, context,
                                  router_id, old_router, router, router_db,
                                  **kwargs):
        """Event handler to react to changes after HA flag has been updated."""
        ha_changed = old_router['ha'] != router['ha']
        if not ha_changed:
            return
        requested_ha_state = router['ha']
        # The HA attribute has changed. First unbind the router from agents
        # to force a proper re-scheduling to agents.
        # TODO(jschwarz): This will have to be more selective to get HA + DVR
        # working (Only unbind from dvr_snat nodes).
        self._unbind_ha_router(context, router_id)

        if not requested_ha_state:
            self._delete_ha_interfaces(context, router_db.id)
            # always attempt to cleanup the network as the router is
            # deleted. the core plugin will stop us if its in use
            ha_network = self.get_ha_network(context,
                                             router_db.tenant_id)
            if ha_network:
                self.safe_delete_ha_network(context, ha_network,
                                            router_db.tenant_id)
            self._migrate_router_ports(
                context, router_db,
                old_owner=constants.DEVICE_OWNER_HA_REPLICATED_INT,
                new_owner=constants.DEVICE_OWNER_ROUTER_INTF)

        self.schedule_router(context, router_id)
        self._notify_router_updated(context, router_db.id)

    def _delete_ha_network(self, context, net):
        admin_ctx = context.elevated()
        self._core_plugin.delete_network(admin_ctx, net.network_id)

    def safe_delete_ha_network(self, context, ha_network, tenant_id):
        try:
            # reference the attr inside the try block before we attempt
            # to delete the network and potentially invalidate the
            # relationship
            net_id = ha_network.network_id
            self._delete_ha_network(context, ha_network)
        except (n_exc.NetworkNotFound,
                orm.exc.ObjectDeletedError):
            LOG.debug(
                "HA network for tenant %s was already deleted.", tenant_id)
        except sa.exc.InvalidRequestError:
            LOG.info(_LI("HA network %s can not be deleted."), net_id)
        except n_exc.NetworkInUse:
            # network is still in use, this is normal so we don't
            # log anything
            pass
        else:
            LOG.info(_LI("HA network %(network)s was deleted as "
                         "no HA routers are present in tenant "
                         "%(tenant)s."),
                     {'network': net_id, 'tenant': tenant_id})

    def _release_router_vr_id(self, resource, event, trigger, context,
                              router_db, **kwargs):
        """Event handler for removal of VRID during router delete."""
        if router_db.extra_attributes.ha:
            ha_network = self.get_ha_network(context,
                                             router_db.tenant_id)
            if ha_network:
                self._delete_vr_id_allocation(
                    context, ha_network, router_db.extra_attributes.ha_vr_id)

    @db_api.retry_if_session_inactive()
    def _cleanup_ha_network(self, resource, event, trigger, context,
                            router_id, original, **kwargs):
        """Event handler to attempt HA network deletion after router delete."""
        if not original['ha']:
            return
        ha_network = self.get_ha_network(context, original['tenant_id'])
        if not ha_network:
            return
        # always attempt to cleanup the network as the router is
        # deleted. the core plugin will stop us if its in use
        self.safe_delete_ha_network(context, ha_network, original['tenant_id'])

    def _unbind_ha_router(self, context, router_id):
        for agent in self.get_l3_agents_hosting_routers(context, [router_id]):
            self.remove_router_from_l3_agent(context, agent['id'], router_id)

    def get_ha_router_port_bindings(self, context, router_ids, host=None):
        if not router_ids:
            return []
        query = context.session.query(l3ha_model.L3HARouterAgentPortBinding)

        if host:
            query = query.join(agent_model.Agent).filter(
                agent_model.Agent.host == host)

        query = query.filter(
            l3ha_model.L3HARouterAgentPortBinding.router_id.in_(router_ids))

        return query.all()

    @staticmethod
    def _check_router_agent_ha_binding(context, router_id, agent_id):
        query = context.session.query(l3ha_model.L3HARouterAgentPortBinding)
        query = query.filter(
            l3ha_model.L3HARouterAgentPortBinding.router_id == router_id,
            l3ha_model.L3HARouterAgentPortBinding.l3_agent_id == agent_id)
        return query.first() is not None

    def _get_bindings_and_update_router_state_for_dead_agents(self, context,
                                                              router_id):
        """Return bindings. In case if dead agents were detected update router
           states on this agent.

        """
        with context.session.begin(subtransactions=True):
            bindings = self.get_ha_router_port_bindings(context, [router_id])
            dead_agents = []
            active = [binding for binding in bindings
                      if binding.state == n_const.HA_ROUTER_STATE_ACTIVE]
            # Check dead agents only if we have more then one active agent
            if len(active) > 1:
                dead_agents = [binding.agent for binding in active
                               if not (binding.agent.is_active and
                                       binding.agent.admin_state_up)]
                for dead_agent in dead_agents:
                    self.update_routers_states(
                        context,
                        {router_id: n_const.HA_ROUTER_STATE_STANDBY},
                        dead_agent.host)
        if dead_agents:
            return self.get_ha_router_port_bindings(context, [router_id])
        return bindings

    def get_l3_bindings_hosting_router_with_ha_states(
            self, context, router_id):
        """Return a list of [(agent, ha_state), ...]."""
        bindings = self._get_bindings_and_update_router_state_for_dead_agents(
            context, router_id)
        return [(binding.agent, binding.state) for binding in bindings
                if binding.agent is not None]

    def get_active_host_for_ha_router(self, context, router_id):
        bindings = self.get_l3_bindings_hosting_router_with_ha_states(
            context, router_id)
        # TODO(amuller): In case we have two or more actives, this method
        # needs to return the last agent to become active. This requires
        # timestamps for state changes. Otherwise, if a host goes down
        # and another takes over, we'll have two actives. In this case,
        # if an interface is added to a router, its binding might be wrong
        # and l2pop would not work correctly.
        return next(
            (agent.host for agent, state in bindings
             if state == n_const.HA_ROUTER_STATE_ACTIVE),
            None)

    @log_helpers.log_method_call
    def _process_sync_ha_data(self, context, routers, host, agent_mode):
        routers_dict = dict((router['id'], router) for router in routers)

        bindings = self.get_ha_router_port_bindings(context,
                                                    routers_dict.keys(),
                                                    host)
        for binding in bindings:
            port = binding.port
            if not port:
                # Filter the HA router has no ha port here
                LOG.info(_LI("HA router %s is missing HA router port "
                             "bindings. Skipping it."),
                         binding.router_id)
                routers_dict.pop(binding.router_id)
                continue
            port_dict = self._core_plugin._make_port_dict(port)

            router = routers_dict.get(binding.router_id)
            router[constants.HA_INTERFACE_KEY] = port_dict
            router[n_const.HA_ROUTER_STATE_KEY] = binding.state

        for router in routers_dict.values():
            interface = router.get(constants.HA_INTERFACE_KEY)
            if interface:
                self._populate_mtu_and_subnets_for_ports(context, [interface])

        # If this is a DVR+HA router, but the agent is question is in 'dvr'
        # mode (as opposed to 'dvr_snat'), then we want to always return it
        # even though it's missing the '_ha_interface' key.
        return [r for r in list(routers_dict.values())
                if (agent_mode == constants.L3_AGENT_MODE_DVR or
                    not r.get('ha') or r.get(constants.HA_INTERFACE_KEY))]

    @log_helpers.log_method_call
    def get_ha_sync_data_for_host(self, context, host, agent,
                                  router_ids=None, active=None):
        agent_mode = self._get_agent_mode(agent)
        dvr_agent_mode = (agent_mode in [constants.L3_AGENT_MODE_DVR_SNAT,
                                         constants.L3_AGENT_MODE_DVR])
        if (dvr_agent_mode and n_utils.is_extension_supported(
                self, constants.L3_DISTRIBUTED_EXT_ALIAS)):
            # DVR has to be handled differently
            sync_data = self._get_dvr_sync_data(context, host, agent,
                                                router_ids, active)
        else:
            sync_data = super(L3_HA_NAT_db_mixin, self).get_sync_data(context,
                                                            router_ids, active)
        return self._process_sync_ha_data(context, sync_data, host, agent_mode)

    @classmethod
    def _set_router_states(cls, context, bindings, states):
        for binding in bindings:
            try:
                with context.session.begin(subtransactions=True):
                    binding.state = states[binding.router_id]
            except (orm.exc.StaleDataError, orm.exc.ObjectDeletedError):
                # Take concurrently deleted routers in to account
                pass

    @db_api.retry_if_session_inactive()
    def update_routers_states(self, context, states, host):
        """Receive dict of router ID to state and update them all."""

        bindings = self.get_ha_router_port_bindings(
            context, router_ids=states.keys(), host=host)
        self._set_router_states(context, bindings, states)
        self._update_router_port_bindings(context, states, host)

    def _update_router_port_bindings(self, context, states, host):
        admin_ctx = context.elevated()
        device_filter = {'device_id': list(states.keys()),
                         'device_owner':
                         [constants.DEVICE_OWNER_HA_REPLICATED_INT,
                          constants.DEVICE_OWNER_ROUTER_SNAT]}
        ports = self._core_plugin.get_ports(admin_ctx, filters=device_filter)
        active_ports = (port for port in ports
            if states[port['device_id']] == n_const.HA_ROUTER_STATE_ACTIVE)

        for port in active_ports:
            port[portbindings.HOST_ID] = host
            try:
                self._core_plugin.update_port(admin_ctx, port['id'],
                                              {attributes.PORT: port})
            except (orm.exc.StaleDataError, orm.exc.ObjectDeletedError,
                    n_exc.PortNotFound):
                # Take concurrently deleted interfaces in to account
                pass


def is_ha_router(router):
    """Return True if router to be handled is ha."""
    try:
        # See if router is a DB object first
        requested_router_type = router.extra_attributes.ha
    except AttributeError:
        # if not, try to see if it is a request body
        requested_router_type = router.get('ha')
    if validators.is_attr_set(requested_router_type):
        return requested_router_type
    return cfg.CONF.l3_ha


def is_ha_router_port(context, device_owner, router_id):
    session = db_api.get_reader_session()
    if device_owner == constants.DEVICE_OWNER_HA_REPLICATED_INT:
        return True
    elif device_owner == constants.DEVICE_OWNER_ROUTER_SNAT:
        query = session.query(l3_attrs.RouterExtraAttributes)
        query = query.filter_by(ha=True)
        query = query.filter(l3_attrs.RouterExtraAttributes.router_id ==
                             router_id)
        return bool(query.limit(1).count())
    else:
        return False


_deprecate._MovedGlobals()
