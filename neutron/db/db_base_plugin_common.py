# Copyright (c) 2015 OpenStack Foundation.
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

from neutron_lib.api.definitions import network as net_def
from neutron_lib.api.definitions import port as port_def
from neutron_lib.api.definitions import subnet as subnet_def
from neutron_lib.api.definitions import subnetpool as subnetpool_def
from neutron_lib.api import validators
from neutron_lib import constants
from neutron_lib import exceptions as n_exc
from neutron_lib.utils import net
from oslo_config import cfg
from oslo_log import log as logging
from sqlalchemy.orm import exc

from neutron.common import constants as n_const
from neutron.common import exceptions
from neutron.db import _model_query as model_query
from neutron.db import _resource_extend as resource_extend
from neutron.db import _utils as db_utils
from neutron.db import api as db_api
from neutron.db import common_db_mixin
from neutron.db import models_v2
from neutron.objects import ports as port_obj
from neutron.objects import subnet as subnet_obj
from neutron.objects import subnetpool as subnetpool_obj

LOG = logging.getLogger(__name__)


def convert_result_to_dict(f):
    @functools.wraps(f)
    def inner(*args, **kwargs):
        result = f(*args, **kwargs)

        if result is None:
            return None
        elif isinstance(result, list):
            return [r.to_dict() for r in result]
        else:
            return result.to_dict()
    return inner


def filter_fields(f):
    @functools.wraps(f)
    def inner_filter(*args, **kwargs):
        result = f(*args, **kwargs)
        fields = kwargs.get('fields')
        if not fields:
            try:
                pos = f.__code__.co_varnames.index('fields')
                fields = args[pos]
            except (IndexError, ValueError):
                return result

        do_filter = lambda d: {k: v for k, v in d.items() if k in fields}
        if isinstance(result, list):
            return [do_filter(obj) for obj in result]
        else:
            return do_filter(result)
    return inner_filter


