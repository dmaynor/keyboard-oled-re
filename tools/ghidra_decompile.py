#!/usr/bin/env python3
"""Ghidra decompiler script — get C pseudocode for key functions.

Runs via PyGhidra headless.
"""

# @category Analysis

from ghidra.app.decompiler import DecompInterface

TARGETS = [
    # Main dispatcher (60 calls, called from reset vector area)
    (0x080733CC, "main_dispatcher_60calls"),
    # Most likely Bragi command handler (matches SET, GET, WRITE_BEGIN, READ, DELETE)
    (0x0802BBD8, "bragi_handler_5cmds_4320B"),
    # Bragi handler with most command matches (SET, GET, UNBIND, WRITE_BEGIN, WRITE_CONT, READ, DESCRIBE)
    (0x08063D54, "bragi_file_ops_7cmds"),
    # Bragi handler (SET, GET, UNBIND, DESCRIBE, CREATE, DELETE, RESET)
    (0x0806B420, "bragi_lifecycle_7cmds"),
    # File ID reference function (ID6D60-ID6D67)
    (0x080604F4, "file_id_handler"),
    # Bragi handler with SET, GET, WRITE_BEGIN, WRITE_CONT, READ, RESET
    (0x0805E670, "bragi_rw_handler_6cmds"),
    # Screen-related dispatcher (called by main dispatcher, has TBH)
    (0x08036280, "screen_dispatcher_tbh"),
    # The caller of the main dispatcher
    (0x08020342, "reset_caller"),
]

out_path = "/home/dmaynor/code/keyboard-oled-re/firmware/ghidra_decompiled.txt"
out = open(out_path, "w")

fm = currentProgram.getFunctionManager()
af = currentProgram.getAddressFactory()

def get_addr(offset):
    return af.getDefaultAddressSpace().getAddress(offset)

decomp = DecompInterface()
decomp.openProgram(currentProgram)

for addr_val, label in TARGETS:
    addr = get_addr(addr_val)
    func = fm.getFunctionContaining(addr)
    if func is None:
        out.write("=" * 80 + "\n")
        out.write("FUNCTION NOT FOUND: %s at 0x%08X\n" % (label, addr_val))
        out.write("=" * 80 + "\n\n")
        continue

    out.write("=" * 80 + "\n")
    out.write("FUNCTION: %s at 0x%08X (%s)\n" % (label, addr_val, func.getName()))
    out.write("Size: %d bytes\n" % (func.getBody().getNumAddresses() if func.getBody() else 0))
    out.write("=" * 80 + "\n\n")

    results = decomp.decompileFunction(func, 60, monitor)
    if results and results.getDecompiledFunction():
        c_code = results.getDecompiledFunction().getC()
        out.write(c_code + "\n\n")
    else:
        err = results.getErrorMessage() if results else "No results"
        out.write("  [Decompilation failed: %s]\n\n" % err)

decomp.dispose()

out.write("=== DECOMPILATION COMPLETE ===\n")
out.close()

print("DECOMPILED OUTPUT SAVED: %s" % out_path)
