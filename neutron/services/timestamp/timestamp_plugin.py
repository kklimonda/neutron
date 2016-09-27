# Copyright 2015 HuaWei Technologies.
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
from neutron.db import models_v2
from neutron.services import service_base
from neutron.services.timestamp import timestamp_db as ts_db


class TimeStampPlugin(service_base.ServicePluginBase,
                      ts_db.TimeStamp_db_mixin):
    """Implements Neutron Timestamp Service plugin."""

    supported_extension_aliases = ['timestamp_core']

    def __init__(self):
        super(TimeStampPlugin, self).__init__()
        self.register_db_events()
        for resources in [attributes.NETWORKS, attributes.PORTS,
                          attributes.SUBNETS, attributes.SUBNETPOOLS]:
            db_base_plugin_v2.NeutronDbPluginV2.register_dict_extend_funcs(
                resources, [self.extend_resource_dict_timestamp])

        for model in [models_v2.Network, models_v2.Port, models_v2.Subnet,
                      models_v2.SubnetPool]:
            db_base_plugin_v2.NeutronDbPluginV2.register_model_query_hook(
                model,
                "change_since_query",
                None,
                None,
                self._change_since_result_filter_hook)

    def get_plugin_type(self):
        return 'timestamp_core'

    def get_plugin_description(self):
        return "Neutron core resources timestamp addition support"