class DbBasePluginCommon(common_db_mixin.CommonDbMixin):
    """Stores getters and helper methods for db_base_plugin_v2

    All private getters and simple helpers like _make_*_dict were moved from
    db_base_plugin_v2.
    More complicated logic and public methods left in db_base_plugin_v2.
    Main purpose of this class is to make getters accessible for Ipam
    backends.
    """

    @staticmethod
    def _generate_mac():
        return net.get_random_mac(cfg.CONF.base_mac.split(':'))

    @db_api.context_manager.reader
    def _is_mac_in_use(self, context, network_id, mac_address):
        return bool(context.session.query(models_v2.Port).
                    filter(models_v2.Port.network_id == network_id).
                    filter(models_v2.Port.mac_address == mac_address).
                    count())

    @staticmethod
    def _delete_ip_allocation(context, network_id, subnet_id, ip_address):

        # Delete the IP address from the IPAllocate table
        LOG.debug("Delete allocated IP %(ip_address)s "
                  "(%(network_id)s/%(subnet_id)s)",
                  {'ip_address': ip_address,
                   'network_id': network_id,
                   'subnet_id': subnet_id})
        port_obj.IPAllocation.delete_objects(
            context, network_id=network_id, ip_address=ip_address,
            subnet_id=subnet_id)

    @staticmethod
    @db_api.context_manager.writer
    def _store_ip_allocation(context, ip_address, network_id, subnet_id,
                             port_id):
        LOG.debug("Allocated IP %(ip_address)s "
                  "(%(network_id)s/%(subnet_id)s/%(port_id)s)",
                  {'ip_address': ip_address,
                   'network_id': network_id,
                   'subnet_id': subnet_id,
                   'port_id': port_id})
        allocated = port_obj.IPAllocation(
            context, network_id=network_id, port_id=port_id,
            ip_address=ip_address, subnet_id=subnet_id)
        # NOTE(lujinluo): Add IPAllocations obj to the port fixed_ips
        # in Port OVO integration, i.e. the same way we did in
        # Ib32509d974c8654131112234bcf19d6eae8f7cca
        allocated.create()

    def _make_subnet_dict(self, subnet, fields=None, context=None):
        res = {'id': subnet['id'],
               'name': subnet['name'],
               'tenant_id': subnet['tenant_id'],
               'network_id': subnet['network_id'],
               'ip_version': subnet['ip_version'],
               'cidr': subnet['cidr'],
               'subnetpool_id': subnet.get('subnetpool_id'),
               'allocation_pools': [{'start': pool['first_ip'],
                                     'end': pool['last_ip']}
                                    for pool in subnet['allocation_pools']],
               'gateway_ip': subnet['gateway_ip'],
               'enable_dhcp': subnet['enable_dhcp'],
               'ipv6_ra_mode': subnet['ipv6_ra_mode'],
               'ipv6_address_mode': subnet['ipv6_address_mode'],
               'dns_nameservers': [dns['address']
                                   for dns in subnet['dns_nameservers']],
               'host_routes': [{'destination': route['destination'],
                                'nexthop': route['nexthop']}
                               for route in subnet['routes']],
               }
        # The shared attribute for a subnet is the same as its parent network
        res['shared'] = self._is_network_shared(context, subnet.rbac_entries)
        # Call auxiliary extend functions, if any
        resource_extend.apply_funcs(subnet_def.COLLECTION_NAME, res, subnet)
        return db_utils.resource_fields(res, fields)

    def _make_subnetpool_dict(self, subnetpool, fields=None):
        default_prefixlen = str(subnetpool['default_prefixlen'])
        min_prefixlen = str(subnetpool['min_prefixlen'])
        max_prefixlen = str(subnetpool['max_prefixlen'])
        res = {'id': subnetpool['id'],
               'name': subnetpool['name'],
               'tenant_id': subnetpool['tenant_id'],
               'default_prefixlen': default_prefixlen,
               'min_prefixlen': min_prefixlen,
               'max_prefixlen': max_prefixlen,
               'is_default': subnetpool['is_default'],
               'shared': subnetpool['shared'],
               'prefixes': [prefix.cidr for prefix in subnetpool['prefixes']],
               'ip_version': subnetpool['ip_version'],
               'default_quota': subnetpool['default_quota'],
               'address_scope_id': subnetpool['address_scope_id']}
        resource_extend.apply_funcs(
            subnetpool_def.COLLECTION_NAME, res, subnetpool)
        return db_utils.resource_fields(res, fields)

    def _make_port_dict(self, port, fields=None,
                        process_extensions=True):
        res = {"id": port["id"],
               'name': port['name'],
               "network_id": port["network_id"],
               'tenant_id': port['tenant_id'],
               "mac_address": port["mac_address"],
               "admin_state_up": port["admin_state_up"],
               "status": port["status"],
               "fixed_ips": [{'subnet_id': ip["subnet_id"],
                              'ip_address': ip["ip_address"]}
                             for ip in port["fixed_ips"]],
               "device_id": port["device_id"],
               "device_owner": port["device_owner"]}
        # Call auxiliary extend functions, if any
        if process_extensions:
            resource_extend.apply_funcs(port_def.COLLECTION_NAME, res, port)
        return db_utils.resource_fields(res, fields)

    def _get_network(self, context, id):
        try:
            network = model_query.get_by_id(context, models_v2.Network, id)
        except exc.NoResultFound:
            raise n_exc.NetworkNotFound(net_id=id)
        return network

    def _get_subnet(self, context, id):
        try:
            subnet = model_query.get_by_id(context, models_v2.Subnet, id)
        except exc.NoResultFound:
            raise n_exc.SubnetNotFound(subnet_id=id)
        return subnet

    def _get_subnetpool(self, context, id):
        subnetpool = subnetpool_obj.SubnetPool.get_object(
            context, id=id)
        if not subnetpool:
            raise exceptions.SubnetPoolNotFound(subnetpool_id=id)
        return subnetpool

    def _get_port(self, context, id):
        try:
            port = model_query.get_by_id(context, models_v2.Port, id)
        except exc.NoResultFound:
            raise n_exc.PortNotFound(port_id=id)
        return port

    def _get_route_by_subnet(self, context, subnet_id):
        return subnet_obj.Route.get_objects(context,
                                            subnet_id=subnet_id)

    def _get_router_gw_ports_by_network(self, context, network_id):
        port_qry = context.session.query(models_v2.Port)
        return port_qry.filter_by(network_id=network_id,
                device_owner=constants.DEVICE_OWNER_ROUTER_GW).all()

    @db_api.context_manager.reader
    def _get_subnets_by_network(self, context, network_id):
        subnet_qry = context.session.query(models_v2.Subnet)
        return subnet_qry.filter_by(network_id=network_id).all()

    @db_api.context_manager.reader
    def _get_subnets_by_subnetpool(self, context, subnetpool_id):
        subnet_qry = context.session.query(models_v2.Subnet)
        return subnet_qry.filter_by(subnetpool_id=subnetpool_id).all()

    @db_api.context_manager.reader
    def _get_all_subnets(self, context):
        # NOTE(salvatore-orlando): This query might end up putting
        # a lot of stress on the db. Consider adding a cache layer
        return context.session.query(models_v2.Subnet).all()

    def _get_subnets(self, context, filters=None, fields=None,
                     sorts=None, limit=None, marker=None,
                     page_reverse=False):
        marker_obj = db_utils.get_marker_obj(self, context, 'subnet',
                                             limit, marker)
        make_subnet_dict = functools.partial(self._make_subnet_dict,
                                             context=context)
        return model_query.get_collection(context, models_v2.Subnet,
                                          make_subnet_dict,
                                          filters=filters, fields=fields,
                                          sorts=sorts,
                                          limit=limit,
                                          marker_obj=marker_obj,
                                          page_reverse=page_reverse)

    def _make_network_dict(self, network, fields=None,
                           process_extensions=True, context=None):
        res = {'id': network['id'],
               'name': network['name'],
               'tenant_id': network['tenant_id'],
               'admin_state_up': network['admin_state_up'],
               'mtu': network.get('mtu', n_const.DEFAULT_NETWORK_MTU),
               'status': network['status'],
               'subnets': [subnet['id']
                           for subnet in network['subnets']]}
        res['shared'] = self._is_network_shared(context, network.rbac_entries)
        # Call auxiliary extend functions, if any
        if process_extensions:
            resource_extend.apply_funcs(net_def.COLLECTION_NAME, res, network)
        return db_utils.resource_fields(res, fields)

    def _is_network_shared(self, context, rbac_entries):
        # The shared attribute for a network now reflects if the network
        # is shared to the calling tenant via an RBAC entry.
        matches = ('*',) + ((context.tenant_id,) if context else ())
        for entry in rbac_entries:
            if (entry.action == 'access_as_shared' and
                    entry.target_tenant in matches):
                return True
        return False

    def _make_subnet_args(self, detail, subnet, subnetpool_id):
        gateway_ip = str(detail.gateway_ip) if detail.gateway_ip else None
        args = {'tenant_id': detail.tenant_id,
                'id': detail.subnet_id,
                'name': subnet['name'],
                'network_id': subnet['network_id'],
                'ip_version': subnet['ip_version'],
                'cidr': str(detail.subnet_cidr),
                'subnetpool_id': subnetpool_id,
                'enable_dhcp': subnet['enable_dhcp'],
                'gateway_ip': gateway_ip,
                'description': subnet.get('description')}
        if subnet['ip_version'] == 6 and subnet['enable_dhcp']:
            if validators.is_attr_set(subnet['ipv6_ra_mode']):
                args['ipv6_ra_mode'] = subnet['ipv6_ra_mode']
            if validators.is_attr_set(subnet['ipv6_address_mode']):
                args['ipv6_address_mode'] = subnet['ipv6_address_mode']
        return args

    def _make_fixed_ip_dict(self, ips):
        # Excludes from dict all keys except subnet_id and ip_address
        return [{'subnet_id': ip["subnet_id"],
                 'ip_address': ip["ip_address"]}
                for ip in ips]
