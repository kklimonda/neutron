.. _config-routed-provider-networks:

========================
Routed provider networks
========================

.. note::

   Use of this feature requires the OpenStack client
   version 3.3 or newer.

Before routed provider networks, the Networking service could not present a
multi-segment layer-3 network as a single entity. Thus, each operator typically
chose one of the following architectures:

* Single large layer-2 network
* Multiple smaller layer-2 networks

Single large layer-2 networks become complex at scale and involve significant
failure domains.

Multiple smaller layer-2 networks scale better and shrink failure domains, but
leave network selection to the user. Without additional information, users
cannot easily differentiate these networks.

A routed provider network enables a single provider network to represent
multiple layer-2 networks (broadcast domains) or segments and enables the
operator to present one network to users. However, the particular IP
addresses available to an instance depend on the segment of the network
available on the particular compute node.

Similar to conventional networking, layer-2 (switching) handles transit of
traffic between ports on the same segment and layer-3 (routing) handles
transit of traffic between segments.

Each segment requires at least one subnet that explicitly belongs to that
segment. The association between a segment and a subnet distinguishes a
routed provider network from other types of networks. The Networking service
enforces that either zero or all subnets on a particular network associate
with a segment. For example, attempting to create a subnet without a segment
on a network containing subnets with segments generates an error.

The Networking service does not provide layer-3 services between segments.
Instead, it relies on physical network infrastructure to route subnets.
Thus, both the Networking service and physical network infrastructure must
contain configuration for routed provider networks, similar to conventional
provider networks. In the future, implementation of dynamic routing protocols
may ease configuration of routed networks.

Prerequisites
~~~~~~~~~~~~~

Routed provider networks require additional prerequisites over conventional
provider networks. We recommend using the following procedure:

#. Begin with segments. The Networking service defines a segment using the
   following components:

   * Unique physical network name
   * Segmentation type
   * Segmentation ID

   For example, ``provider1``, ``VLAN``, and ``2016``. See the
   `API reference <https://developer.openstack.org/api-ref/networking/v2/#segments>`__
   for more information.

   Within a network, use a unique physical network name for each segment which
   enables reuse of the same segmentation details between subnets. For
   example, using the same VLAN ID across all segments of a particular
   provider network. Similar to conventional provider networks, the operator
   must provision the layer-2 physical network infrastructure accordingly.

#. Implement routing between segments.

   The Networking service does not provision routing among segments. The
   operator must implement routing among segments of a provider network.
   Each subnet on a segment must contain the gateway address of the
   router interface on that particular subnet. For example:

   =========== ======= ======================= =====================
   Segment     Version Addresses               Gateway
   =========== ======= ======================= =====================
   segment1    4       203.0.113.0/24          203.0.113.1
   segment1    6       fd00:203:0:113::/64     fd00:203:0:113::1
   segment2    4       198.51.100.0/24         198.51.100.1
   segment2    6       fd00:198:51:100::/64    fd00:198:51:100::1
   =========== ======= ======================= =====================

#. Map segments to compute nodes.

   Routed provider networks imply that compute nodes reside on different
   segments. The operator must ensure that every compute host that is supposed
   to participate in a router provider network has direct connectivity to one
   of its segments.

   =========== ====== ================
   Host        Rack   Physical Network
   =========== ====== ================
   compute0001 rack 1 segment 1
   compute0002 rack 1 segment 1
   ...         ...    ...
   compute0101 rack 2 segment 2
   compute0102 rack 2 segment 2
   compute0102 rack 2 segment 2
   ...         ...    ...
   =========== ====== ================

#. Deploy DHCP agents.

   Unlike conventional provider networks, a DHCP agent cannot support more
   than one segment within a network. The operator must deploy at least one
   DHCP agent per segment. Consider deploying DHCP agents on compute nodes
   containing the segments rather than one or more network nodes to reduce
   node count.

   =========== ====== ================
   Host        Rack   Physical Network
   =========== ====== ================
   network0001 rack 1 segment 1
   network0002 rack 2 segment 2
   ...         ...    ...
   =========== ====== ================

