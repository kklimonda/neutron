# Copyright (c) 2013 OpenStack Foundation.
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

from neutron.api.v2 import attributes
from neutron.db import db_base_plugin_v2
from neutron.extensions import extra_dhcp_opt as edo_ext
from neutron.objects.port.extensions import extra_dhcp_opt as obj_extra_dhcp


class ExtraDhcpOptMixin(object):
    """Mixin class to add extra options to the DHCP opts file
    and associate them to a port.
    """

    def _is_valid_opt_value(self, opt_name, opt_value):
        # If the dhcp opt is blank-able, it shouldn't be saved to the DB in
        # case that the value is None
        if opt_name in edo_ext.VALID_BLANK_EXTRA_DHCP_OPTS:
            return opt_value is not None

        # Otherwise, it shouldn't be saved to the DB in case that the value
        # is None or empty
        return bool(opt_value)

    def _process_port_create_extra_dhcp_opts(self, context, port,
                                             extra_dhcp_opts):
        if not extra_dhcp_opts:
            return port
        with context.session.begin(subtransactions=True):
            for dopt in extra_dhcp_opts:
                if self._is_valid_opt_value(dopt['opt_name'],
                                            dopt['opt_value']):
                    ip_version = dopt.get('ip_version', 4)
                    extra_dhcp_obj = obj_extra_dhcp.ExtraDhcpOpt(
                        context,
                        port_id=port['id'],
                        opt_name=dopt['opt_name'],
                        opt_value=dopt['opt_value'],
                        ip_version=ip_version)
                    extra_dhcp_obj.create()
        return self._extend_port_extra_dhcp_opts_dict(context, port)

    def _extend_port_extra_dhcp_opts_dict(self, context, port):
        port[edo_ext.EXTRADHCPOPTS] = self._get_port_extra_dhcp_opts_binding(
            context, port['id'])

    def _get_port_extra_dhcp_opts_binding(self, context, port_id):
        opts = obj_extra_dhcp.ExtraDhcpOpt.get_objects(
                            context, port_id=port_id)
        # TODO(mhickey): When port serilization is available then
        # the object list should be returned instead
        return [{'opt_name': r.opt_name, 'opt_value': r.opt_value,
                 'ip_version': r.ip_version}
                for r in opts]

    def _update_extra_dhcp_opts_on_port(self, context, id, port,
                                        updated_port=None):
        # It is not necessary to update in a transaction, because
        # its called from within one from ovs_neutron_plugin.
        dopts = port['port'].get(edo_ext.EXTRADHCPOPTS)

        if dopts:
            opts = obj_extra_dhcp.ExtraDhcpOpt.get_objects(
                                context, port_id=id)
            # if there are currently no dhcp_options associated to
            # this port, Then just insert the new ones and be done.
            with context.session.begin(subtransactions=True):
                for upd_rec in dopts:
                    for opt in opts:
                        if (opt['opt_name'] == upd_rec['opt_name']
                                and opt['ip_version'] == upd_rec.get(
                                    'ip_version', 4)):
                            # to handle deleting of a opt from the port.
                            if upd_rec['opt_value'] is None:
                                opt.delete()
                            else:
                                if (self._is_valid_opt_value(
                                        opt['opt_name'],
                                        upd_rec['opt_value']) and
                                        opt['opt_value'] !=
                                        upd_rec['opt_value']):
                                    opt['opt_value'] = upd_rec['opt_value']
                                    opt.update()
                            break
                    else:
                        if self._is_valid_opt_value(
                                upd_rec['opt_name'],
                                upd_rec['opt_value']):
                            ip_version = upd_rec.get('ip_version', 4)
                            extra_dhcp_obj = obj_extra_dhcp.ExtraDhcpOpt(
                                context,
                                port_id=id,
                                opt_name=upd_rec['opt_name'],
                                opt_value=upd_rec['opt_value'],
                                ip_version=ip_version)
                            extra_dhcp_obj.create()

            if updated_port:
                edolist = self._get_port_extra_dhcp_opts_binding(context, id)
                updated_port[edo_ext.EXTRADHCPOPTS] = edolist

        return bool(dopts)

    def _extend_port_dict_extra_dhcp_opt(self, res, port):
        res[edo_ext.EXTRADHCPOPTS] = [{'opt_name': dho.opt_name,
                                       'opt_value': dho.opt_value,
                                       'ip_version': dho.ip_version}
                                      for dho in port.dhcp_opts]
        return res

    db_base_plugin_v2.NeutronDbPluginV2.register_dict_extend_funcs(
        attributes.PORTS, ['_extend_port_dict_extra_dhcp_opt'])
