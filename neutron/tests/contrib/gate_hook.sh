#!/usr/bin/env bash

set -ex

VENV=${1:-"dsvm-functional"}

GATE_DEST=$BASE/new
NEUTRON_PATH=$GATE_DEST/neutron
GATE_HOOKS=$NEUTRON_PATH/neutron/tests/contrib/hooks
DEVSTACK_PATH=$GATE_DEST/devstack
LOCAL_CONF=$DEVSTACK_PATH/late-local.conf
RALLY_EXTRA_DIR=$NEUTRON_PATH/rally-jobs/extra
DSCONF=/tmp/devstack-tools/bin/dsconf

# Install devstack-tools used to produce local.conf; we can't rely on
# test-requirements.txt because the gate hook is triggered before neutron is
# installed
sudo -H pip install virtualenv
virtualenv /tmp/devstack-tools
/tmp/devstack-tools/bin/pip install -U devstack-tools==0.4.0

# Inject config from hook into localrc
function load_rc_hook {
    local hook="$1"
    local tmpfile
    local config
    tmpfile=$(tempfile)
    config=$(cat $GATE_HOOKS/$hook)
    echo "[[local|localrc]]" > $tmpfile
    $DSCONF setlc_raw $tmpfile "$config"
    $DSCONF merge_lc $LOCAL_CONF $tmpfile
    rm -f $tmpfile
}


# Inject config from hook into local.conf
function load_conf_hook {
    local hook="$1"
    $DSCONF merge_lc $LOCAL_CONF $GATE_HOOKS/$hook
}


# Tweak gate configuration for our rally scenarios
function load_rc_for_rally {
    for file in $(ls $RALLY_EXTRA_DIR/*.setup); do
        $DSCONF merge_lc $LOCAL_CONF $file
    done
}


case $VENV in
"dsvm-functional"|"dsvm-fullstack")
    # The following need to be set before sourcing
    # configure_for_func_testing.
    GATE_STACK_USER=stack
    PROJECT_NAME=neutron
    IS_GATE=True
    LOCAL_CONF=$DEVSTACK_PATH/local.conf

    source $DEVSTACK_PATH/functions
    source $NEUTRON_PATH/devstack/lib/ovs

    source $NEUTRON_PATH/tools/configure_for_func_testing.sh

    configure_host_for_func_testing

    # Because of bug present in current Ubuntu Xenial kernel version
    # we need a fix for VXLAN local tunneling.
    if [[ "$VENV" =~ "dsvm-fullstack" ]]; then
        upgrade_ovs_if_necessary
    fi

    load_conf_hook iptables_verify
    # Make the workspace owned by the stack user
    sudo chown -R $STACK_USER:$STACK_USER $BASE
    ;;

# TODO(ihrachys): remove dsvm-scenario from the list when it's no longer used in project-config
"api"|"api-pecan"|"full-pecan"|"dsvm-scenario"|"dsvm-scenario-ovs"|"dsvm-scenario-linuxbridge")
    load_rc_hook api_extensions
    # NOTE(ihrachys): note the order of hook post-* sections is significant: [quotas] hook should
    # go before other hooks modifying [DEFAULT]. See LP#1583214 for details.
    load_conf_hook quotas
    load_rc_hook dns
    load_rc_hook qos
    load_rc_hook trunk
    load_conf_hook osprofiler
    if [[ "$VENV" =~ "dsvm-scenario" ]]; then
        load_conf_hook iptables_verify
    fi
    if [[ "$VENV" =~ "pecan" ]]; then
        load_conf_hook pecan
    fi

    export DEVSTACK_LOCALCONF=$(cat $LOCAL_CONF)
    $BASE/new/devstack-gate/devstack-vm-gate.sh
    ;;

"rally")
    load_rc_for_rally
    export DEVSTACK_LOCALCONF=$(cat $LOCAL_CONF)
    $BASE/new/devstack-gate/devstack-vm-gate.sh
    ;;

*)
    echo "Unrecognized environment $VENV".
    exit 1
esac
