# Copyright (c) 2015 IBM Corp.
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

import hashlib

import mock
from neutron_lib import constants

from neutron.plugins.common import utils
from neutron.tests import base

LONG_NAME1 = "A_REALLY_LONG_INTERFACE_NAME1"
LONG_NAME2 = "A_REALLY_LONG_INTERFACE_NAME2"
SHORT_NAME = "SHORT"
MOCKED_HASH = "mockedhash"


class MockSHA(object):
    def hexdigest(self):
        return MOCKED_HASH


class TestUtils(base.BaseTestCase):

    @mock.patch.object(hashlib, 'sha1', return_value=MockSHA())
    def test_get_interface_name(self, mock_sha1):
        prefix = "pre-"
        prefix_long = "long_prefix"
        prefix_exceeds_max_dev_len = "much_too_long_prefix"
        hash_used = MOCKED_HASH[0:6]

        self.assertEqual("A_REALLY_" + hash_used,
                         utils.get_interface_name(LONG_NAME1))
        self.assertEqual("SHORT",
                         utils.get_interface_name(SHORT_NAME))
        self.assertEqual("pre-A_REA" + hash_used,
                         utils.get_interface_name(LONG_NAME1, prefix=prefix))
        self.assertEqual("pre-SHORT",
                         utils.get_interface_name(SHORT_NAME, prefix=prefix))
        # len(prefix) > max_device_len - len(hash_used)
        self.assertRaises(ValueError, utils.get_interface_name, SHORT_NAME,
                          prefix_long)
        # len(prefix) > max_device_len
        self.assertRaises(ValueError, utils.get_interface_name, SHORT_NAME,
                          prefix=prefix_exceeds_max_dev_len)

    def test_get_interface_uniqueness(self):
        prefix = "prefix-"
        if_prefix1 = utils.get_interface_name(LONG_NAME1, prefix=prefix)
        if_prefix2 = utils.get_interface_name(LONG_NAME2, prefix=prefix)
        self.assertNotEqual(if_prefix1, if_prefix2)

    @mock.patch.object(hashlib, 'sha1', return_value=MockSHA())
    def test_get_interface_max_len(self, mock_sha1):
        self.assertEqual(constants.DEVICE_NAME_MAX_LEN,
                         len(utils.get_interface_name(LONG_NAME1)))
        self.assertEqual(10, len(utils.get_interface_name(LONG_NAME1,
                                                          max_len=10)))
        self.assertEqual(12, len(utils.get_interface_name(LONG_NAME1,
                                                          prefix="pre-",
                                                          max_len=12)))
