#!/bin/bash

set -e

IFACE="vcan0"

if ip link show "$IFACE" > /dev/null 2>&1; then
    echo "Delete $IFACE"
    sudo ip link delete "$IFACE"
else
    echo "$IFACE does not exist"
fi