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

import collections
import copy
import itertools
import os.path
import random

import mock
import netaddr
from neutron_lib import exceptions as n_exc
from neutron_lib.utils import helpers
from oslo_db import exception as obj_exc
from oslo_db.sqlalchemy import utils as db_utils
from oslo_utils import uuidutils
from oslo_versionedobjects import base as obj_base
from oslo_versionedobjects import fields as obj_fields
from oslo_versionedobjects import fixture
import testtools

from neutron.common import constants
from neutron.common import utils
from neutron import context
from neutron.db import db_base_plugin_v2
from neutron.db.models import external_net as ext_net_model
from neutron.db.models import l3 as l3_model
from neutron.db import standard_attr
from neutron import objects
from neutron.objects import base
from neutron.objects import common_types
from neutron.objects.db import api as obj_db_api
from neutron.objects import exceptions as o_exc
from neutron.objects import flavor
from neutron.objects import network as net_obj
from neutron.objects import ports
from neutron.objects import rbac_db
from neutron.objects import securitygroup
from neutron.objects import subnet
from neutron.tests import base as test_base
from neutron.tests import tools
from neutron.tests.unit.db import test_db_base_plugin_v2


SQLALCHEMY_COMMIT = 'sqlalchemy.engine.Connection._commit_impl'
OBJECTS_BASE_OBJ_FROM_PRIMITIVE = ('oslo_versionedobjects.base.'
                                   'VersionedObject.obj_from_primitive')
TIMESTAMP_FIELDS = ['created_at', 'updated_at', 'revision_number']


class FakeModel(dict):
    pass


