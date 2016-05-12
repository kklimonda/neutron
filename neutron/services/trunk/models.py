# Copyright 2016 Red Hat Inc.
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

import sqlalchemy as sa

from neutron.db import model_base
from neutron.db import models_v2


class Trunk(model_base.HasStandardAttributes, model_base.BASEV2,
            model_base.HasId, model_base.HasTenant):

    port_id = sa.Column(sa.String(36),
                        sa.ForeignKey('ports.id',
                                      ondelete='CASCADE'),
                        nullable=False,
                        unique=True)
    port = sa.orm.relationship(
        models_v2.Port,
        backref=sa.orm.backref('trunk_port', lazy='joined', uselist=False,
                               cascade='delete'))


class SubPort(model_base.BASEV2):

    port_id = sa.Column(sa.String(36),
                        sa.ForeignKey('ports.id',
                                      ondelete='CASCADE'),
                        nullable=False,
                        unique=True,
                        primary_key=True)
    port = sa.orm.relationship(
        models_v2.Port,
        backref=sa.orm.backref('sub_port', lazy='joined', uselist=False,
                               cascade='delete'))

    trunk_id = sa.Column(sa.String(36),
                         sa.ForeignKey('trunks.id',
                                       ondelete='CASCADE'),
                         nullable=False,
                         primary_key=True)

    segmentation_type = sa.Column(sa.String(32), nullable=False)
    segmentation_id = sa.Column(sa.Integer, nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            'trunk_id',
            'segmentation_type',
            'segmentation_id',
            name='uniq_subport0trunk_id0segmentation_type0segmentation_id'),
        model_base.BASEV2.__table_args__
    )

# NOTE(armax) constraints like the following are implemented via
# business logic rules:
#
# Deletion of a trunk automatically deletes all of its subports;
# Deletion of a (child) port referred by a subport is forbidden;
# Deletion of a (parent) port referred by a trunk is forbidden;
# A port cannot be a subport and a trunk port at the same time (nested).
