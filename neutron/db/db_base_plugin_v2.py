# Copyright (c) 2012 OpenStack Foundation.
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

import functools

import netaddr
from oslo_config import cfg
from oslo_db import exception as db_exc
from oslo_log import log as logging
from oslo_utils import excutils
from oslo_utils import uuidutils
from sqlalchemy import and_
from sqlalchemy import event

from neutron.api.v2 import attributes
from neutron.callbacks import events
from neutron.callbacks import exceptions
from neutron.callbacks import registry
from neutron.callbacks import resources
from neutron.common import constants
from neutron.common import exceptions as n_exc
from neutron.common import ipv6_utils
from neutron import context as ctx
from neutron.db import api as db_api
from neutron.db import db_base_plugin_common
from neutron.db import ipam_non_pluggable_backend
from neutron.db import ipam_pluggable_backend
from neutron.db import models_v2
from neutron.db import rbac_db_models as rbac_db
from neutron.db import sqlalchemyutils
from neutron.extensions import l3
from neutron.i18n import _LE, _LI
from neutron import ipam
from neutron.ipam import subnet_alloc
from neutron import manager
from neutron import neutron_plugin_base_v2
from neutron.plugins.common import constants as service_constants


LOG = logging.getLogger(__name__)

# Ports with the following 'device_owner' values will not prevent
# network deletion.  If delete_network() finds that all ports on a
# network have these owners, it will explicitly delete each port
# and allow network deletion to continue.  Similarly, if delete_subnet()
# finds out that all existing IP Allocations are associated with ports
# with these owners, it will allow subnet deletion to proceed with the
# IP allocations being cleaned up by cascade.
AUTO_DELETE_PORT_OWNERS = [constants.DEVICE_OWNER_DHCP]


def _check_subnet_not_used(context, subnet_id):
    try:
        kwargs = {'context': context, 'subnet_id': subnet_id}
        registry.notify(
            resources.SUBNET, events.BEFORE_DELETE, None, **kwargs)
    except exceptions.CallbackFailure as e:
        raise n_exc.SubnetInUse(subnet_id=subnet_id, reason=e)