#. Configure communication of the Networking service with the Compute
   scheduler.

   An instance with an interface with an IPv4 address in a routed provider
   network must be placed by the Compute scheduler in a host that has access to
   a segment with available IPv4 addresses. To make this possible, the
   Networking service communicates to the Compute scheduler the inventory of
   IPv4 addresses associated with each segment of a routed provider network.
   The operator must configure the authentication credentials that the
   Networking service will use to communicate with the Compute scheduler's
   placement API. Please see below an example configuration.

   .. note::

      Coordination between the Networking service and the Compute scheduler is
      not necessary for IPv6 subnets as a consequence of their large address
      spaces.

   .. note::

      The coordination between the Networking service and the Compute scheduler
      requires the following minimum API micro-versions.

      * Compute service API: 2.41
      * Placement API: 1.1

Example configuration
~~~~~~~~~~~~~~~~~~~~~

Controller node
---------------

#. Enable the segments service plug-in by appending ``segments`` to the list
   of ``service_plugins`` in the ``neutron.conf`` file on all nodes running the
   ``neutron-server`` service:

   .. code-block:: ini

      [DEFAULT]
      # ...
      service_plugins = ..., segments

#. Add a ``placement`` section to the ``neutron.conf`` file with authentication
   credentials for the Compute service placement API:

   .. code-block:: ini

      [placement]
      auth_uri = http://192.0.2.72/identity
      project_domain_name = Default
      project_name = service
      user_domain_name = Default
      password = apassword
      username = nova
      auth_url = http://192.0.2.72/identity_admin
      auth_type = password
      region_name = RegionOne

#. Restart the ``neutron-server`` service.

Network or compute nodes
------------------------

* Configure the layer-2 agent on each node to map one or more segments to
  the appropriate physical network bridge or interface and restart the
  agent.

Create a routed provider network
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The following steps create a routed provider network with two segments. Each
segment contains one IPv4 subnet and one IPv6 subnet.

#. Source the administrative project credentials.
#. Create a VLAN provider network which includes a default segment. In this
   example, the network uses the ``provider1`` physical network with VLAN ID
   2016.

   .. code-block:: console

      $ openstack network create --share --provider-physical-network provider1 \
        --provider-network-type vlan --provider-segment 2016 multisegment1
      +---------------------------+--------------------------------------+
      | Field                     | Value                                |
      +---------------------------+--------------------------------------+
      | admin_state_up            | UP                                   |
      | id                        | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 |
      | ipv4_address_scope        | None                                 |
      | ipv6_address_scope        | None                                 |
      | l2_adjacency              | True                                 |
      | mtu                       | 1500                                 |
      | name                      | multisegment1                        |
      | port_security_enabled     | True                                 |
      | provider:network_type     | vlan                                 |
      | provider:physical_network | provider1                            |
      | provider:segmentation_id  | 2016                                 |
      | router:external           | Internal                             |
      | shared                    | True                                 |
      | status                    | ACTIVE                               |
      | subnets                   |                                      |
      | tags                      | []                                   |
      +---------------------------+--------------------------------------+

#. Rename the default segment to ``segment1``.

   .. code-block:: console

      $ openstack network segment list --network multisegment1
      +--------------------------------------+----------+--------------------------------------+--------------+---------+
      | ID                                   | Name     | Network                              | Network Type | Segment |
      +--------------------------------------+----------+--------------------------------------+--------------+---------+
      | 43e16869-ad31-48e4-87ce-acf756709e18 | None     | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 | vlan         |    2016 |
      +--------------------------------------+----------+--------------------------------------+--------------+---------+

   .. code-block:: console

      $ openstack network segment set --name segment1 43e16869-ad31-48e4-87ce-acf756709e18

   .. note::

      This command provides no output.

#. Create a second segment on the provider network. In this example, the
   segment uses the ``provider2`` physical network with VLAN ID 2016.

   .. code-block:: console

      $ openstack network segment create --physical-network provider2 \
        --network-type vlan --segment 2016 --network multisegment1 segment2
      +------------------+--------------------------------------+
      | Field            | Value                                |
      +------------------+--------------------------------------+
      | description      | None                                 |
      | headers          |                                      |
      | id               | 053b7925-9a89-4489-9992-e164c8cc8763 |
      | name             | segment2                             |
      | network_id       | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 |
      | network_type     | vlan                                 |
      | physical_network | provider2                            |
      | segmentation_id  | 2016                                 |
      +------------------+--------------------------------------+

