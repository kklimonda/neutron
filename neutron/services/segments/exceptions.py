# Copyright 2016 Hewlett Packard Enterprise Development, LP
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


from neutron._i18n import _
from neutron_lib import exceptions


class SegmentNotFound(exceptions.NotFound):
    message = _("Segment %(segment_id)s could not be found.")


class SubnetsNotAllAssociatedWithSegments(exceptions.BadRequest):
    message = _("All of the subnets on network '%(network_id)s' must either "
                "all be associated with segments or all not associated with "
                "any segment.")


class SubnetCantAssociateToDynamicSegment(exceptions.BadRequest):
    message = _("A subnet cannot be associated with a dynamic segment.")


class NetworkIdsDontMatch(exceptions.BadRequest):
    message = _("The subnet's network id, '%(subnet_network)s', doesn't match "
                "the network_id of segment '%(segment_id)s'")
