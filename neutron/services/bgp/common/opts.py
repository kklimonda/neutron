# Copyright 2016 Huawei Technologies India Pvt. Ltd.
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import itertools

import neutron.services.bgp.agent.config


def list_bgp_agent_opts():
    return [
        ('BGP',
         itertools.chain(
             neutron.services.bgp.agent.config.BGP_DRIVER_OPTS,
             neutron.services.bgp.agent.config.BGP_PROTO_CONFIG_OPTS)
         )
    ]