#. Verify that the network contains the ``segment1`` and ``segment2`` segments.

   .. code-block:: console

      $ openstack network segment list --network multisegment1
      +--------------------------------------+----------+--------------------------------------+--------------+---------+
      | ID                                   | Name     | Network                              | Network Type | Segment |
      +--------------------------------------+----------+--------------------------------------+--------------+---------+
      | 053b7925-9a89-4489-9992-e164c8cc8763 | segment2 | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 | vlan         |    2016 |
      | 43e16869-ad31-48e4-87ce-acf756709e18 | segment1 | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 | vlan         |    2016 |
      +--------------------------------------+----------+--------------------------------------+--------------+---------+

#. Create subnets on the ``segment1`` segment. In this example, the IPv4
   subnet uses 203.0.113.0/24 and the IPv6 subnet uses fd00:203:0:113::/64.

   .. code-block:: console

      $ openstack subnet create \
        --network multisegment1 --network-segment segment1 \
        --ip-version 4 --subnet-range 203.0.113.0/24 \
        multisegment1-segment1-v4
      +-------------------+--------------------------------------+
      | Field             | Value                                |
      +-------------------+--------------------------------------+
      | allocation_pools  | 203.0.113.2-203.0.113.254            |
      | cidr              | 203.0.113.0/24                       |
      | enable_dhcp       | True                                 |
      | gateway_ip        | 203.0.113.1                          |
      | id                | c428797a-6f8e-4cb1-b394-c404318a2762 |
      | ip_version        | 4                                    |
      | name              | multisegment1-segment1-v4            |
      | network_id        | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 |
      | segment_id        | 43e16869-ad31-48e4-87ce-acf756709e18 |
      +-------------------+--------------------------------------+

      $ openstack subnet create \
        --network multisegment1 --network-segment segment1 \
        --ip-version 6 --subnet-range fd00:203:0:113::/64 \
        --ipv6-address-mode slaac multisegment1-segment1-v6
      +-------------------+------------------------------------------------------+
      | Field             | Value                                                |
      +-------------------+------------------------------------------------------+
      | allocation_pools  | fd00:203:0:113::2-fd00:203:0:113:ffff:ffff:ffff:ffff |
      | cidr              | fd00:203:0:113::/64                                  |
      | enable_dhcp       | True                                                 |
      | gateway_ip        | fd00:203:0:113::1                                    |
      | id                | e41cb069-9902-4c01-9e1c-268c8252256a                 |
      | ip_version        | 6                                                    |
      | ipv6_address_mode | slaac                                                |
      | ipv6_ra_mode      | None                                                 |
      | name              | multisegment1-segment1-v6                            |
      | network_id        | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9                 |
      | segment_id        | 43e16869-ad31-48e4-87ce-acf756709e18                 |
      +-------------------+------------------------------------------------------+

   .. note::

      By default, IPv6 subnets on provider networks rely on physical network
      infrastructure for stateless address autoconfiguration (SLAAC) and
      router advertisement.

#. Create subnets on the ``segment2`` segment. In this example, the IPv4
   subnet uses 198.51.100.0/24 and the IPv6 subnet uses fd00:198:51:100::/64.

   .. code-block:: console

      $ openstack subnet create \
        --network multisegment1 --network-segment segment2 \
        --ip-version 4 --subnet-range 198.51.100.0/24 \
        multisegment1-segment2-v4
      +-------------------+--------------------------------------+
      | Field             | Value                                |
      +-------------------+--------------------------------------+
      | allocation_pools  | 198.51.100.2-198.51.100.254          |
      | cidr              | 198.51.100.0/24                      |
      | enable_dhcp       | True                                 |
      | gateway_ip        | 198.51.100.1                         |
      | id                | 242755c2-f5fd-4e7d-bd7a-342ca95e50b2 |
      | ip_version        | 4                                    |
      | name              | multisegment1-segment2-v4            |
      | network_id        | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 |
      | segment_id        | 053b7925-9a89-4489-9992-e164c8cc8763 |
      +-------------------+--------------------------------------+

      $ openstack subnet create \
        --network multisegment1 --network-segment segment2 \
        --ip-version 6 --subnet-range fd00:198:51:100::/64 \
        --ipv6-address-mode slaac multisegment1-segment2-v6
      +-------------------+--------------------------------------------------------+
      | Field             | Value                                                  |
      +-------------------+--------------------------------------------------------+
      | allocation_pools  | fd00:198:51:100::2-fd00:198:51:100:ffff:ffff:ffff:ffff |
      | cidr              | fd00:198:51:100::/64                                   |
      | enable_dhcp       | True                                                   |
      | gateway_ip        | fd00:198:51:100::1                                     |
      | id                | b884c40e-9cfe-4d1b-a085-0a15488e9441                   |
      | ip_version        | 6                                                      |
      | ipv6_address_mode | slaac                                                  |
      | ipv6_ra_mode      | None                                                   |
      | name              | multisegment1-segment2-v6                              |
      | network_id        | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9                   |
      | segment_id        | 053b7925-9a89-4489-9992-e164c8cc8763                   |
      +-------------------+--------------------------------------------------------+