class NeutronDbPluginV2(db_base_plugin_common.DbBasePluginCommon,
                        neutron_plugin_base_v2.NeutronPluginBaseV2):
    """V2 Neutron plugin interface implementation using SQLAlchemy models.

    Whenever a non-read call happens the plugin will call an event handler
    class method (e.g., network_created()).  The result is that this class
    can be sub-classed by other classes that add custom behaviors on certain
    events.
    """

    # This attribute specifies whether the plugin supports or not
    # bulk/pagination/sorting operations. Name mangling is used in
    # order to ensure it is qualified by class
    __native_bulk_support = True
    __native_pagination_support = True
    __native_sorting_support = True

    def __init__(self):
        self.set_ipam_backend()
        if cfg.CONF.notify_nova_on_port_status_changes:
            from neutron.notifiers import nova
            # NOTE(arosen) These event listeners are here to hook into when
            # port status changes and notify nova about their change.
            self.nova_notifier = nova.Notifier()
            event.listen(models_v2.Port, 'after_insert',
                         self.nova_notifier.send_port_status)
            event.listen(models_v2.Port, 'after_update',
                         self.nova_notifier.send_port_status)
            event.listen(models_v2.Port.status, 'set',
                         self.nova_notifier.record_port_status_changed)

    def set_ipam_backend(self):
        if cfg.CONF.ipam_driver:
            self.ipam = ipam_pluggable_backend.IpamPluggableBackend()
        else:
            self.ipam = ipam_non_pluggable_backend.IpamNonPluggableBackend()

    def _validate_host_route(self, route, ip_version):
        try:
            netaddr.IPNetwork(route['destination'])
            netaddr.IPAddress(route['nexthop'])
        except netaddr.core.AddrFormatError:
            err_msg = _("Invalid route: %s") % route
            raise n_exc.InvalidInput(error_message=err_msg)
        except ValueError:
            # netaddr.IPAddress would raise this
            err_msg = _("Invalid route: %s") % route
            raise n_exc.InvalidInput(error_message=err_msg)
        self._validate_ip_version(ip_version, route['nexthop'], 'nexthop')
        self._validate_ip_version(ip_version, route['destination'],
                                  'destination')

    def _validate_shared_update(self, context, id, original, updated):
        # The only case that needs to be validated is when 'shared'
        # goes from True to False
        if updated['shared'] == original.shared or updated['shared']:
            return
        ports = self._model_query(
            context, models_v2.Port).filter(
                and_(
                    models_v2.Port.network_id == id,
                    models_v2.Port.device_owner !=
                    constants.DEVICE_OWNER_ROUTER_GW,
                    models_v2.Port.device_owner !=
                    constants.DEVICE_OWNER_FLOATINGIP))
        subnets = self._model_query(
            context, models_v2.Subnet).filter(
                models_v2.Subnet.network_id == id)
        tenant_ids = set([port['tenant_id'] for port in ports] +
                         [subnet['tenant_id'] for subnet in subnets])
        # raise if multiple tenants found or if the only tenant found
        # is not the owner of the network
        if (len(tenant_ids) > 1 or len(tenant_ids) == 1 and
            tenant_ids.pop() != original.tenant_id):
            raise n_exc.InvalidSharedSetting(network=original.name)

    def _validate_ipv6_attributes(self, subnet, cur_subnet):
        if cur_subnet:
            self._validate_ipv6_update_dhcp(subnet, cur_subnet)
            return
        ra_mode_set = attributes.is_attr_set(subnet.get('ipv6_ra_mode'))
        address_mode_set = attributes.is_attr_set(
            subnet.get('ipv6_address_mode'))
        self._validate_ipv6_dhcp(ra_mode_set, address_mode_set,
                                 subnet['enable_dhcp'])
        if ra_mode_set and address_mode_set:
            self._validate_ipv6_combination(subnet['ipv6_ra_mode'],
                                            subnet['ipv6_address_mode'])
        if address_mode_set or ra_mode_set:
            self._validate_eui64_applicable(subnet)

    def _validate_eui64_applicable(self, subnet):
        # Per RFC 4862, section 5.5.3, prefix length and interface
        # id together should be equal to 128. Currently neutron supports
        # EUI64 interface id only, thus limiting the prefix
        # length to be 64 only.
        if ipv6_utils.is_auto_address_subnet(subnet):
            if netaddr.IPNetwork(subnet['cidr']).prefixlen != 64:
                msg = _('Invalid CIDR %s for IPv6 address mode. '
                        'OpenStack uses the EUI-64 address format, '
                        'which requires the prefix to be /64.')
                raise n_exc.InvalidInput(
                    error_message=(msg % subnet['cidr']))

    def _validate_ipv6_combination(self, ra_mode, address_mode):
        if ra_mode != address_mode:
            msg = _("ipv6_ra_mode set to '%(ra_mode)s' with ipv6_address_mode "
                    "set to '%(addr_mode)s' is not valid. "
                    "If both attributes are set, they must be the same value"
                    ) % {'ra_mode': ra_mode, 'addr_mode': address_mode}
            raise n_exc.InvalidInput(error_message=msg)

    def _validate_ipv6_dhcp(self, ra_mode_set, address_mode_set, enable_dhcp):
        if (ra_mode_set or address_mode_set) and not enable_dhcp:
            msg = _("ipv6_ra_mode or ipv6_address_mode cannot be set when "
                    "enable_dhcp is set to False.")
            raise n_exc.InvalidInput(error_message=msg)

    def _validate_ipv6_update_dhcp(self, subnet, cur_subnet):
        if ('enable_dhcp' in subnet and not subnet['enable_dhcp']):
            msg = _("Cannot disable enable_dhcp with "
                    "ipv6 attributes set")

            ra_mode_set = attributes.is_attr_set(subnet.get('ipv6_ra_mode'))
            address_mode_set = attributes.is_attr_set(
                subnet.get('ipv6_address_mode'))

            if ra_mode_set or address_mode_set:
                raise n_exc.InvalidInput(error_message=msg)

            old_ra_mode_set = attributes.is_attr_set(
                cur_subnet.get('ipv6_ra_mode'))
            old_address_mode_set = attributes.is_attr_set(
                cur_subnet.get('ipv6_address_mode'))

            if old_ra_mode_set or old_address_mode_set:
                raise n_exc.InvalidInput(error_message=msg)

    def _create_bulk(self, resource, context, request_items):
        objects = []
        collection = "%ss" % resource
        items = request_items[collection]
        context.session.begin(subtransactions=True)
        try:
            for item in items:
                obj_creator = getattr(self, 'create_%s' % resource)
                objects.append(obj_creator(context, item))
            context.session.commit()
        except Exception:
            context.session.rollback()
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("An exception occurred while creating "
                              "the %(resource)s:%(item)s"),
                          {'resource': resource, 'item': item})
        return objects

    def create_network_bulk(self, context, networks):
        return self._create_bulk('network', context, networks)

    def create_network(self, context, network):
        """Handle creation of a single network."""
        # single request processing
        n = network['network']
        # NOTE(jkoelker) Get the tenant_id outside of the session to avoid
        #                unneeded db action if the operation raises
        tenant_id = self._get_tenant_id_for_create(context, n)
        with context.session.begin(subtransactions=True):
            args = {'tenant_id': tenant_id,
                    'id': n.get('id') or uuidutils.generate_uuid(),
                    'name': n['name'],
                    'admin_state_up': n['admin_state_up'],
                    'mtu': n.get('mtu', constants.DEFAULT_NETWORK_MTU),
                    'status': n.get('status', constants.NET_STATUS_ACTIVE)}
            # TODO(pritesh): Move vlan_transparent to the extension module.
            # vlan_transparent here is only added if the vlantransparent
            # extension is enabled.
            if ('vlan_transparent' in n and n['vlan_transparent'] !=
                attributes.ATTR_NOT_SPECIFIED):
                args['vlan_transparent'] = n['vlan_transparent']
            network = models_v2.Network(**args)
            if n['shared']:
                entry = rbac_db.NetworkRBAC(
                    network=network, action='access_as_shared',
                    target_tenant='*', tenant_id=network['tenant_id'])
                context.session.add(entry)
            context.session.add(network)
        return self._make_network_dict(network, process_extensions=False,
                                       context=context)

    def update_network(self, context, id, network):
        n = network['network']
        with context.session.begin(subtransactions=True):
            network = self._get_network(context, id)
            # validate 'shared' parameter
            if 'shared' in n:
                entry = None
                for item in network.rbac_entries:
                    if (item.action == 'access_as_shared' and
                            item.target_tenant == '*'):
                        entry = item
                        break
                setattr(network, 'shared', True if entry else False)
                self._validate_shared_update(context, id, network, n)
                update_shared = n.pop('shared')
                if update_shared and not entry:
                    entry = rbac_db.NetworkRBAC(
                        network=network, action='access_as_shared',
                        target_tenant='*', tenant_id=network['tenant_id'])
                    context.session.add(entry)
                elif not update_shared and entry:
                    context.session.delete(entry)
                    context.session.expire(network, ['rbac_entries'])
            network.update(n)
        return self._make_network_dict(network, context=context)

    def delete_network(self, context, id):
        with context.session.begin(subtransactions=True):
            network = self._get_network(context, id)

            context.session.query(models_v2.Port).filter_by(
                network_id=id).filter(
                models_v2.Port.device_owner.
                in_(AUTO_DELETE_PORT_OWNERS)).delete(synchronize_session=False)

            port_in_use = context.session.query(models_v2.Port).filter_by(
                network_id=id).first()

            if port_in_use:
                raise n_exc.NetworkInUse(net_id=id)

            # clean up subnets
            subnets = self._get_subnets_by_network(context, id)
            for subnet in subnets:
                self.delete_subnet(context, subnet['id'])

            context.session.delete(network)

    def get_network(self, context, id, fields=None):
        network = self._get_network(context, id)
        return self._make_network_dict(network, fields, context=context)

    def get_networks(self, context, filters=None, fields=None,
                     sorts=None, limit=None, marker=None,
                     page_reverse=False):
        marker_obj = self._get_marker_obj(context, 'network', limit, marker)
        make_network_dict = functools.partial(self._make_network_dict,
                                              context=context)
        return self._get_collection(context, models_v2.Network,
                                    make_network_dict,
                                    filters=filters, fields=fields,
                                    sorts=sorts,
                                    limit=limit,
                                    marker_obj=marker_obj,
                                    page_reverse=page_reverse)

    def get_networks_count(self, context, filters=None):
        return self._get_collection_count(context, models_v2.Network,
                                          filters=filters)

    def create_subnet_bulk(self, context, subnets):
        return self._create_bulk('subnet', context, subnets)

    def _validate_ip_version(self, ip_version, addr, name):
        """Check IP field of a subnet match specified ip version."""
        ip = netaddr.IPNetwork(addr)
        if ip.version != ip_version:
            data = {'name': name,
                    'addr': addr,
                    'ip_version': ip_version}
            msg = _("%(name)s '%(addr)s' does not match "
                    "the ip_version '%(ip_version)s'") % data
            raise n_exc.InvalidInput(error_message=msg)

    def _validate_subnet(self, context, s, cur_subnet=None):
        """Validate a subnet spec."""

        # This method will validate attributes which may change during
        # create_subnet() and update_subnet().
        # The method requires the subnet spec 's' has 'ip_version' field.
        # If 's' dict does not have 'ip_version' field in an API call
        # (e.g., update_subnet()), you need to set 'ip_version' field
        # before calling this method.

        ip_ver = s['ip_version']

        if attributes.is_attr_set(s.get('cidr')):
            self._validate_ip_version(ip_ver, s['cidr'], 'cidr')

        # TODO(watanabe.isao): After we found a way to avoid the re-sync
        # from the agent side, this restriction could be removed.
        if cur_subnet:
            dhcp_was_enabled = cur_subnet.enable_dhcp
        else:
            dhcp_was_enabled = False
        if s.get('enable_dhcp') and not dhcp_was_enabled:
            subnet_prefixlen = netaddr.IPNetwork(s['cidr']).prefixlen
            error_message = _("Subnet has a prefix length that is "
                              "incompatible with DHCP service enabled.")
            if ((ip_ver == 4 and subnet_prefixlen > 30) or
                (ip_ver == 6 and subnet_prefixlen > 126)):
                    raise n_exc.InvalidInput(error_message=error_message)
            else:
                # NOTE(watanabe.isao): The following restriction is necessary
                # only when updating subnet.
                if cur_subnet:
                    range_qry = context.session.query(models_v2.
                        IPAvailabilityRange).join(models_v2.IPAllocationPool)
                    ip_range = range_qry.filter_by(subnet_id=s['id']).first()
                    if not ip_range:
                        raise n_exc.IpAddressGenerationFailure(
                            net_id=cur_subnet.network_id)

        if attributes.is_attr_set(s.get('gateway_ip')):
            self._validate_ip_version(ip_ver, s['gateway_ip'], 'gateway_ip')
            if (cfg.CONF.force_gateway_on_subnet and
                not ipam.utils.check_gateway_in_subnet(
                    s['cidr'], s['gateway_ip'])):
                error_message = _("Gateway is not valid on subnet")
                raise n_exc.InvalidInput(error_message=error_message)
            # Ensure the gateway IP is not assigned to any port
            # skip this check in case of create (s parameter won't have id)
            # NOTE(salv-orlando): There is slight chance of a race, when
            # a subnet-update and a router-interface-add operation are
            # executed concurrently
            if cur_subnet:
                alloc_qry = context.session.query(models_v2.IPAllocation)
                allocated = alloc_qry.filter_by(
                    ip_address=cur_subnet['gateway_ip'],
                    subnet_id=cur_subnet['id']).first()
                if allocated and allocated['port_id']:
                    raise n_exc.GatewayIpInUse(
                        ip_address=cur_subnet['gateway_ip'],
                        port_id=allocated['port_id'])

        if attributes.is_attr_set(s.get('dns_nameservers')):
            if len(s['dns_nameservers']) > cfg.CONF.max_dns_nameservers:
                raise n_exc.DNSNameServersExhausted(
                    subnet_id=s.get('id', _('new subnet')),
                    quota=cfg.CONF.max_dns_nameservers)
            for dns in s['dns_nameservers']:
                try:
                    netaddr.IPAddress(dns)
                except Exception:
                    raise n_exc.InvalidInput(
                        error_message=(_("Error parsing dns address %s") %
                                       dns))
                self._validate_ip_version(ip_ver, dns, 'dns_nameserver')

        if attributes.is_attr_set(s.get('host_routes')):
            if len(s['host_routes']) > cfg.CONF.max_subnet_host_routes:
                raise n_exc.HostRoutesExhausted(
                    subnet_id=s.get('id', _('new subnet')),
                    quota=cfg.CONF.max_subnet_host_routes)
            # check if the routes are all valid
            for rt in s['host_routes']:
                self._validate_host_route(rt, ip_ver)

        if ip_ver == 4:
            if attributes.is_attr_set(s.get('ipv6_ra_mode')):
                raise n_exc.InvalidInput(
                    error_message=(_("ipv6_ra_mode is not valid when "
                                     "ip_version is 4")))
            if attributes.is_attr_set(s.get('ipv6_address_mode')):
                raise n_exc.InvalidInput(
                    error_message=(_("ipv6_address_mode is not valid when "
                                     "ip_version is 4")))
        if ip_ver == 6:
            self._validate_ipv6_attributes(s, cur_subnet)

    def _update_router_gw_ports(self, context, network, subnet):
        l3plugin = manager.NeutronManager.get_service_plugins().get(
                service_constants.L3_ROUTER_NAT)
        if l3plugin:
            gw_ports = self._get_router_gw_ports_by_network(context,
                    network['id'])
            router_ids = [p['device_id'] for p in gw_ports]
            ctx_admin = ctx.get_admin_context()
            ext_subnets_dict = {s['id']: s for s in network['subnets']}
            for id in router_ids:
                router = l3plugin.get_router(ctx_admin, id)
                external_gateway_info = router['external_gateway_info']
                # Get all stateful (i.e. non-SLAAC/DHCPv6-stateless) fixed ips
                fips = [f for f in external_gateway_info['external_fixed_ips']
                        if not ipv6_utils.is_auto_address_subnet(
                            ext_subnets_dict[f['subnet_id']])]
                num_fips = len(fips)
                # Don't add the fixed IP to the port if it already
                # has a stateful fixed IP of the same IP version
                if num_fips > 1:
                    continue
                if num_fips == 1 and netaddr.IPAddress(
                        fips[0]['ip_address']).version == subnet['ip_version']:
                    continue
                external_gateway_info['external_fixed_ips'].append(
                                             {'subnet_id': subnet['id']})
                info = {'router': {'external_gateway_info':
                    external_gateway_info}}
                l3plugin.update_router(context, id, info)

    def _create_subnet(self, context, subnet, subnetpool_id):
        s = subnet['subnet']

        with context.session.begin(subtransactions=True):
            network = self._get_network(context, s["network_id"])
            subnet, ipam_subnet = self.ipam.allocate_subnet(context,
                                                            network,
                                                            s,
                                                            subnetpool_id)
        if hasattr(network, 'external') and network.external:
            self._update_router_gw_ports(context,
                                         network,
                                         subnet)
        # If this subnet supports auto-addressing, then update any
        # internal ports on the network with addresses for this subnet.
        if ipv6_utils.is_auto_address_subnet(subnet):
            self.ipam.add_auto_addrs_on_network_ports(context, subnet,
                                                      ipam_subnet)
        return self._make_subnet_dict(subnet, context=context)

    def _get_subnetpool_id(self, subnet):
        """Returns the subnetpool id for this request

        If the pool id was explicitly set in the request then that will be
        returned, even if it is None.

        Otherwise, the default pool for the IP version requested will be
        returned.  This will either be a pool id or None (the default for each
        configuration parameter).  This implies that the ip version must be
        either set implicitly with a specific cidr or explicitly using
        ip_version attribute.

        :param subnet: The subnet dict from the request
        """
        subnetpool_id = subnet.get('subnetpool_id',
                                   attributes.ATTR_NOT_SPECIFIED)
        if subnetpool_id != attributes.ATTR_NOT_SPECIFIED:
            return subnetpool_id

        cidr = subnet.get('cidr')
        if attributes.is_attr_set(cidr):
            ip_version = netaddr.IPNetwork(cidr).version
        else:
            ip_version = subnet.get('ip_version')
            if not attributes.is_attr_set(ip_version):
                msg = _('ip_version must be specified in the absence of '
                        'cidr and subnetpool_id')
                raise n_exc.BadRequest(resource='subnets', msg=msg)

        if ip_version == 4:
            return cfg.CONF.default_ipv4_subnet_pool
        return cfg.CONF.default_ipv6_subnet_pool

    def create_subnet(self, context, subnet):

        s = subnet['subnet']
        cidr = s.get('cidr', attributes.ATTR_NOT_SPECIFIED)
        prefixlen = s.get('prefixlen', attributes.ATTR_NOT_SPECIFIED)
        has_cidr = attributes.is_attr_set(cidr)
        has_prefixlen = attributes.is_attr_set(prefixlen)

        if has_cidr and has_prefixlen:
            msg = _('cidr and prefixlen must not be supplied together')
            raise n_exc.BadRequest(resource='subnets', msg=msg)

        if has_cidr:
            # turn the CIDR into a proper subnet
            net = netaddr.IPNetwork(s['cidr'])
            subnet['subnet']['cidr'] = '%s/%s' % (net.network, net.prefixlen)

        s['tenant_id'] = self._get_tenant_id_for_create(context, s)
        subnetpool_id = self._get_subnetpool_id(s)
        if subnetpool_id:
            self.ipam.validate_pools_with_subnetpool(s)
        else:
            if not has_cidr:
                msg = _('A cidr must be specified in the absence of a '
                        'subnet pool')
                raise n_exc.BadRequest(resource='subnets', msg=msg)
            self._validate_subnet(context, s)

        return self._create_subnet(context, subnet, subnetpool_id)

    def update_subnet(self, context, id, subnet):
        """Update the subnet with new info.

        The change however will not be realized until the client renew the
        dns lease or we support gratuitous DHCP offers
        """
        s = subnet['subnet']
        db_subnet = self._get_subnet(context, id)
        # Fill 'ip_version' and 'allocation_pools' fields with the current
        # value since _validate_subnet() expects subnet spec has 'ip_version'
        # and 'allocation_pools' fields.
        s['ip_version'] = db_subnet.ip_version
        s['cidr'] = db_subnet.cidr
        s['id'] = db_subnet.id
        s['tenant_id'] = db_subnet.tenant_id
        self._validate_subnet(context, s, cur_subnet=db_subnet)
        db_pools = [netaddr.IPRange(p['first_ip'], p['last_ip'])
                    for p in db_subnet.allocation_pools]

        range_pools = None
        if s.get('allocation_pools') is not None:
            # Convert allocation pools to IPRange to simplify future checks
            range_pools = self.ipam.pools_to_ip_range(s['allocation_pools'])
            s['allocation_pools'] = range_pools

        if s.get('gateway_ip') is not None:
            pools = range_pools if range_pools is not None else db_pools
            self.ipam.validate_gw_out_of_pools(s["gateway_ip"], pools)

        with context.session.begin(subtransactions=True):
            subnet, changes = self.ipam.update_db_subnet(context, id, s,
                                                         db_pools)
        result = self._make_subnet_dict(subnet, context=context)
        # Keep up with fields that changed
        result.update(changes)
        return result

    def _subnet_check_ip_allocations(self, context, subnet_id):
        return (context.session.query(models_v2.IPAllocation).
                filter_by(subnet_id=subnet_id).join(models_v2.Port).first())

    def _subnet_get_user_allocation(self, context, subnet_id):
        """Check if there are any user ports on subnet and return first."""
        # need to join with ports table as IPAllocation's port
        # is not joined eagerly and thus producing query which yields
        # incorrect results
        return (context.session.query(models_v2.IPAllocation).
                filter_by(subnet_id=subnet_id).join(models_v2.Port).
                filter(~models_v2.Port.device_owner.
                       in_(AUTO_DELETE_PORT_OWNERS)).first())

    def _subnet_check_ip_allocations_internal_router_ports(self, context,
                                                           subnet_id):
        # Do not delete the subnet if IP allocations for internal
        # router ports still exist
        allocs = context.session.query(models_v2.IPAllocation).filter_by(
                subnet_id=subnet_id).join(models_v2.Port).filter(
                        models_v2.Port.device_owner.in_(
                            constants.ROUTER_INTERFACE_OWNERS)
                ).first()
        if allocs:
            LOG.debug("Subnet %s still has internal router ports, "
                      "cannot delete", subnet_id)
            raise n_exc.SubnetInUse(subnet_id=id)

    def delete_subnet(self, context, id):
        with context.session.begin(subtransactions=True):
            subnet = self._get_subnet(context, id)

            # Make sure the subnet isn't used by other resources
            _check_subnet_not_used(context, id)

            # Delete all network owned ports
            qry_network_ports = (
                context.session.query(models_v2.IPAllocation).
                filter_by(subnet_id=subnet['id']).
                join(models_v2.Port))
            # Remove network owned ports, and delete IP allocations
            # for IPv6 addresses which were automatically generated
            # via SLAAC
            is_auto_addr_subnet = ipv6_utils.is_auto_address_subnet(subnet)
            if is_auto_addr_subnet:
                self._subnet_check_ip_allocations_internal_router_ports(
                        context, id)
            else:
                qry_network_ports = (
                    qry_network_ports.filter(models_v2.Port.device_owner.
                    in_(AUTO_DELETE_PORT_OWNERS)))
            network_ports = qry_network_ports.all()
            if network_ports:
                for port in network_ports:
                    context.session.delete(port)
            # Check if there are more IP allocations, unless
            # is_auto_address_subnet is True. In that case the check is
            # unnecessary. This additional check not only would be wasteful
            # for this class of subnet, but is also error-prone since when
            # the isolation level is set to READ COMMITTED allocations made
            # concurrently will be returned by this query
            if not is_auto_addr_subnet:
                alloc = self._subnet_check_ip_allocations(context, id)
                if alloc:
                    LOG.info(_LI("Found port (%(port_id)s, %(ip)s) having IP "
                                 "allocation on subnet "
                                 "%(subnet)s, cannot delete"),
                             {'ip': alloc.ip_address,
                              'port_id': alloc.port_id,
                              'subnet': id})
                    raise n_exc.SubnetInUse(subnet_id=id)

            context.session.delete(subnet)
            # Delete related ipam subnet manually,
            # since there is no FK relationship
            self.ipam.delete_subnet(context, id)

    def get_subnet(self, context, id, fields=None):
        subnet = self._get_subnet(context, id)
        return self._make_subnet_dict(subnet, fields, context=context)

    def get_subnets(self, context, filters=None, fields=None,
                    sorts=None, limit=None, marker=None,
                    page_reverse=False):
        return self._get_subnets(context, filters, fields, sorts, limit,
                                 marker, page_reverse)

    def get_subnets_count(self, context, filters=None):
        return self._get_collection_count(context, models_v2.Subnet,
                                          filters=filters)

    def _create_subnetpool_prefix(self, context, cidr, subnetpool_id):
        prefix_args = {'cidr': cidr, 'subnetpool_id': subnetpool_id}
        subnetpool_prefix = models_v2.SubnetPoolPrefix(**prefix_args)
        context.session.add(subnetpool_prefix)

    def create_subnetpool(self, context, subnetpool):
        """Create a subnetpool"""

        sp = subnetpool['subnetpool']
        sp_reader = subnet_alloc.SubnetPoolReader(sp)
        tenant_id = self._get_tenant_id_for_create(context, sp)
        with context.session.begin(subtransactions=True):
            pool_args = {'tenant_id': tenant_id,
                         'id': sp_reader.id,
                         'name': sp_reader.name,
                         'ip_version': sp_reader.ip_version,
                         'default_prefixlen':
                         sp_reader.default_prefixlen,
                         'min_prefixlen': sp_reader.min_prefixlen,
                         'max_prefixlen': sp_reader.max_prefixlen,
                         'shared': sp_reader.shared,
                         'default_quota': sp_reader.default_quota}
            subnetpool = models_v2.SubnetPool(**pool_args)
            context.session.add(subnetpool)
            for prefix in sp_reader.prefixes:
                self._create_subnetpool_prefix(context,
                                               prefix,
                                               subnetpool.id)

        return self._make_subnetpool_dict(subnetpool)

    def _update_subnetpool_prefixes(self, context, prefix_list, id):
        with context.session.begin(subtransactions=True):
            context.session.query(models_v2.SubnetPoolPrefix).filter_by(
                subnetpool_id=id).delete()
            for prefix in prefix_list:
                model_prefix = models_v2.SubnetPoolPrefix(cidr=prefix,
                                                      subnetpool_id=id)
                context.session.add(model_prefix)

    def _updated_subnetpool_dict(self, model, new_pool):
        updated = {}
        new_prefixes = new_pool.get('prefixes', attributes.ATTR_NOT_SPECIFIED)
        orig_prefixes = [str(x.cidr) for x in model['prefixes']]
        if new_prefixes is not attributes.ATTR_NOT_SPECIFIED:
            orig_set = netaddr.IPSet(orig_prefixes)
            new_set = netaddr.IPSet(new_prefixes)
            if not orig_set.issubset(new_set):
                msg = _("Existing prefixes must be "
                        "a subset of the new prefixes")
                raise n_exc.IllegalSubnetPoolPrefixUpdate(msg=msg)
            new_set.compact()
            updated['prefixes'] = [str(x.cidr) for x in new_set.iter_cidrs()]
        else:
            updated['prefixes'] = orig_prefixes

        for key in ['id', 'name', 'ip_version', 'min_prefixlen',
                    'max_prefixlen', 'default_prefixlen', 'shared',
                    'default_quota']:
            self._write_key(key, updated, model, new_pool)

        return updated

    def _write_key(self, key, update, orig, new_dict):
        new_val = new_dict.get(key, attributes.ATTR_NOT_SPECIFIED)
        if new_val is not attributes.ATTR_NOT_SPECIFIED:
            update[key] = new_dict[key]
        else:
            update[key] = orig[key]

    def update_subnetpool(self, context, id, subnetpool):
        """Update a subnetpool"""
        new_sp = subnetpool['subnetpool']

        with context.session.begin(subtransactions=True):
            orig_sp = self._get_subnetpool(context, id)
            updated = self._updated_subnetpool_dict(orig_sp, new_sp)
            updated['tenant_id'] = orig_sp.tenant_id
            reader = subnet_alloc.SubnetPoolReader(updated)
            orig_sp.update(self._filter_non_model_columns(
                                                      reader.subnetpool,
                                                      models_v2.SubnetPool))
            self._update_subnetpool_prefixes(context,
                                             reader.prefixes,
                                             id)
        for key in ['min_prefixlen', 'max_prefixlen', 'default_prefixlen']:
            updated['key'] = str(updated[key])

        return updated

    def get_subnetpool(self, context, id, fields=None):
        """Retrieve a subnetpool."""
        subnetpool = self._get_subnetpool(context, id)
        return self._make_subnetpool_dict(subnetpool, fields)

    def get_subnetpools(self, context, filters=None, fields=None,
                        sorts=None, limit=None, marker=None,
                        page_reverse=False):
        """Retrieve list of subnetpools."""
        marker_obj = self._get_marker_obj(context, 'subnetpool', limit, marker)
        collection = self._get_collection(context, models_v2.SubnetPool,
                                    self._make_subnetpool_dict,
                                    filters=filters, fields=fields,
                                    sorts=sorts,
                                    limit=limit,
                                    marker_obj=marker_obj,
                                    page_reverse=page_reverse)
        return collection

    def delete_subnetpool(self, context, id):
        """Delete a subnetpool."""
        with context.session.begin(subtransactions=True):
            subnetpool = self._get_subnetpool(context, id)
            subnets = self._get_subnets_by_subnetpool(context, id)
            if subnets:
                reason = _("Subnet pool has existing allocations")
                raise n_exc.SubnetPoolDeleteError(reason=reason)
            context.session.delete(subnetpool)

    def _check_mac_addr_update(self, context, port, new_mac, device_owner):
        if (device_owner and device_owner.startswith('network:')):
            raise n_exc.UnsupportedPortDeviceOwner(
                op=_("mac address update"), port_id=id,
                device_owner=device_owner)

    def create_port_bulk(self, context, ports):
        return self._create_bulk('port', context, ports)

    def _create_port_with_mac(self, context, network_id, port_data,
                              mac_address):
        try:
            # since this method could either be used within or outside the
            # transaction, use convenience method to avoid passing a flag
            with db_api.autonested_transaction(context.session):
                db_port = models_v2.Port(mac_address=mac_address, **port_data)
                context.session.add(db_port)
                return db_port
        except db_exc.DBDuplicateEntry:
            raise n_exc.MacAddressInUse(net_id=network_id, mac=mac_address)

    def _create_port(self, context, network_id, port_data):
        max_retries = cfg.CONF.mac_generation_retries
        for i in range(max_retries):
            mac = self._generate_mac()
            try:
                return self._create_port_with_mac(
                    context, network_id, port_data, mac)
            except n_exc.MacAddressInUse:
                LOG.debug('Generated mac %(mac_address)s exists on '
                          'network %(network_id)s',
                          {'mac_address': mac, 'network_id': network_id})

        LOG.error(_LE("Unable to generate mac address after %s attempts"),
                  max_retries)
        raise n_exc.MacAddressGenerationFailure(net_id=network_id)

    def create_port(self, context, port):
        p = port['port']
        port_id = p.get('id') or uuidutils.generate_uuid()
        network_id = p['network_id']
        # NOTE(jkoelker) Get the tenant_id outside of the session to avoid
        #                unneeded db action if the operation raises
        tenant_id = self._get_tenant_id_for_create(context, p)
        if p.get('device_owner'):
            self._enforce_device_owner_not_router_intf_or_device_id(
                context, p.get('device_owner'), p.get('device_id'), tenant_id)

        port_data = dict(tenant_id=tenant_id,
                         name=p['name'],
                         id=port_id,
                         network_id=network_id,
                         admin_state_up=p['admin_state_up'],
                         status=p.get('status', constants.PORT_STATUS_ACTIVE),
                         device_id=p['device_id'],
                         device_owner=p['device_owner'])

        with context.session.begin(subtransactions=True):
            # Ensure that the network exists.
            self._get_network(context, network_id)

            # Create the port
            if p['mac_address'] is attributes.ATTR_NOT_SPECIFIED:
                db_port = self._create_port(context, network_id, port_data)
                p['mac_address'] = db_port['mac_address']
            else:
                db_port = self._create_port_with_mac(
                    context, network_id, port_data, p['mac_address'])

            self.ipam.allocate_ips_for_port_and_store(context, port, port_id)

        return self._make_port_dict(db_port, process_extensions=False)

    def _validate_port_for_update(self, context, db_port, new_port, new_mac):
        changed_owner = 'device_owner' in new_port
        current_owner = (new_port.get('device_owner') or
                         db_port['device_owner'])
        changed_device_id = new_port.get('device_id') != db_port['device_id']
        current_device_id = new_port.get('device_id') or db_port['device_id']

        if current_owner and changed_device_id or changed_owner:
            self._enforce_device_owner_not_router_intf_or_device_id(
                context, current_owner, current_device_id,
                db_port['tenant_id'])

        if new_mac and new_mac != db_port['mac_address']:
            self._check_mac_addr_update(context, db_port,
                                        new_mac, current_owner)

    def update_port(self, context, id, port):
        new_port = port['port']

        with context.session.begin(subtransactions=True):
            port = self._get_port(context, id)
            new_mac = new_port.get('mac_address')
            self._validate_port_for_update(context, port, new_port, new_mac)
            changes = self.ipam.update_port_with_ips(context, port,
                                                     new_port, new_mac)
        result = self._make_port_dict(port)
        # Keep up with fields that changed
        if changes.original or changes.add or changes.remove:
            result['fixed_ips'] = self._make_fixed_ip_dict(
                changes.original + changes.add)
        return result

    def delete_port(self, context, id):
        with context.session.begin(subtransactions=True):
            self.ipam.delete_port(context, id)

    def delete_ports_by_device_id(self, context, device_id, network_id=None):
        query = (context.session.query(models_v2.Port.id)
                 .enable_eagerloads(False)
                 .filter(models_v2.Port.device_id == device_id))
        if network_id:
            query = query.filter(models_v2.Port.network_id == network_id)
        port_ids = [p[0] for p in query]
        for port_id in port_ids:
            try:
                self.delete_port(context, port_id)
            except n_exc.PortNotFound:
                # Don't raise if something else concurrently deleted the port
                LOG.debug("Ignoring PortNotFound when deleting port '%s'. "
                          "The port has already been deleted.",
                          port_id)

    def get_port(self, context, id, fields=None):
        port = self._get_port(context, id)
        return self._make_port_dict(port, fields)

    def _get_ports_query(self, context, filters=None, sorts=None, limit=None,
                         marker_obj=None, page_reverse=False):
        Port = models_v2.Port
        IPAllocation = models_v2.IPAllocation

        if not filters:
            filters = {}

        query = self._model_query(context, Port)

        fixed_ips = filters.pop('fixed_ips', {})
        ip_addresses = fixed_ips.get('ip_address')
        subnet_ids = fixed_ips.get('subnet_id')
        if ip_addresses or subnet_ids:
            query = query.join(Port.fixed_ips)
            if ip_addresses:
                query = query.filter(IPAllocation.ip_address.in_(ip_addresses))
            if subnet_ids:
                query = query.filter(IPAllocation.subnet_id.in_(subnet_ids))

        query = self._apply_filters_to_query(query, Port, filters, context)
        if limit and page_reverse and sorts:
            sorts = [(s[0], not s[1]) for s in sorts]
        query = sqlalchemyutils.paginate_query(query, Port, limit,
                                               sorts, marker_obj)
        return query

    def get_ports(self, context, filters=None, fields=None,
                  sorts=None, limit=None, marker=None,
                  page_reverse=False):
        marker_obj = self._get_marker_obj(context, 'port', limit, marker)
        query = self._get_ports_query(context, filters=filters,
                                      sorts=sorts, limit=limit,
                                      marker_obj=marker_obj,
                                      page_reverse=page_reverse)
        items = [self._make_port_dict(c, fields) for c in query]
        if limit and page_reverse:
            items.reverse()
        return items

    def get_ports_count(self, context, filters=None):
        return self._get_ports_query(context, filters).count()

    def _enforce_device_owner_not_router_intf_or_device_id(self, context,
                                                           device_owner,
                                                           device_id,
                                                           tenant_id):
        """Prevent tenants from replacing the device id of router ports with
        a router uuid belonging to another tenant.
        """
        if device_owner not in constants.ROUTER_INTERFACE_OWNERS:
            return
        if not context.is_admin:
            # check to make sure device_id does not match another tenants
            # router.
            if device_id:
                if hasattr(self, 'get_router'):
                    try:
                        ctx_admin = ctx.get_admin_context()
                        router = self.get_router(ctx_admin, device_id)
                    except l3.RouterNotFound:
                        return
                else:
                    l3plugin = (
                        manager.NeutronManager.get_service_plugins().get(
                            service_constants.L3_ROUTER_NAT))
                    if l3plugin:
                        try:
                            ctx_admin = ctx.get_admin_context()
                            router = l3plugin.get_router(ctx_admin,
                                                         device_id)
                        except l3.RouterNotFound:
                            return
                    else:
                        # raise as extension doesn't support L3 anyways.
                        raise n_exc.DeviceIDNotOwnedByTenant(
                            device_id=device_id)
                if tenant_id != router['tenant_id']:
                    raise n_exc.DeviceIDNotOwnedByTenant(device_id=device_id)
