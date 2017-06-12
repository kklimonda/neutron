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

from neutron_lib.services import base as service_base
from oslo_log import log as logging
import sqlalchemy
from sqlalchemy.orm import exc
from sqlalchemy.orm import session as se

from neutron._i18n import _, _LW
from neutron.db import _resource_extend as resource_extend
from neutron.db import api as db_api
from neutron.db import standard_attr

LOG = logging.getLogger(__name__)


@resource_extend.has_resource_extenders
class RevisionPlugin(service_base.ServicePluginBase):
    """Plugin to populate revision numbers into standard attr resources."""

    supported_extension_aliases = ['standard-attr-revisions']

    def __init__(self):
        super(RevisionPlugin, self).__init__()
        db_api.sqla_listen(se.Session, 'before_flush', self.bump_revisions)

    def bump_revisions(self, session, context, instances):
        # bump revision number for any updated objects in the session
        for obj in session.dirty:
            if isinstance(obj, standard_attr.HasStandardAttributes):
                obj.bump_revision()

        # see if any created/updated/deleted objects bump the revision
        # of another object
        objects_with_related_revisions = [
            o for o in session.deleted | session.dirty | session.new
            if getattr(o, 'revises_on_change', ())
        ]
        for obj in objects_with_related_revisions:
            self._bump_related_revisions(session, obj)

    def _bump_related_revisions(self, session, obj):
        for revises_col in getattr(obj, 'revises_on_change', ()):
            try:
                related_obj = self._find_related_obj(session, obj, revises_col)
                if not related_obj:
                    LOG.warning(_LW("Could not find related %(col)s for "
                                    "resource %(obj)s to bump revision."),
                                {'obj': obj, 'col': revises_col})
                    continue
                # if related object revises others, bump those as well
                self._bump_related_revisions(session, related_obj)
                # no need to bump revisions on related objects being deleted
                if related_obj not in session.deleted:
                    related_obj.bump_revision()
            except exc.ObjectDeletedError:
                # object was in session but another writer deleted it
                pass

    def get_plugin_type(self):
        return "revision_plugin"

    def get_plugin_description(self):
        return "Adds revision numbers to resources."

    @staticmethod
    @resource_extend.extends(
        list(standard_attr.get_standard_attr_resource_model_map()))
    def extend_resource_dict_revision(resource_res, resource_db):
        resource_res['revision_number'] = resource_db.revision_number

    def _find_related_obj(self, session, obj, relationship_col):
        """Gets a related object off of a relationship.

        Raises a runtime error if the relationship isn't configured correctly
        for revision bumping.
        """
        # first check to see if it's directly attached to the object already
        related_obj = getattr(obj, relationship_col)
        if related_obj:
            return related_obj
        for rel in sqlalchemy.inspect(obj).mapper.relationships:
            if rel.key != relationship_col:
                continue
            if not rel.load_on_pending:
                raise RuntimeError(_("revises_on_change relationships must "
                                     "have load_on_pending set to True to "
                                     "bump parent revisions on create: %s"),
                                   relationship_col)
