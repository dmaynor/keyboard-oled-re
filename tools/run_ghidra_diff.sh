#!/bin/bash
# Run Ghidra analysis on both firmware versions for diffing.
# Uses PyGhidra headless mode.

set -e

cd /home/dmaynor/code/keyboard-oled-re

GHIDRA=/opt/ghidra_12.0.4_PUBLIC
PYTHON=.venv/bin/python
SCRIPT=tools/ghidra_fw_diff.py
PROJECT_DIR=ghidra_project

# Firmware 1: v1.18.42 (Nord)
FW1=firmware/Nord_App_v1.18.42.bin
FW1_NAME=Nord_v1_18_42
FW1_OUT=firmware/ghidra_fw_diff_v1_18_42

# Firmware 2: v2.8.59
FW2=firmware/VANGUARD96_App_v2.8.59.bin
FW2_NAME=Vanguard_v2_8_59
FW2_OUT=firmware/ghidra_fw_diff_v2_8_59

export GHIDRA_INSTALL_DIR=$GHIDRA

echo "=== Analyzing v1.18.42 (Nord) ==="
# Temporarily modify the output path in the script for v1
sed "s|ghidra_fw_diff.txt|ghidra_fw_diff_v1_18_42.txt|g" $SCRIPT > /tmp/ghidra_fw_diff_v1.py

$GHIDRA/support/analyzeHeadless $PROJECT_DIR ${FW1_NAME} \
    -import $FW1 \
    -processor ARM:LE:32:Cortex \
    -loader BinaryLoader \
    -loader-baseAddr 0x08020000 \
    -scriptPath tools \
    -postScript /tmp/ghidra_fw_diff_v1.py \
    -overwrite \
    2>&1 | tail -20

echo ""
echo "=== Analyzing v2.8.59 ==="
sed "s|ghidra_fw_diff.txt|ghidra_fw_diff_v2_8_59.txt|g" $SCRIPT > /tmp/ghidra_fw_diff_v2.py

$GHIDRA/support/analyzeHeadless $PROJECT_DIR ${FW2_NAME} \
    -import $FW2 \
    -processor ARM:LE:32:Cortex \
    -loader BinaryLoader \
    -loader-baseAddr 0x08020000 \
    -scriptPath tools \
    -postScript /tmp/ghidra_fw_diff_v2.py \
    -overwrite \
    2>&1 | tail -20

echo ""
echo "=== Both analyses complete ==="
echo "v1.18.42: firmware/ghidra_fw_diff_v1_18_42.txt + .json"
echo "v2.8.59:  firmware/ghidra_fw_diff_v2_8_59.txt + .json"
