# Copyright 2015 Intel Corporation.
# Copyright 2015 Isaku Yamahata <isaku.yamahata at intel com>
#                               <isaku.yamahata at gmail com>
# All Rights Reserved.
#
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

from oslo_log import log as logging

from neutron.agent.linux import ip_lib
from neutron.i18n import _LE

LOG = logging.getLogger(__name__)


class BridgeDevice(ip_lib.IPDevice):
    def _brctl(self, cmd):
        cmd = ['brctl'] + cmd
        ip_wrapper = ip_lib.IPWrapper(self.namespace)
        return ip_wrapper.netns.execute(cmd, run_as_root=True)

    def _sysctl(self, cmd):
        """execute() doesn't return the exit status of the command it runs,
        it returns stdout and stderr. Setting check_exit_code=True will cause
        it to raise a RuntimeError if the exit status of the command is
        non-zero, which in sysctl's case is an error. So we're normalizing
        that into zero (success) and one (failure) here to mimic what
        "echo $?" in a shell would be.

        This is all because sysctl is too verbose and prints the value you
        just set on success, unlike most other utilities that print nothing.

        execute() will have dumped a message to the logs with the actual
        output on failure, so it's not lost, and we don't need to print it
        here.
        """
        cmd = ['sysctl', '-w'] + cmd
        ip_wrapper = ip_lib.IPWrapper(self.namespace)
        try:
            ip_wrapper.netns.execute(cmd, run_as_root=True,
                                     check_exit_code=True)
        except RuntimeError:
            LOG.exception(_LE("Failed running %s"), cmd)
            return 1

        return 0

    @classmethod
    def addbr(cls, name, namespace=None):
        bridge = cls(name, namespace)
        bridge._brctl(['addbr', bridge.name])
        return bridge

    def delbr(self):
        return self._brctl(['delbr', self.name])

    def addif(self, interface):
        return self._brctl(['addif', self.name, interface])

    def delif(self, interface):
        return self._brctl(['delif', self.name, interface])

    def setfd(self, fd):
        return self._brctl(['setfd', self.name, str(fd)])

    def disable_stp(self):
        return self._brctl(['stp', self.name, 'off'])

    def disable_ipv6(self):
        cmd = 'net.ipv6.conf.%s.disable_ipv6=1' % self.name
        return self._sysctl([cmd])
