#!/bin/bash

set -e

IFACE="vcan0"

echo "[1] Load vcan kernel module"
sudo modprobe vcan

echo "[2] Check if $IFACE already exists"
if ip link show "$IFACE" > /dev/null 2>&1; then
    echo "$IFACE already exists"
else
    echo "Create $IFACE"
    sudo ip link add dev "$IFACE" type vcan
fi

echo "[3] Set $IFACE up"
sudo ip link set up "$IFACE"

echo "[4] Done"
ip link show "$IFACE"