#. Verify that each IPv4 subnet associates with at least one DHCP agent.

   .. code-block:: console

      $ neutron dhcp-agent-list-hosting-net multisegment1
      +--------------------------------------+-------------+----------------+-------+
      | id                                   | host        | admin_state_up | alive |
      +--------------------------------------+-------------+----------------+-------+
      | c904ed10-922c-4c1a-84fd-d928abaf8f55 | compute0001 | True           | :-)   |
      | e0b22cc0-d2a6-4f1c-b17c-27558e20b454 | compute0101 | True           | :-)   |
      +--------------------------------------+-------------+----------------+-------+

#. Verify that inventories were created for each segment IPv4 subnet in the
   Compute service placement API (for the sake of brevity, only one of the
   segments is shown in this example).

   .. code-block:: console

      $ SEGMENT_ID=053b7925-9a89-4489-9992-e164c8cc8763
      $ curl -s -X GET \
        http://localhost/placement/resource_providers/$SEGMENT_ID/inventories \
        -H "Content-type: application/json" \
        -H "X-Auth-Token: $TOKEN" \
        -H "Openstack-Api-Version: placement 1.1"
      {
          "resource_provider_generation": 1,
          "inventories": {
              "allocation_ratio": 1,
              "total": 254,
              "reserved": 2,
              "step_size": 1,
              "min_unit": 1,
              "max_unit": 1
          }
      }

   .. note::

      As of the writing of this guide, there is not placement API CLI client,
      so the :command:`curl` command is used for this example.

#. Verify that host aggregates were created for each segment in the Compute
   service (for the sake of brevity, only one of the segments is shown in this
   example).

   .. code-block:: console

      $ openstack aggregate list
      +----+---------------------------------------------------------+-------------------+
      | Id | Name                                                    | Availability Zone |
      +----+---------------------------------------------------------+-------------------+
      | 10 | Neutron segment id 053b7925-9a89-4489-9992-e164c8cc8763 | None              |
      +----+---------------------------------------------------------+-------------------+

#. Launch one or more instances. Each instance obtains IP addresses according
   to the segment it uses on the particular compute node.

   .. note::

      If a fixed IP is specified by the user in the port create request, that
      particular IP is allocated immediately to the port. However, creating a
      port and passing it to an instance yields a different behavior than
      conventional networks. The Networking service defers assignment of IP
      addresses to the port until the particular compute node becomes
      apparent. For example:

      .. code-block:: console

         $ openstack port create --network multisegment1 port1
         +-----------------------+--------------------------------------+
         | Field                 | Value                                |
         +-----------------------+--------------------------------------+
         | admin_state_up        | UP                                   |
         | binding_vnic_type     | normal                               |
         | id                    | 6181fb47-7a74-4add-9b6b-f9837c1c90c4 |
         | ip_allocation         | deferred                             |
         | mac_address           | fa:16:3e:34:de:9b                    |
         | name                  | port1                                |
         | network_id            | 6ab19caa-dda9-4b3d-abc4-5b8f435b98d9 |
         | port_security_enabled | True                                 |
         | security_groups       | e4fcef0d-e2c5-40c3-a385-9c33ac9289c5 |
         | status                | DOWN                                 |
         +-----------------------+--------------------------------------+
