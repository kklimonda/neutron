# Copyright (c) 2016 Hewlett Packard Enterprise Development Company, L.P.
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
#

"""Add segment_id to subnet """

# revision identifiers, used by Alembic.
revision = 'c879c5e1ee90'
down_revision = '89ab9a816d70'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column('subnets',
        sa.Column('segment_id', sa.String(length=36), nullable=True))
    op.create_foreign_key(
        None, 'subnets', 'networksegments', ['segment_id'], ['id'])
