# Copyright 2015 OpenStack Foundation
#
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

import sys

from oslo_config import cfg
from oslo_service import service

from neutron.agent.common import config
from neutron.agent.dhcp import config as dhcp_config
from neutron.agent.linux import interface
from neutron.agent.metadata import config as metadata_config
from neutron.common import config as common_config
from neutron.common import topics
from neutron import service as neutron_service


def register_options(conf):
    config.register_interface_driver_opts_helper(conf)
    config.register_agent_state_opts_helper(conf)
    config.register_availability_zone_opts_helper(conf)
    conf.register_opts(dhcp_config.DHCP_AGENT_OPTS)
    conf.register_opts(dhcp_config.DHCP_OPTS)
    conf.register_opts(dhcp_config.DNSMASQ_OPTS)
    conf.register_opts(metadata_config.DRIVER_OPTS)
    conf.register_opts(metadata_config.SHARED_OPTS)
    conf.register_opts(interface.OPTS)


def main():
    register_options(cfg.CONF)
    common_config.init(sys.argv[1:])
    config.setup_logging()
    server = neutron_service.Service.create(
        binary='neutron-dhcp-agent',
        topic=topics.DHCP_AGENT,
        report_interval=cfg.CONF.AGENT.report_interval,
        manager='neutron.agent.dhcp.agent.DhcpAgentWithStateReport')
    service.launch(cfg.CONF, server).wait()