class ObjectFieldsModel(dict):
    pass


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeSmallNeutronObject(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = ObjectFieldsModel

    primary_keys = ['field1']

    foreign_keys = {
        'FakeNeutronObjectCompositePrimaryKeyWithId': {'field1': 'id'},
        'FakeNeutronDbObject': {'field2': 'id'},
        'FakeNeutronObjectUniqueKey': {'field3': 'id'},
    }

    fields = {
        'field1': common_types.UUIDField(),
        'field2': common_types.UUIDField(),
        'field3': common_types.UUIDField(),
    }


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeSmallNeutronObjectWithMultipleParents(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = ObjectFieldsModel

    primary_keys = ['field1', 'field2']

    foreign_keys = {
        'FakeParent': {'field1': 'id'},
        'FakeParent2': {'field2': 'id'},
    }

    fields = {
        'field1': common_types.UUIDField(),
        'field2': obj_fields.StringField(),
    }


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeParent(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = ObjectFieldsModel

    primary_keys = ['field1', 'field2']

    fields = {
        'id': common_types.UUIDField(),
        'children': obj_fields.ListOfObjectsField(
            'FakeSmallNeutronObjectWithMultipleParents',
            nullable=True)
    }

    synthetic_fields = ['children']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeWeirdKeySmallNeutronObject(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = ObjectFieldsModel

    primary_keys = ['field1']

    foreign_keys = {
        'FakeNeutronObjectNonStandardPrimaryKey': {'field1': 'weird_key'},
        'FakeNeutronObjectCompositePrimaryKey': {'field2': 'weird_key'},
    }

    fields = {
        'field1': common_types.UUIDField(),
        'field2': obj_fields.StringField(),
    }


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronDbObject(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    fields = {
        'id': common_types.UUIDField(),
        'field1': obj_fields.StringField(),
        'obj_field': obj_fields.ObjectField('FakeSmallNeutronObject',
                                            nullable=True)
    }

    primary_keys = ['id']

    fields_no_update = ['field1']

    synthetic_fields = ['obj_field']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectNonStandardPrimaryKey(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    primary_keys = ['weird_key']

    fields = {
        'weird_key': common_types.UUIDField(),
        'field1': obj_fields.StringField(),
        'obj_field': obj_fields.ListOfObjectsField(
            'FakeWeirdKeySmallNeutronObject'),
        'field2': obj_fields.StringField()
    }

    synthetic_fields = ['obj_field', 'field2']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectCompositePrimaryKey(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    primary_keys = ['weird_key', 'field1']

    fields = {
        'weird_key': common_types.UUIDField(),
        'field1': obj_fields.StringField(),
        'obj_field': obj_fields.ListOfObjectsField(
            'FakeWeirdKeySmallNeutronObject')
    }

    synthetic_fields = ['obj_field']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectUniqueKey(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    primary_keys = ['id', 'id2']
    unique_keys = [['unique_key'], ['id2']]

    fields = {
        'id': common_types.UUIDField(),
        'id2': common_types.UUIDField(),
        'unique_key': obj_fields.StringField(),
        'field1': obj_fields.StringField(),
        'obj_field': obj_fields.ObjectField('FakeSmallNeutronObject',
                                            nullable=True)
    }

    synthetic_fields = ['obj_field']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectRenamedField(base.NeutronDbObject):
    """
    Testing renaming the parameter from DB to NeutronDbObject
    For tests:
        - db fields: id, field_db, field2
        - object: id, field_ovo, field2
    """
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    primary_keys = ['id']

    fields = {
        'id': common_types.UUIDField(),
        'field_ovo': obj_fields.StringField(),
        'field2': obj_fields.StringField()
    }

    synthetic_fields = ['field2']

    fields_need_translation = {'field_ovo': 'field_db'}


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectCompositePrimaryKeyWithId(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    primary_keys = ['id', 'field1']

    fields = {
        'id': common_types.UUIDField(),
        'field1': obj_fields.StringField(),
        'obj_field': obj_fields.ListOfObjectsField('FakeSmallNeutronObject')
    }

    synthetic_fields = ['obj_field']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectMultipleForeignKeys(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = ObjectFieldsModel

    foreign_keys = {
        'FakeNeutronObjectSyntheticField': {'field1': 'id', 'field2': 'id'},
    }

    fields = {
        'field1': common_types.UUIDField(),
        'field2': common_types.UUIDField(),
    }


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectSyntheticField(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    fields = {
        'id': common_types.UUIDField(),
        'obj_field': obj_fields.ListOfObjectsField(
            'FakeNeutronObjectMultipleForeignKeys')
    }

    synthetic_fields = ['obj_field']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectSyntheticField2(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    fields = {
        'id': common_types.UUIDField(),
        'obj_field': obj_fields.ObjectField('FakeSmallNeutronObject')
    }

    synthetic_fields = ['obj_field']


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectWithProjectId(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    fields = {
        'id': common_types.UUIDField(),
        'project_id': obj_fields.StringField(),
        'field2': common_types.UUIDField(),
    }


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObject(base.NeutronObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    fields = {
        'id': common_types.UUIDField(),
        'project_id': obj_fields.StringField(),
        'field2': common_types.UUIDField(),
    }

    @classmethod
    def get_object(cls, context, **kwargs):
        if not hasattr(cls, '_obj'):
            cls._obj = FakeNeutronObject(id=uuidutils.generate_uuid(),
                                         project_id='fake-id',
                                         field2=uuidutils.generate_uuid())
        return cls._obj

    @classmethod
    def get_objects(cls, context, _pager=None, count=1, **kwargs):
        return [
            cls.get_object(context, **kwargs)
            for i in range(count)
        ]


@obj_base.VersionedObjectRegistry.register_if(False)
class FakeNeutronObjectDictOfMiscValues(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = FakeModel

    fields = {
        'id': common_types.UUIDField(),
        'dict_field': common_types.DictOfMiscValuesField(),
    }


def get_random_dscp_mark():
    return random.choice(constants.VALID_DSCP_MARKS)


def get_list_of_random_networks(num=10):
    for i in range(5):
        res = [tools.get_random_ip_network() for i in range(num)]
        # make sure there are no duplicates
        if len(set(res)) == num:
            return res
    raise Exception('Failed to generate unique networks')


def get_random_domain_name():
    return '.'.join([
        helpers.get_random_string(62)[:random.choice(range(63))]
        for i in range(4)
    ])


def get_random_dict_of_strings():
    return {
        helpers.get_random_string(10): helpers.get_random_string(10)
        for i in range(10)
    }


def get_random_dict():
    return {
        helpers.get_random_string(6): helpers.get_random_string(6),
        helpers.get_random_string(6): tools.get_random_boolean(),
        helpers.get_random_string(6): tools.get_random_integer(),
        helpers.get_random_string(6): [
            tools.get_random_integer(),
            helpers.get_random_string(6),
            tools.get_random_boolean(),
        ],
        helpers.get_random_string(6): {
            helpers.get_random_string(6): helpers.get_random_string(6)
        }
    }


def get_set_of_random_uuids():
    return {
        uuidutils.generate_uuid()
        for i in range(10)
    }


# NOTE: The keys in this dictionary have alphabetic order.
FIELD_TYPE_VALUE_GENERATOR_MAP = {
    common_types.DictOfMiscValuesField: get_random_dict,
    common_types.DomainNameField: get_random_domain_name,
    common_types.DscpMarkField: get_random_dscp_mark,
    common_types.EtherTypeEnumField: tools.get_random_ether_type,
    common_types.FlowDirectionEnumField: tools.get_random_flow_direction,
    common_types.IpamAllocationStatusEnumField: tools.get_random_ipam_status,
    common_types.IPNetworkField: tools.get_random_ip_network,
    common_types.IPNetworkPrefixLenField: tools.get_random_prefixlen,
    common_types.IPV6ModeEnumField: tools.get_random_ipv6_mode,
    common_types.IPVersionEnumField: tools.get_random_ip_version,
    common_types.IpProtocolEnumField: tools.get_random_ip_protocol,
    common_types.ListOfIPNetworksField: get_list_of_random_networks,
    common_types.MACAddressField: tools.get_random_EUI,
    common_types.PortBindingStatusEnumField:
        tools.get_random_port_binding_statuses,
    common_types.PortRangeField: tools.get_random_port,
    common_types.PortRangeWith0Field: lambda: tools.get_random_port(0),
    common_types.SetOfUUIDsField: get_set_of_random_uuids,
    common_types.UUIDField: uuidutils.generate_uuid,
    common_types.VlanIdRangeField: tools.get_random_vlan,
    obj_fields.BooleanField: tools.get_random_boolean,
    obj_fields.DateTimeField: tools.get_random_datetime,
    obj_fields.DictOfStringsField: get_random_dict_of_strings,
    obj_fields.IPAddressField: tools.get_random_ip_address,
    obj_fields.IntegerField: tools.get_random_integer,
    obj_fields.ListOfObjectsField: lambda: [],
    obj_fields.ListOfStringsField: tools.get_random_string_list,
    obj_fields.ObjectField: lambda: None,
    obj_fields.StringField: lambda: helpers.get_random_string(10),
}


def get_obj_persistent_fields(obj):
    return {field: getattr(obj, field) for field in obj.fields
            if field not in obj.synthetic_fields
            if field in obj}


def get_value(generator, version):
    if 'version' in generator.__code__.co_varnames:
        return generator(version=version)
    return generator()


def remove_timestamps_from_fields(obj_fields, cls_fields):
    obj_fields_result = obj_fields.copy()
    for ts_field in TIMESTAMP_FIELDS:
        if ts_field in cls_fields.keys() and cls_fields[ts_field].nullable:
            obj_fields_result.pop(ts_field)
    return obj_fields_result


def get_non_synthetic_fields(objclass, obj_fields):
    return {field: value for field, value in obj_fields.items()
            if not objclass.is_synthetic(field)}


class _BaseObjectTestCase(object):

    _test_class = FakeNeutronDbObject

    CORE_PLUGIN = 'neutron.db.db_base_plugin_v2.NeutronDbPluginV2'

    def setUp(self):
        super(_BaseObjectTestCase, self).setUp()
        # TODO(ihrachys): revisit plugin setup once we decouple
        # neutron.objects.db.api from core plugin instance
        self.setup_coreplugin(self.CORE_PLUGIN)
        # make sure all objects are loaded and registered in the registry
        utils.import_modules_recursively(os.path.dirname(objects.__file__))
        self.context = context.get_admin_context()
        self._unique_tracker = collections.defaultdict(set)
        self.db_objs = [
            self._test_class.db_model(**self.get_random_db_fields())
            for _ in range(3)
        ]

        # TODO(ihrachys) remove obj_fields since they duplicate self.objs
        self.obj_fields = [self._test_class.modify_fields_from_db(db_obj)
                           for db_obj in self.db_objs]
        self.objs = [
            self._test_class(self.context, **fields)
            for fields in self.obj_fields
        ]

        invalid_fields = (
            set(self._test_class.synthetic_fields).union(set(TIMESTAMP_FIELDS))
        )
        valid_field = [f for f in self._test_class.fields
                       if f not in invalid_fields][0]
        self.valid_field_filter = {valid_field:
                                   self.obj_fields[-1][valid_field]}
        self.obj_registry = self.useFixture(
            fixture.VersionedObjectRegistryFixture())
        self.obj_registry.register(FakeSmallNeutronObject)
        self.obj_registry.register(FakeWeirdKeySmallNeutronObject)
        self.obj_registry.register(FakeNeutronObjectMultipleForeignKeys)
        synthetic_obj_fields = self.get_random_db_fields(
            FakeSmallNeutronObject)
        self.model_map = {
            self._test_class.db_model: self.db_objs,
            ObjectFieldsModel: [ObjectFieldsModel(**synthetic_obj_fields)]}

    def get_random_object_fields(self, obj_cls=None):
        obj_cls = obj_cls or self._test_class
        fields = {}
        ip_version = tools.get_random_ip_version()
        for field, field_obj in obj_cls.fields.items():
            if field not in obj_cls.synthetic_fields:
                generator = FIELD_TYPE_VALUE_GENERATOR_MAP[type(field_obj)]
                fields[field] = get_value(generator, ip_version)
        for keys in obj_cls.unique_keys:
            keytup = tuple(keys)
            unique_values = tuple(fields[k] for k in keytup)
            if unique_values in self._unique_tracker[keytup]:
                # if you get a recursion depth error here, it means
                # your random generator didn't generate unique values
                return self.get_random_object_fields(obj_cls)
            self._unique_tracker[keytup].add(unique_values)
        return fields

    def get_random_db_fields(self, obj_cls=None):
        obj_cls = obj_cls or self._test_class
        return obj_cls.modify_fields_to_db(
            self.get_random_object_fields(obj_cls))

    def update_obj_fields(self, values_dict,
                          db_objs=None, obj_fields=None, objs=None):
        '''Update values for test objects with specific values.

        The default behaviour is using random values for all fields of test
        objects. Sometimes it's not practical, for example, when some fields,
        often those referencing other objects, require non-random values (None
        or UUIDs of valid objects). If that's the case, a test subclass may
        call the method to override some field values for test objects.

        Receives a single ``values_dict`` dict argument where keys are names of
        test class fields, and values are either actual values for the keys, or
        callables that will be used to generate different values for each test
        object.

        Note: if a value is a dict itself, the method will recursively update
        corresponding embedded objects.
        '''
        for k, v in values_dict.items():
            for db_obj, fields, obj in zip(
                    db_objs or self.db_objs,
                    obj_fields or self.obj_fields,
                    objs or self.objs):
                val = v() if callable(v) else v
                db_obj_key = obj.fields_need_translation.get(k, k)
                if isinstance(val, collections.Mapping):
                    self.update_obj_fields(
                        val, db_obj[db_obj_key], fields[k], obj[k])
                else:
                    db_obj[db_obj_key] = val
                    fields[k] = val
                    obj[k] = val
            if k in self.valid_field_filter:
                self.valid_field_filter[k] = val

    @classmethod
    def generate_object_keys(cls, obj_cls, field_names=None):
        if field_names is None:
            field_names = obj_cls.primary_keys
        keys = {}
        for field in field_names:
            field_obj = obj_cls.fields[field]
            generator = FIELD_TYPE_VALUE_GENERATOR_MAP[type(field_obj)]
            keys[field] = generator()
        return keys

    def get_updatable_fields(self, fields):
        return base.get_updatable_fields(self._test_class, fields)

    @classmethod
    def _is_test_class(cls, obj):
        return isinstance(obj, cls._test_class)

    def fake_get_objects(self, context, model, **kwargs):
        return self.model_map[model]

    def _get_object_synthetic_fields(self, objclass):
        return [field for field in objclass.synthetic_fields
                if objclass.is_object_field(field)]

    def _get_ovo_object_class(self, objclass, field):
        try:
            name = objclass.fields[field].objname
            return obj_base.VersionedObjectRegistry.obj_classes().get(name)[0]
        except TypeError:
            # NOTE(korzen) some synthetic fields are not handled by
            # this method, for example the ones that have subclasses, see
            # QosRule
            return


class BaseObjectIfaceTestCase(_BaseObjectTestCase, test_base.BaseTestCase):

    def setUp(self):
        super(BaseObjectIfaceTestCase, self).setUp()
        self.model_map = collections.defaultdict(list)
        self.model_map[self._test_class.db_model] = self.db_objs
        self.pager_map = collections.defaultdict(lambda: None)
        # don't validate refresh and expunge in tests that don't touch database
        # because otherwise it will fail due to db models not being injected
        # into active session in the first place
        mock.patch.object(self.context.session, 'refresh').start()
        mock.patch.object(self.context.session, 'expunge').start()

        self.get_objects_mock = mock.patch.object(
            obj_db_api, 'get_objects',
            side_effect=self.fake_get_objects).start()

        self.get_object_mock = mock.patch.object(
            obj_db_api, 'get_object',
            side_effect=self.fake_get_object).start()

        # NOTE(ihrachys): for matters of basic object behaviour validation,
        # mock out rbac code accessing database. There are separate tests that
        # cover RBAC, per object type.
        if getattr(self._test_class, 'rbac_db_model', None):
            mock.patch.object(
                rbac_db.RbacNeutronDbObjectMixin,
                'is_shared_with_tenant', return_value=False).start()

    def fake_get_object(self, context, model, **kwargs):
        objects = self.model_map[model]
        if not objects:
            return None
        return [obj for obj in objects if obj['id'] == kwargs['id']][0]

    def fake_get_objects(self, context, model, **kwargs):
        return self.model_map[model]

    # TODO(ihrachys) document the intent of all common test cases in docstrings
    def test_get_object(self):
        with mock.patch.object(
                obj_db_api, 'get_object',
                return_value=self.db_objs[0]) as get_object_mock:
            with mock.patch.object(obj_db_api, 'get_objects',
                                   side_effect=self.fake_get_objects):
                obj_keys = self.generate_object_keys(self._test_class)
                obj = self._test_class.get_object(self.context, **obj_keys)
                self.assertTrue(self._is_test_class(obj))
                self._check_equal(self.objs[0], obj)
                get_object_mock.assert_called_once_with(
                    self.context, self._test_class.db_model,
                    **self._test_class.modify_fields_to_db(obj_keys))

    def test_get_object_missing_object(self):
        with mock.patch.object(obj_db_api, 'get_object', return_value=None):
            obj_keys = self.generate_object_keys(self._test_class)
            obj = self._test_class.get_object(self.context, **obj_keys)
            self.assertIsNone(obj)

    def test_get_object_missing_primary_key(self):
        non_unique_fields = (set(self._test_class.fields.keys()) -
                             set(self._test_class.primary_keys) -
                             set(itertools.chain.from_iterable(
                                 self._test_class.unique_keys)))
        obj_keys = self.generate_object_keys(self._test_class,
                                             non_unique_fields)
        self.assertRaises(o_exc.NeutronPrimaryKeyMissing,
                          self._test_class.get_object,
                          self.context, **obj_keys)

    def test_get_object_unique_key(self):
        if not self._test_class.unique_keys:
            self.skipTest('No unique keys found in test class %r' %
                          self._test_class)

        for unique_keys in self._test_class.unique_keys:
            with mock.patch.object(obj_db_api, 'get_object',
                                   return_value=self.db_objs[0]) \
                    as get_object_mock:
                with mock.patch.object(obj_db_api, 'get_objects',
                                       side_effect=self.fake_get_objects):
                    obj_keys = self.generate_object_keys(self._test_class,
                                                         unique_keys)
                    obj = self._test_class.get_object(self.context,
                                                      **obj_keys)
                    self.assertTrue(self._is_test_class(obj))
                    self._check_equal(self.objs[0], obj)
                    get_object_mock.assert_called_once_with(
                        mock.ANY, self._test_class.db_model,
                        **self._test_class.modify_fields_to_db(obj_keys))

    def _get_synthetic_fields_get_objects_calls(self, db_objs):
        mock_calls = []
        for db_obj in db_objs:
            for field in self._test_class.synthetic_fields:
                if self._test_class.is_object_field(field):
                    obj_class = self._get_ovo_object_class(self._test_class,
                                                           field)
                    filter_kwargs = {
                        obj_class.fields_need_translation.get(k, k): db_obj[v]
                        for k, v in obj_class.foreign_keys.get(
                            self._test_class.__name__).items()
                    }
                    mock_calls.append(
                        mock.call(
                            self.context, obj_class.db_model,
                            _pager=self.pager_map[obj_class.obj_name()],
                            **filter_kwargs))
        return mock_calls

    def test_get_objects(self):
        '''Test that get_objects fetches data from database.'''
        with mock.patch.object(
                obj_db_api, 'get_objects',
                side_effect=self.fake_get_objects) as get_objects_mock:
            objs = self._test_class.get_objects(self.context)
            self.assertItemsEqual(
                [get_obj_persistent_fields(obj) for obj in self.objs],
                [get_obj_persistent_fields(obj) for obj in objs])
        get_objects_mock.assert_any_call(
            self.context, self._test_class.db_model,
            _pager=self.pager_map[self._test_class.obj_name()]
        )

    def test_get_objects_valid_fields(self):
        '''Test that a valid filter does not raise an error.'''
        with mock.patch.object(
                obj_db_api, 'get_objects', side_effect=self.fake_get_objects):
            self._test_class.get_objects(self.context,
                                         **self.valid_field_filter)

    def test_get_objects_mixed_fields(self):
        synthetic_fields = (
            set(self._test_class.synthetic_fields) -
            self._test_class.extra_filter_names
        )
        if not synthetic_fields:
            self.skipTest('No synthetic fields that are not extra filters '
                          'found in test class %r' %
                          self._test_class)

        filters = copy.copy(self.valid_field_filter)
        filters[synthetic_fields.pop()] = 'xxx'

        with mock.patch.object(obj_db_api, 'get_objects',
                               return_value=self.db_objs):
            self.assertRaises(n_exc.InvalidInput,
                              self._test_class.get_objects, self.context,
                              **filters)

    def test_get_objects_synthetic_fields_not_extra_filters(self):
        synthetic_fields = (
            set(self._test_class.synthetic_fields) -
            self._test_class.extra_filter_names
        )
        if not synthetic_fields:
            self.skipTest('No synthetic fields that are not extra filters '
                          'found in test class %r' %
                          self._test_class)

        with mock.patch.object(obj_db_api, 'get_objects',
                               side_effect=self.fake_get_objects):
            self.assertRaises(n_exc.InvalidInput,
                              self._test_class.get_objects, self.context,
                              **{synthetic_fields.pop(): 'xxx'})

    def test_get_objects_invalid_fields(self):
        with mock.patch.object(obj_db_api, 'get_objects',
                               side_effect=self.fake_get_objects):
            self.assertRaises(n_exc.InvalidInput,
                              self._test_class.get_objects, self.context,
                              fake_field='xxx')

    def test_get_objects_without_validate_filters(self):
        with mock.patch.object(
                obj_db_api, 'get_objects',
                side_effect=self.fake_get_objects):
            objs = self._test_class.get_objects(self.context,
                                                validate_filters=False,
                                                unknown_filter='value')
            self.assertItemsEqual(
                [get_obj_persistent_fields(obj) for obj in self.objs],
                [get_obj_persistent_fields(obj) for obj in objs])

    def test_delete_objects(self):
        '''Test that delete_objects calls to underlying db_api.'''
        with mock.patch.object(
                obj_db_api, 'delete_objects', return_value=0
        ) as delete_objects_mock:
            self.assertEqual(0, self._test_class.delete_objects(self.context))
        delete_objects_mock.assert_any_call(
            self.context, self._test_class.db_model)

    def test_delete_objects_valid_fields(self):
        '''Test that a valid filter does not raise an error.'''
        with mock.patch.object(obj_db_api, 'delete_objects', return_value=0):
            self._test_class.delete_objects(self.context,
                                            **self.valid_field_filter)

    def test_delete_objects_invalid_fields(self):
        with mock.patch.object(obj_db_api, 'delete_objects'):
            self.assertRaises(n_exc.InvalidInput,
                              self._test_class.delete_objects, self.context,
                              fake_field='xxx')

    def test_delete_objects_without_validate_filters(self):
        with mock.patch.object(
                obj_db_api, 'delete_objects'):
            self._test_class.delete_objects(self.context,
                                            validate_filters=False,
                                            unknown_filter='value')

    def test_count(self):
        if not isinstance(self._test_class, base.NeutronDbObject):
            self.skipTest('Class %s does not inherit from NeutronDbObject' %
                          self._test_class)
        expected = 10
        with mock.patch.object(obj_db_api, 'count', return_value=expected):
            self.assertEqual(expected, self._test_class.count(self.context))

    def test_count_invalid_fields(self):
            self.assertRaises(n_exc.InvalidInput,
                              self._test_class.count, self.context,
                              fake_field='xxx')

    def _check_equal(self, expected, observed):
        self.assertItemsEqual(get_obj_persistent_fields(expected),
                              get_obj_persistent_fields(observed))

    def test_count_validate_filters_false(self):
        if not isinstance(self._test_class, base.NeutronDbObject):
            self.skipTest('Class %s does not inherit from NeutronDbObject' %
                          self._test_class)
        expected = 10
        with mock.patch.object(obj_db_api, 'count', return_value=expected):
            self.assertEqual(expected, self._test_class.count(self.context,
                validate_filters=False, fake_field='xxx'))

    # Adding delete_objects mock because some objects are using delete_objects
    # while calling create(), Port for example
    @mock.patch.object(obj_db_api, 'delete_objects')
    def test_create(self, *mocks):
        with mock.patch.object(obj_db_api, 'create_object',
                               return_value=self.db_objs[0]) as create_mock:
            with mock.patch.object(obj_db_api, 'get_objects',
                  side_effect=self.fake_get_objects):
                obj = self._test_class(self.context, **self.obj_fields[0])
                self._check_equal(self.objs[0], obj)
                obj.create()
                self._check_equal(self.objs[0], obj)
                create_mock.assert_called_once_with(
                    self.context, self._test_class.db_model,
                    self._test_class.modify_fields_to_db(
                        get_obj_persistent_fields(self.objs[0])))

    # Adding delete_objects mock because some objects are using delete_objects
    # while calling create(), Port for example
    @mock.patch.object(obj_db_api, 'delete_objects')
    def test_create_updates_from_db_object(self, *mocks):
        with mock.patch.object(obj_db_api, 'create_object',
                               return_value=self.db_objs[0]):
            with mock.patch.object(obj_db_api, 'get_objects',
                  side_effect=self.fake_get_objects):
                self.objs[1].create()
                self._check_equal(self.objs[0], self.objs[1])

    # Adding delete_objects mock because some objects are using delete_objects
    # while calling create(), Port for example
    @mock.patch.object(obj_db_api, 'delete_objects')
    def test_create_duplicates(self, delete_object):
        with mock.patch.object(obj_db_api, 'create_object',
                               side_effect=obj_exc.DBDuplicateEntry):
            obj = self._test_class(self.context, **self.obj_fields[0])
            self.assertRaises(o_exc.NeutronDbObjectDuplicateEntry, obj.create)

    def test_update_fields(self):
        if not self._test_class.primary_keys:
            self.skipTest(
                'Test class %r has no primary keys' % self._test_class)

        with mock.patch.object(obj_base.VersionedObject, 'obj_reset_changes'):
            expected = self._test_class(self.context, **self.obj_fields[0])
            for key, val in self.obj_fields[1].items():
                if key not in expected.fields_no_update:
                    setattr(expected, key, val)
            observed = self._test_class(self.context, **self.obj_fields[0])
            observed.update_fields(self.obj_fields[1], reset_changes=True)
            self.assertEqual(expected, observed)
            self.assertTrue(observed.obj_reset_changes.called)

        with mock.patch.object(obj_base.VersionedObject, 'obj_reset_changes'):
            obj = self._test_class(self.context, **self.obj_fields[0])
            obj.update_fields(self.obj_fields[1])
            self.assertFalse(obj.obj_reset_changes.called)

    def test_extra_fields(self):
        if not len(self._test_class.obj_extra_fields):
            self.skipTest(
                'Test class %r has no obj_extra_fields' % self._test_class)
        obj = self._test_class(self.context, **self.obj_fields[0])
        for field in self._test_class.obj_extra_fields:
            # field is accessible and cannot be set by any value
            getattr(obj, field)
            self.assertIn(field, obj.to_dict().keys())
            self.assertRaises(AttributeError, setattr, obj, field, "1")

    def test_to_dict_makes_primitive_field_value(self):
        obj = self._test_class(self.context, **self.obj_fields[0])
        dict_ = obj.to_dict()
        for k, v in dict_.items():
            if k not in obj.fields:
                continue
            field = obj.fields[k]
            self.assertEqual(v, field.to_primitive(obj, k, getattr(obj, k)))

    def test_to_dict_with_unset_project_id(self):
        if 'project_id' not in self._test_class.fields:
            self.skipTest(
                'Test class %r has no project_id in fields' % self._test_class)
        obj_data = copy.copy(self.obj_fields[0])
        obj_data.pop('project_id')
        obj = self._test_class(self.context, **obj_data)
        dict_ = obj.to_dict()

        self.assertNotIn('project_id', dict_)
        self.assertNotIn('tenant_id', dict_)

    def test_fields_no_update(self):
        obj = self._test_class(self.context, **self.obj_fields[0])
        for field in self._test_class.fields_no_update:
            self.assertTrue(hasattr(obj, field))

    def test_get_tenant_id(self):
        if not hasattr(self._test_class, 'project_id'):
            self.skipTest(
                'Test class %r has no project_id field' % self._test_class)
        obj = self._test_class(self.context, **self.obj_fields[0])
        project_id = self.obj_fields[0]['project_id']
        self.assertEqual(project_id, obj.tenant_id)

    # Adding delete_objects mock because some objects are using delete_objects
    # while calling update(), Port for example
    @mock.patch.object(obj_db_api, 'delete_objects')
    @mock.patch.object(obj_db_api, 'update_object')
    def test_update_changes(self, update_mock, del_mock):
        fields_to_update = self.get_updatable_fields(
            self._test_class.modify_fields_from_db(self.db_objs[0]))
        if not fields_to_update:
            self.skipTest('No updatable fields found in test class %r' %
                          self._test_class)

        with mock.patch.object(base.NeutronDbObject,
                               '_get_changed_persistent_fields',
                               return_value=fields_to_update):
            with mock.patch.object(obj_db_api, 'get_objects',
                side_effect=self.fake_get_objects):
                obj = self._test_class(self.context, **self.obj_fields[0])
                # get new values and fix keys
                update_mock.return_value = self.db_objs[1]
                fixed_keys = self._test_class.modify_fields_to_db(
                    obj._get_composite_keys())
                for key, value in fixed_keys.items():
                    update_mock.return_value[key] = value
                obj.update()
                update_mock.assert_called_once_with(
                    self.context, self._test_class.db_model,
                    self._test_class.modify_fields_to_db(fields_to_update),
                    **fixed_keys)

    @mock.patch.object(base.NeutronDbObject,
                       '_get_changed_persistent_fields',
                       return_value={'a': 'a', 'b': 'b', 'c': 'c'})
    def test_update_changes_forbidden(self, *mocks):
        with mock.patch.object(
            self._test_class,
            'fields_no_update',
            new_callable=mock.PropertyMock(return_value=['a', 'c']),
            create=True):
            obj = self._test_class(self.context, **self.obj_fields[0])
            self.assertRaises(o_exc.NeutronObjectUpdateForbidden, obj.update)

    # Adding delete_objects mock because some objects are using delete_objects
    # while calling update(), Port and Network for example
    @mock.patch.object(obj_db_api, 'delete_objects')
    def test_update_updates_from_db_object(self, *mocks):
        with mock.patch.object(obj_db_api, 'update_object',
                               return_value=self.db_objs[0]):
            with mock.patch.object(obj_db_api, 'get_objects',
                  side_effect=self.fake_get_objects):
                obj = self._test_class(self.context, **self.obj_fields[1])
                fields_to_update = self.get_updatable_fields(
                    self.obj_fields[1])
                if not fields_to_update:
                    self.skipTest('No updatable fields found in test '
                                  'class %r' % self._test_class)
                with mock.patch.object(base.NeutronDbObject,
                                       '_get_changed_persistent_fields',
                                       return_value=fields_to_update):
                    with mock.patch.object(
                        obj_db_api, 'get_objects',
                        side_effect=self.fake_get_objects):
                        obj.update()
                self._check_equal(self.objs[0], obj)

    @mock.patch.object(obj_db_api, 'delete_object')
    def test_delete(self, delete_mock):
        obj = self._test_class(self.context, **self.obj_fields[0])
        self._check_equal(self.objs[0], obj)
        obj.delete()
        self._check_equal(self.objs[0], obj)
        delete_mock.assert_called_once_with(
            self.context, self._test_class.db_model,
            **self._test_class.modify_fields_to_db(obj._get_composite_keys()))

    @mock.patch(OBJECTS_BASE_OBJ_FROM_PRIMITIVE)
    def test_clean_obj_from_primitive(self, get_prim_m):
        expected_obj = get_prim_m.return_value
        observed_obj = self._test_class.clean_obj_from_primitive('foo', 'bar')
        self.assertIs(expected_obj, observed_obj)
        self.assertTrue(observed_obj.obj_reset_changes.called)

    def test_update_primary_key_forbidden_fail(self):
        obj = self._test_class(self.context, **self.obj_fields[0])
        obj.obj_reset_changes()

        if not self._test_class.primary_keys:
            self.skipTest(
                'All non-updatable fields found in test class %r '
                'are primary keys' % self._test_class)

        for key, val in self.obj_fields[0].items():
            if key in self._test_class.primary_keys:
                setattr(obj, key, val)

        self.assertRaises(o_exc.NeutronObjectUpdateForbidden, obj.update)

    def test_to_dict_synthetic_fields(self):
        cls_ = self._test_class
        object_fields = self._get_object_synthetic_fields(cls_)
        if not object_fields:
            self.skipTest(
                'No object fields found in test class %r' % cls_)

        for field in object_fields:
            obj = cls_(self.context, **self.obj_fields[0])
            objclass = self._get_ovo_object_class(cls_, field)
            if not objclass:
                continue

            child = objclass(
                self.context, **objclass.modify_fields_from_db(
                    self.get_random_db_fields(obj_cls=objclass))
            )
            child_dict = child.to_dict()
            if isinstance(cls_.fields[field], obj_fields.ListOfObjectsField):
                setattr(obj, field, [child])
                dict_ = obj.to_dict()
                self.assertEqual([child_dict], dict_[field])
            else:
                setattr(obj, field, child)
                dict_ = obj.to_dict()
                self.assertEqual(child_dict, dict_[field])

    def test_get_objects_pager_is_passed_through(self):
        with mock.patch.object(obj_db_api, 'get_objects') as get_objects:
            pager = base.Pager()
            self._test_class.get_objects(self.context, _pager=pager)
            get_objects.assert_called_once_with(
                mock.ANY, self._test_class.db_model, _pager=pager)


class BaseDbObjectNonStandardPrimaryKeyTestCase(BaseObjectIfaceTestCase):

    _test_class = FakeNeutronObjectNonStandardPrimaryKey


class BaseDbObjectCompositePrimaryKeyTestCase(BaseObjectIfaceTestCase):

    _test_class = FakeNeutronObjectCompositePrimaryKey


class BaseDbObjectUniqueKeysTestCase(BaseObjectIfaceTestCase):

    _test_class = FakeNeutronObjectUniqueKey


class UniqueKeysTestCase(test_base.BaseTestCase):

    def test_class_creation(self):
        m_get_unique_keys = mock.patch.object(db_utils, 'get_unique_keys')
        with m_get_unique_keys as get_unique_keys:
            get_unique_keys.return_value = [['field1'],
                                            ['field2', 'db_field3']]

            @obj_base.VersionedObjectRegistry.register_if(False)
            class UniqueKeysTestObject(base.NeutronDbObject):
                # Version 1.0: Initial version
                VERSION = '1.0'

                db_model = FakeModel

                primary_keys = ['id']

                fields = {
                    'id': common_types.UUIDField(),
                    'field1': common_types.UUIDField(),
                    'field2': common_types.UUIDField(),
                    'field3': common_types.UUIDField(),
                }

                fields_need_translation = {'field3': 'db_field3'}
        expected = {('field1',), ('field2', 'field3')}
        observed = {tuple(sorted(key))
                    for key in UniqueKeysTestObject.unique_keys}
        self.assertEqual(expected, observed)


class NeutronObjectCountTestCase(test_base.BaseTestCase):

    def test_count(self):
        expected = 10
        self.assertEqual(
            expected, FakeNeutronObject.count(None, count=expected))


class BaseDbObjectCompositePrimaryKeyWithIdTestCase(BaseObjectIfaceTestCase):

    _test_class = FakeNeutronObjectCompositePrimaryKeyWithId


class BaseDbObjectRenamedFieldTestCase(BaseObjectIfaceTestCase):

    _test_class = FakeNeutronObjectRenamedField


class BaseObjectIfaceWithProjectIdTestCase(BaseObjectIfaceTestCase):

    _test_class = FakeNeutronObjectWithProjectId

    def test_update_fields_using_tenant_id(self):
        obj = self._test_class(self.context, **self.obj_fields[0])
        obj.obj_reset_changes()

        tenant_id = obj['tenant_id']
        new_obj_fields = dict()
        new_obj_fields['tenant_id'] = uuidutils.generate_uuid()
        new_obj_fields['field2'] = uuidutils.generate_uuid()

        obj.update_fields(new_obj_fields)
        self.assertEqual(set(['field2']), obj.obj_what_changed())
        self.assertEqual(tenant_id, obj.project_id)


class BaseDbObjectMultipleForeignKeysTestCase(_BaseObjectTestCase,
                                              test_base.BaseTestCase):

    _test_class = FakeNeutronObjectSyntheticField

    def test_load_synthetic_db_fields_with_multiple_foreign_keys(self):
        obj = self._test_class(self.context, **self.obj_fields[0])
        self.assertRaises(o_exc.NeutronSyntheticFieldMultipleForeignKeys,
                          obj.load_synthetic_db_fields)


class BaseDbObjectForeignKeysNotFoundTestCase(_BaseObjectTestCase,
                                              test_base.BaseTestCase):

    _test_class = FakeNeutronObjectSyntheticField2

    def test_load_foreign_keys_not_belong_class(self):
        obj = self._test_class(self.context, **self.obj_fields[0])
        self.assertRaises(o_exc.NeutronSyntheticFieldsForeignKeysNotFound,
                          obj.load_synthetic_db_fields)


class BaseDbObjectMultipleParentsForForeignKeysTestCase(
        _BaseObjectTestCase,
        test_base.BaseTestCase):

    _test_class = FakeParent

    def test_load_synthetic_db_fields_with_multiple_parents(self):
        child_cls = FakeSmallNeutronObjectWithMultipleParents
        self.obj_registry.register(child_cls)
        self.obj_registry.register(FakeParent)
        obj = self._test_class(self.context, **self.obj_fields[0])
        fake_children = [
            child_cls(
                self.context, **child_cls.modify_fields_from_db(
                    self.get_random_db_fields(obj_cls=child_cls))
            )
            for _ in range(5)
        ]
        with mock.patch.object(child_cls, 'get_objects',
                               return_value=fake_children) as get_objects:
            obj.load_synthetic_db_fields()
        get_objects.assert_called_once_with(self.context, field1=obj.id)
        self.assertEqual(fake_children, obj.children)


class BaseObjectIfaceDictMiscValuesTestCase(_BaseObjectTestCase,
                                            test_base.BaseTestCase):

    _test_class = FakeNeutronObjectDictOfMiscValues

    def test_dict_of_misc_values(self):
        obj_id = uuidutils.generate_uuid()
        float_value = 1.23
        misc_list = [True, float_value]
        obj_dict = {
            'bool': True,
            'float': float_value,
            'misc_list': misc_list
        }
        obj = self._test_class(self.context, id=obj_id, dict_field=obj_dict)
        self.assertTrue(obj.dict_field['bool'])
        self.assertEqual(float_value, obj.dict_field['float'])
        self.assertEqual(misc_list, obj.dict_field['misc_list'])


class BaseDbObjectTestCase(_BaseObjectTestCase,
                           test_db_base_plugin_v2.DbOperationBoundMixin):
    def setUp(self):
        super(BaseDbObjectTestCase, self).setUp()
        self.useFixture(tools.CommonDbMixinHooksFixture())
        synthetic_fields = self._get_object_synthetic_fields(self._test_class)
        for synth_field in synthetic_fields:
            objclass = self._get_ovo_object_class(self._test_class,
                                                  synth_field)
            if not objclass:
                continue
            for db_obj in self.db_objs:
                objclass_fields = self.get_random_db_fields(objclass)
                if isinstance(self._test_class.fields[synth_field],
                              obj_fields.ObjectField):
                    db_obj[synth_field] = objclass.db_model(**objclass_fields)
                else:
                    db_obj[synth_field] = [
                        objclass.db_model(**objclass_fields)
                    ]

    def _create_test_network(self):
        self._network = net_obj.Network(self.context,
                                       name='test-network1')
        self._network.create()

    def _create_network(self):
        name = "test-network-%s" % helpers.get_random_string(4)
        _network = net_obj.Network(self.context,
                                   name=name)
        _network.create()
        return _network

    def _create_external_network(self):
        test_network = self._create_network()
        # TODO(manjeets) replace this with ext_net ovo
        # once it is implemented
        return obj_db_api.create_object(
            self.context,
            ext_net_model.ExternalNetwork,
            {'network_id': test_network['id']})

    def _create_test_fip(self):
        fake_fip = '172.23.3.0'
        ext_net = self._create_external_network()
        test_port = self._create_port(
            network_id=ext_net['network_id'])
        # TODO(manjeets) replace this with fip ovo
        # once it is implemented
        return obj_db_api.create_object(
            self.context,
            l3_model.FloatingIP,
            {'floating_ip_address': fake_fip,
             'floating_network_id': ext_net['network_id'],
             'floating_port_id': test_port['id']})

    def _create_test_subnet(self, network):
        test_subnet = {
            'project_id': uuidutils.generate_uuid(),
            'name': 'test-subnet1',
            'network_id': network['id'],
            'ip_version': 4,
            'cidr': netaddr.IPNetwork('10.0.0.0/24'),
            'gateway_ip': '10.0.0.1',
            'enable_dhcp': 1,
            'ipv6_ra_mode': None,
            'ipv6_address_mode': None
        }
        self._subnet = subnet.Subnet(self.context, **test_subnet)
        self._subnet.create()

    def _create_port(self, **port_attrs):
        if not hasattr(self, '_mac_address_generator'):
            self._mac_address_generator = (
                netaddr.EUI(":".join(["%02x" % i] * 6))
                for i in itertools.count()
            )

        if not hasattr(self, '_port_name_generator'):
            self._port_name_generator = ("test-port%d" % i
                                         for i in itertools.count(1))

        attrs = {'project_id': uuidutils.generate_uuid(),
                 'admin_state_up': True,
                 'status': 'ACTIVE',
                 'device_id': 'fake_device',
                 'device_owner': 'fake_owner'}
        attrs.update(port_attrs)

        if 'name' not in attrs:
            attrs['name'] = next(self._port_name_generator)
        if 'mac_address' not in attrs:
            attrs['mac_address'] = next(self._mac_address_generator)

        port = ports.Port(self.context, **attrs)
        port.create()
        return port

    def _create_test_segment(self, network):
        self._segment = net_obj.NetworkSegment(self.context,
            network_id=network['id'],
            network_type='vxlan')
        self._segment.create()

    def _create_test_router(self):
        attrs = {
            'name': 'test_router',
        }
        # TODO(sindhu): Replace with the router object once its ready
        self._router = obj_db_api.create_object(self.context,
                                                l3_model.Router,
                                                attrs)

    def _create_test_security_group(self):
        sg_fields = self.get_random_object_fields(securitygroup.SecurityGroup)
        self._securitygroup = securitygroup.SecurityGroup(self.context,
                                                          **sg_fields)
        self._securitygroup.create()
        return self._securitygroup

    def _create_test_port(self, network):
        self._port = self._create_port(network_id=network['id'])

    def _create_test_standard_attribute(self):
        attrs = {
            'id': tools.get_random_integer(),
            'resource_type': helpers.get_random_string(4),
            'revision_number': tools.get_random_integer()
        }
        self._standard_attribute = obj_db_api.create_object(
            self.context,
            standard_attr.StandardAttribute,
            attrs)

    def test_get_standard_attr_id(self):

        if not self._test_class.has_standard_attributes():
            self.skipTest(
                    'No standard attributes found in test class %r'
                    % self._test_class)

        obj = self._make_object(self.obj_fields[0])
        obj.create()

        model = self.context.session.query(obj.db_model).filter_by(
            **obj._get_composite_keys()).one()

        retrieved_obj = self._test_class.get_object(
            self.context, **obj._get_composite_keys())

        self.assertIsNotNone(retrieved_obj.standard_attr_id)
        self.assertEqual(
            model.standard_attr_id, retrieved_obj.standard_attr_id)

    def _create_test_flavor(self):
        attrs = self.get_random_object_fields(obj_cls=flavor.Flavor)
        self._flavor = flavor.Flavor(self.context, **attrs)
        self._flavor.create()
        return self._flavor

    def _create_test_service_profile(self):
        attrs = self.get_random_object_fields(obj_cls=flavor.ServiceProfile)
        self._service_profile = flavor.ServiceProfile(self.context, **attrs)
        self._service_profile.create()
        return self._service_profile

    def _make_object(self, fields):
        fields = get_non_synthetic_fields(self._test_class, fields)
        return self._test_class(self.context,
                                **remove_timestamps_from_fields(
                                    fields, self._test_class.fields))

    def test_get_object_create_update_delete(self):
        # Timestamps can't be initialized and multiple objects may use standard
        # attributes so we need to remove timestamps when creating objects
        obj = self._make_object(self.obj_fields[0])
        obj.create()

        new = self._test_class.get_object(self.context,
                                          **obj._get_composite_keys())
        self.assertEqual(obj, new)

        obj = new

        for key, val in self.get_updatable_fields(self.obj_fields[1]).items():
            setattr(obj, key, val)
        obj.update()

        new = self._test_class.get_object(self.context,
                                          **obj._get_composite_keys())
        self.assertEqual(obj, new)

        obj = new
        new.delete()

        new = self._test_class.get_object(self.context,
                                          **obj._get_composite_keys())
        self.assertIsNone(new)

    def test_update_non_existent_object_raises_not_found(self):
        obj = self._make_object(self.obj_fields[0])
        obj.obj_reset_changes()

        fields_to_update = self.get_updatable_fields(self.obj_fields[0])
        if not fields_to_update:
            self.skipTest('No updatable fields found in test class %r' %
                          self._test_class)
        for key, val in fields_to_update.items():
            setattr(obj, key, val)

        self.assertRaises(n_exc.ObjectNotFound, obj.update)

    def test_delete_non_existent_object_raises_not_found(self):
        obj = self._make_object(self.obj_fields[0])
        self.assertRaises(n_exc.ObjectNotFound, obj.delete)

    @mock.patch(SQLALCHEMY_COMMIT)
    def test_create_single_transaction(self, mock_commit):
        obj = self._make_object(self.obj_fields[0])
        obj.create()
        self.assertEqual(1, mock_commit.call_count)

    def test_update_single_transaction(self):
        obj = self._make_object(self.obj_fields[0])
        obj.create()

        fields_to_update = self.get_updatable_fields(self.obj_fields[1])
        if not fields_to_update:
            self.skipTest('No updatable fields found in test class %r' %
                          self._test_class)
        for key, val in fields_to_update.items():
            setattr(obj, key, val)

        with mock.patch(SQLALCHEMY_COMMIT) as mock_commit:
            obj.update()
        self.assertEqual(1, mock_commit.call_count)

    def test_delete_single_transaction(self):
        obj = self._make_object(self.obj_fields[0])
        obj.create()

        with mock.patch(SQLALCHEMY_COMMIT) as mock_commit:
            obj.delete()
        self.assertEqual(1, mock_commit.call_count)

    @mock.patch(SQLALCHEMY_COMMIT)
    def test_get_objects_single_transaction(self, mock_commit):
        self._test_class.get_objects(self.context)
        self.assertEqual(1, mock_commit.call_count)

    @mock.patch(SQLALCHEMY_COMMIT)
    def test_get_object_single_transaction(self, mock_commit):
        obj = self._make_object(self.obj_fields[0])
        obj.create()

        obj = self._test_class.get_object(self.context,
                                          **obj._get_composite_keys())
        self.assertEqual(2, mock_commit.call_count)

    def test_get_objects_supports_extra_filtername(self):
        self.filtered_args = None

        def foo_filter(query, filters):
            self.filtered_args = filters
            return query

        self.obj_registry.register(self._test_class)
        db_base_plugin_v2.NeutronDbPluginV2.register_model_query_hook(
            self._test_class.db_model,
            'foo_filter',
            None,
            None,
            foo_filter)
        base.register_filter_hook_on_model(self._test_class.db_model, 'foo')

        self._test_class.get_objects(self.context, foo=42)
        self.assertEqual({'foo': [42]}, self.filtered_args)

    def test_filtering_by_fields(self):
        obj = self._make_object(self.obj_fields[0])
        obj.create()

        for field in get_obj_persistent_fields(obj):
            if not isinstance(obj[field], list):
                filters = {field: [obj[field]]}
            else:
                filters = {field: obj[field]}
            new = self._test_class.get_objects(self.context, **filters)
            self.assertItemsEqual(
                [obj._get_composite_keys()],
                [obj_._get_composite_keys() for obj_ in new],
                'Filtering by %s failed.' % field)

    def _get_non_synth_fields(self, objclass, db_attrs):
        fields = objclass.modify_fields_from_db(db_attrs)
        fields = remove_timestamps_from_fields(fields, objclass.fields)
        fields = get_non_synthetic_fields(objclass, fields)
        return fields

    def _create_object_with_synthetic_fields(self, db_obj):
        cls_ = self._test_class
        object_fields = self._get_object_synthetic_fields(cls_)

        # create base object
        obj = cls_(self.context, **self._get_non_synth_fields(cls_, db_obj))
        obj.create()

        # create objects that are going to be loaded into the base object
        # through synthetic fields
        for field in object_fields:
            objclass = self._get_ovo_object_class(cls_, field)
            if not objclass:
                continue

            # check that the stored database model does not have non-empty
            # relationships
            dbattr = obj.fields_need_translation.get(field, field)
            self.assertFalse(getattr(obj.db_obj, dbattr, None))

            if isinstance(cls_.fields[field], obj_fields.ObjectField):
                objclass_fields = self._get_non_synth_fields(objclass,
                                                             db_obj[field])
            else:
                objclass_fields = self._get_non_synth_fields(objclass,
                                                             db_obj[field][0])

            # make sure children point to the base object
            foreign_keys = objclass.foreign_keys.get(obj.__class__.__name__)
            for local_field, foreign_key in foreign_keys.items():
                objclass_fields[local_field] = obj.get(foreign_key)

            synth_field_obj = objclass(self.context, **objclass_fields)
            synth_field_obj.create()

            # reload the parent object under test
            obj = cls_.get_object(self.context, **obj._get_composite_keys())

            # check that the stored database model now has filled relationships
            dbattr = obj.fields_need_translation.get(field, field)
            self.assertTrue(getattr(obj.db_obj, dbattr, None))

            # reset the object so that we can compare it to other clean objects
            obj.obj_reset_changes([field])

        return obj

    def _test_get_with_synthetic_fields(self, getter):
        object_fields = self._get_object_synthetic_fields(self._test_class)
        if not object_fields:
            self.skipTest(
                'No synthetic object fields found '
                'in test class %r' % self._test_class
            )
        obj = self._create_object_with_synthetic_fields(self.db_objs[0])
        listed_obj = getter(self.context, **obj._get_composite_keys())
        self.assertTrue(listed_obj)
        self.assertEqual(obj, listed_obj)

    def test_get_object_with_synthetic_fields(self):
        self._test_get_with_synthetic_fields(self._test_class.get_object)

    def test_get_objects_with_synthetic_fields(self):
        def getter(*args, **kwargs):
            objs = self._test_class.get_objects(*args, **kwargs)
            self.assertEqual(1, len(objs))
            return objs[0]

        self._test_get_with_synthetic_fields(getter)

    # NOTE(korzen) _list method is used in neutron.tests.db.unit.db.
    # test_db_base_plugin_v2.DbOperationBoundMixin in _list_and_count_queries()
    # This is used in test_subnet for asserting that number of queries is
    # constant. It can be used also for port and network objects when ready.
    def _list(self, resource, neutron_context):
        cls_ = resource
        return cls_.get_objects(neutron_context)

    def test_get_objects_queries_constant(self):
        iter_db_obj = iter(self.db_objs)

        def _create():
            return self._create_object_with_synthetic_fields(next(iter_db_obj))

        self._assert_object_list_queries_constant(_create, self._test_class)

    def test_count(self):
        for fields in self.obj_fields:
            self._make_object(fields).create()
        self.assertEqual(
            len(self.obj_fields), self._test_class.count(self.context))

    def test_count_validate_filters_false(self):
        for fields in self.obj_fields:
            self._make_object(fields).create()
        self.assertEqual(
            len(self.obj_fields), self._test_class.count(self.context,
                validate_filters=False, fake_filter='xxx'))

    def test_count_invalid_filters(self):
        for fields in self.obj_fields:
            self._make_object(fields).create()
        self.assertRaises(n_exc.InvalidInput,
                          self._test_class.count, self.context,
                          fake_field='xxx')

    def test_objects_exist(self):
        for fields in self.obj_fields:
            self._make_object(fields).create()
        self.assertTrue(self._test_class.objects_exist(self.context))

    def test_objects_exist_false(self):
        self.assertFalse(self._test_class.objects_exist(self.context))

    def test_objects_exist_validate_filters(self):
        self.assertRaises(n_exc.InvalidInput,
                          self._test_class.objects_exist, self.context,
                          fake_field='xxx')

    def test_objects_exist_validate_filters_false(self):
        for fields in self.obj_fields:
            self._make_object(fields).create()
        self.assertTrue(self._test_class.objects_exist(
            self.context, validate_filters=False, fake_filter='xxx'))

    def test_delete_objects(self):
        for fields in self.obj_fields:
            self._make_object(fields).create()

        objs = self._test_class.get_objects(
            self.context, **self.valid_field_filter)
        for k, v in self.valid_field_filter.items():
            self.assertEqual(v, objs[0][k])

        count = self._test_class.delete_objects(
            self.context, **self.valid_field_filter)

        self.assertEqual(len(objs), count)

        new_objs = self._test_class.get_objects(self.context)
        self.assertEqual(len(self.obj_fields) - len(objs), len(new_objs))
        for obj in new_objs:
            for k, v in self.valid_field_filter.items():
                self.assertNotEqual(v, obj[k])

    def test_delete_objects_nothing_to_delete(self):
        self.assertEqual(
            0, self._test_class.delete_objects(self.context))

    def test_db_obj(self):
        obj = self._make_object(self.obj_fields[0])
        self.assertIsNone(obj.db_obj)

        obj.create()
        self.assertIsNotNone(obj.db_obj)

        fields_to_update = self.get_updatable_fields(self.obj_fields[1])
        if fields_to_update:
            old_model = copy.deepcopy(obj.db_obj)
            for key, val in fields_to_update.items():
                setattr(obj, key, val)
            obj.update()
            self.assertIsNotNone(obj.db_obj)
            self.assertNotEqual(old_model, obj.db_obj)

        obj.delete()
        self.assertIsNone(obj.db_obj)


class UniqueObjectBase(test_base.BaseTestCase):
    def setUp(self):
        super(UniqueObjectBase, self).setUp()
        obj_registry = self.useFixture(
            fixture.VersionedObjectRegistryFixture())
        self.db_model = FakeModel

        class RegisteredObject(base.NeutronDbObject):
            db_model = self.db_model

        self.registered_object = RegisteredObject
        obj_registry.register(self.registered_object)


class GetObjectClassByModelTestCase(UniqueObjectBase):
    def setUp(self):
        super(GetObjectClassByModelTestCase, self).setUp()
        self.not_registered_object = FakeSmallNeutronObject

    def test_object_found_by_model(self):
        found_obj = base.get_object_class_by_model(
            self.registered_object.db_model)
        self.assertIs(self.registered_object, found_obj)

    def test_not_registed_object_raises_exception(self):
        with testtools.ExpectedException(o_exc.NeutronDbObjectNotFoundByModel):
            base.get_object_class_by_model(self.not_registered_object.db_model)


class RegisterFilterHookOnModelTestCase(UniqueObjectBase):
    def test_filtername_is_added(self):
        filter_name = 'foo'
        self.assertNotIn(
            filter_name, self.registered_object.extra_filter_names)
        base.register_filter_hook_on_model(
            FakeNeutronDbObject.db_model, filter_name)
        self.assertIn(filter_name, self.registered_object.extra_filter_names)


class PagerTestCase(test_base.BaseTestCase):
    def test_comparison(self):
        pager = base.Pager(sorts=[('order', True)])
        pager2 = base.Pager(sorts=[('order', True)])
        self.assertEqual(pager, pager2)

        pager3 = base.Pager()
        self.assertNotEqual(pager, pager3)


class OperationOnStringAndJsonTestCase(test_base.BaseTestCase):
    def test_load_empty_string_to_json(self):
        for field_val in ['', None]:
            for default_val in [None, {}]:
                res = base.NeutronDbObject.load_json_from_str(field_val,
                                                              default_val)
                self.assertEqual(res, default_val)

    def test_dump_field_to_string(self):
        for field_val in [{}, None]:
            for default_val in ['', None]:
                res = base.NeutronDbObject.filter_to_json_str(field_val,
                                                              default_val)
                self.assertEqual(default_val, res)
