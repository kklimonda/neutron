..
      Licensed under the Apache License, Version 2.0 (the "License"); you may
      not use this file except in compliance with the License. You may obtain
      a copy of the License at

          http://www.apache.org/licenses/LICENSE-2.0

      Unless required by applicable law or agreed to in writing, software
      distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
      WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
      License for the specific language governing permissions and limitations
      under the License.


      Convention for heading levels in Neutron devref:
      =======  Heading 0 (reserved for the title in a document)
      -------  Heading 1
      ~~~~~~~  Heading 2
      +++++++  Heading 3
      '''''''  Heading 4
      (Avoid deeper levels because they do not render well.)


L2 Networking with Linux Bridge
===============================

This Agent uses the `Linux Bridge
<http://www.linuxfoundation.org/collaborate/workgroups/networking/bridge>`_ to
provide L2 connectivity for VM instances running on the compute node to the
public network.  A graphical illustration of the deployment can be found in
`Networking Guide
<http://docs.openstack.org/networking-guide/scenario_legacy_lb.html>`_

In most common deployments, there is a compute and a network node. On both the
compute and the network node, the Linux Bridge Agent will manage virtual
switches, connectivity among them, and interaction via virtual ports with other
network components such as namespaces and underlying interfaces. Additionally,
on the compute node, the Linux Bridge Agent will manage security groups.

Three use cases and their packet flow are documented as follows:

1. `Legacy implementation with Linux Bridge
   <http://docs.openstack.org/networking-guide/deploy_scenario1b.html>`_

2. `High Availability using L3HA with Linux Bridge
   <http://docs.openstack.org/networking-guide/deploy_scenario3b.html>`_

3. `Provider networks with Linux Bridge
   <http://docs.openstack.org/networking-guide/deploy_scenario4b.html>`_
