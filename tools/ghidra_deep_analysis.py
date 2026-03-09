#!/usr/bin/env python3
"""Ghidra deep analysis script — disassemble key functions and trace Bragi dispatch.

Runs via PyGhidra headless.
"""

# @category Analysis

import struct

FLASH_BASE = 0x08020000

# Top dispatcher candidates from initial analysis
TARGETS = [
    (0x080733CC, "top_dispatcher_60calls"),
    (0x08029504, "dispatcher_28calls"),
    (0x08064964, "dispatcher_21calls"),
    (0x08036280, "dispatcher_19calls"),
    (0x08030A18, "dispatcher_18calls"),
]

# Interesting string addresses
STRING_TARGETS = [
    (0x0803CB5C, "newScreen"),
    (0x08044B20, "Unknown_L8_RGB888_compression"),
    (0x080907C1, "Unknown_L8_RGB565_compression"),
]

out_path = "/home/dmaynor/code/keyboard-oled-re/firmware/ghidra_deep_analysis.txt"
out = open(out_path, "w")

fm = currentProgram.getFunctionManager()
listing = currentProgram.getListing()
refMgr = currentProgram.getReferenceManager()
af = currentProgram.getAddressFactory()

def get_addr(offset):
    return af.getDefaultAddressSpace().getAddress(offset)

def disassemble_function(func_addr, label):
    """Disassemble a function and show its key characteristics."""
    out.write("=" * 80 + "\n")
    out.write("FUNCTION: %s at 0x%08X\n" % (label, func_addr))
    out.write("=" * 80 + "\n")

    addr = get_addr(func_addr)
    func = fm.getFunctionContaining(addr)
    if func is None:
        out.write("  [NOT A FUNCTION - trying instruction listing]\n\n")
        return

    out.write("Name: %s\n" % func.getName())
    body = func.getBody()
    size = body.getNumAddresses() if body else 0
    out.write("Size: %d bytes\n" % size)

    # Called functions
    called = func.getCalledFunctions(monitor)
    out.write("Calls %d functions:\n" % len(called))
    for cf in sorted(called, key=lambda f: f.getEntryPoint().getOffset()):
        out.write("  -> 0x%08X %s\n" % (cf.getEntryPoint().getOffset(), cf.getName()))

    # Calling functions (who calls this)
    callers = func.getCallingFunctions(monitor)
    out.write("Called by %d functions:\n" % len(callers))
    for cf in sorted(callers, key=lambda f: f.getEntryPoint().getOffset()):
        out.write("  <- 0x%08X %s\n" % (cf.getEntryPoint().getOffset(), cf.getName()))

    # Disassembly with focus on CMP/branch/TBB instructions
    out.write("\nKey instructions (CMP, TBB, TBH, BL, LDR with constants):\n")
    if body:
        instr_iter = listing.getInstructions(body, True)
        cmp_values = []
        while instr_iter.hasNext():
            instr = instr_iter.next()
            mnemonic = instr.getMnemonicString()
            addr_off = instr.getAddress().getOffset()

            # Show all CMP, TBB, TBH, BL, and interesting instructions
            if mnemonic in ("cmp", "cmn", "tbb", "tbh", "bl", "blx", "svc"):
                line = "  0x%08X: %s %s" % (addr_off, mnemonic, instr.toString().split(None, 1)[-1] if ' ' in instr.toString() else '')
                out.write(line + "\n")
                if mnemonic == "cmp":
                    # Try to extract compare value
                    try:
                        op_str = instr.toString()
                        if "#0x" in op_str:
                            val = int(op_str.split("#0x")[-1].split()[0].rstrip("])}"), 16)
                            cmp_values.append(val)
                        elif "#" in op_str:
                            parts = op_str.split("#")
                            if len(parts) > 1:
                                val_str = parts[-1].strip().rstrip("])}").split()[0]
                                if val_str.isdigit():
                                    cmp_values.append(int(val_str))
                    except:
                        pass
            elif mnemonic.startswith("b") and mnemonic not in ("b", "bl", "blx", "bx"):
                # Conditional branches
                line = "  0x%08X: %s %s" % (addr_off, mnemonic, instr.toString().split(None, 1)[-1] if ' ' in instr.toString() else '')
                out.write(line + "\n")

        if cmp_values:
            out.write("\nCMP values found: %s\n" % sorted(set(cmp_values)))
            # Check if any match Bragi command IDs
            bragi_cmds = {1: "SET_PROPERTY", 2: "GET_PROPERTY", 5: "UNBIND",
                         6: "WRITE_BEGIN", 7: "WRITE_CONT", 8: "READ",
                         9: "DESCRIBE", 11: "CREATE_FILE", 12: "DELETE_FILE",
                         13: "OPEN_FILE", 15: "RESET_FACTORY", 18: "PING",
                         27: "SESSION"}
            matches = []
            for v in sorted(set(cmp_values)):
                if v in bragi_cmds:
                    matches.append("0x%02X=%s" % (v, bragi_cmds[v]))
            if matches:
                out.write("*** BRAGI COMMAND MATCHES: %s ***\n" % ", ".join(matches))

    out.write("\n")


def trace_string_refs(str_addr, label):
    """Find who references a string address."""
    out.write("-" * 60 + "\n")
    out.write("STRING XREFS: \"%s\" at 0x%08X\n" % (label, str_addr))
    out.write("-" * 60 + "\n")

    addr = get_addr(str_addr)
    refs = refMgr.getReferencesTo(addr)
    for r in refs:
        from_addr = r.getFromAddress().getOffset()
        func = fm.getFunctionContaining(r.getFromAddress())
        func_name = func.getName() if func else "??"
        func_addr = func.getEntryPoint().getOffset() if func else 0
        out.write("  <- 0x%08X in %s (0x%08X) [%s]\n" %
                  (from_addr, func_name, func_addr, r.getReferenceType().getName()))
    if not refs:
        out.write("  [no xrefs found]\n")
    out.write("\n")


# === DISASSEMBLE TOP DISPATCHERS ===
for addr, label in TARGETS:
    disassemble_function(addr, label)

# === TRACE STRING REFERENCES ===
for addr, label in STRING_TARGETS:
    trace_string_refs(addr, label)

# === FIND FUNCTIONS THAT REFERENCE FILE IDS (28000-28007 = 0x6D60-0x6D67) ===
out.write("=" * 80 + "\n")
out.write("FUNCTIONS REFERENCING FILE IDS (0x6D60-0x6D67 = 28000-28007)\n")
out.write("=" * 80 + "\n")

# Search for the ID strings
for file_id in range(0x6D60, 0x6D68):
    str_name = "ID%04X.hex" % file_id
    # Search for the string in the listing
    data_iter = listing.getDefinedData(True)
    while data_iter.hasNext():
        d = data_iter.next()
        try:
            val = d.getValue()
            if val and str(val) == str_name:
                addr = d.getAddress()
                refs = refMgr.getReferencesTo(addr)
                out.write("  %s at 0x%08X:\n" % (str_name, addr.getOffset()))
                for r in refs:
                    from_addr = r.getFromAddress().getOffset()
                    func = fm.getFunctionContaining(r.getFromAddress())
                    func_name = func.getName() if func else "??"
                    out.write("    <- 0x%08X in %s\n" % (from_addr, func_name))
                break
        except:
            pass
out.write("\n")

# === FIND THE HID RECEIVE HANDLER ===
# Look for functions that compare byte[0] to 0x08 (Device_Itself)
out.write("=" * 80 + "\n")
out.write("SEARCHING FOR HID RECEIVE / BRAGI HANDLER\n")
out.write("=" * 80 + "\n")

# The Bragi handler should compare the first byte of incoming packets to 0x08
# and then dispatch based on the second byte (command ID)
# Look for functions with CMP #0x8 followed by CMP with known command IDs
func_iter = fm.getFunctions(True)
for func in func_iter:
    body = func.getBody()
    if body is None:
        continue
    size = body.getNumAddresses()
    if size < 100:
        continue

    instr_iter = listing.getInstructions(body, True)
    cmp_8_found = False
    cmp_cmd_ids = []

    while instr_iter.hasNext():
        instr = instr_iter.next()
        mnemonic = instr.getMnemonicString()
        if mnemonic == "cmp":
            op_str = instr.toString()
            # Look for cmp with #0x8 or #8
            if "#0x8" in op_str and "#0x8" in op_str.split(",")[-1]:
                cmp_8_found = True
            elif ",#0x8 " in op_str.replace(" ", "") or op_str.endswith("#0x8"):
                cmp_8_found = True

            # Look for cmp with known Bragi command IDs
            try:
                if "#0x" in op_str:
                    val_str = op_str.split("#0x")[-1].split()[0].rstrip("])}").split(",")[0]
                    val = int(val_str, 16)
                    if val in (0x01, 0x02, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0C, 0x0D, 0x0F, 0x12, 0x1B):
                        cmp_cmd_ids.append(val)
            except:
                pass

    if cmp_8_found and len(cmp_cmd_ids) >= 3:
        out.write("*** LIKELY BRAGI HANDLER: 0x%08X %s (size=%d) ***\n" %
                  (func.getEntryPoint().getOffset(), func.getName(), size))
        out.write("    CMP #0x8 found, command ID matches: %s\n" %
                  ["0x%02X" % x for x in sorted(set(cmp_cmd_ids))])
        # Get its callers
        callers = func.getCallingFunctions(monitor)
        for cf in callers:
            out.write("    called by: 0x%08X %s\n" %
                      (cf.getEntryPoint().getOffset(), cf.getName()))
        out.write("\n")

# === LOOK FOR 1024-BYTE BUFFER REFERENCES ===
# The HID report size is 1024 (0x400) — functions handling this are HID-related
out.write("=" * 80 + "\n")
out.write("FUNCTIONS WITH CMP #0x400 (1024-byte HID report size)\n")
out.write("=" * 80 + "\n")

func_iter = fm.getFunctions(True)
for func in func_iter:
    body = func.getBody()
    if body is None:
        continue
    instr_iter = listing.getInstructions(body, True)
    while instr_iter.hasNext():
        instr = instr_iter.next()
        if instr.getMnemonicString() == "cmp" and "#0x400" in instr.toString():
            size = body.getNumAddresses()
            out.write("  0x%08X %s (size=%d)\n" %
                      (func.getEntryPoint().getOffset(), func.getName(), size))
            break

out.write("\n")

# === DECOMPILER OUTPUT FOR TOP CANDIDATES ===
# Try to get decompiled C for the top dispatcher
out.write("=" * 80 + "\n")
out.write("DECOMPILER OUTPUT — TOP DISPATCHER CANDIDATES\n")
out.write("=" * 80 + "\n")

try:
    from ghidra.app.decompiler import DecompInterface
    decomp = DecompInterface()
    decomp.openProgram(currentProgram)

    for addr_val, label in TARGETS[:5]:
        addr = get_addr(addr_val)
        func = fm.getFunctionContaining(addr)
        if func is None:
            continue

        out.write("\n--- %s (0x%08X) ---\n" % (label, addr_val))
        results = decomp.decompileFunction(func, 30, monitor)
        if results and results.depiledFunction():
            c_code = results.getDecompiledFunction().getC()
            # Truncate if too long
            if len(c_code) > 8000:
                c_code = c_code[:8000] + "\n... [TRUNCATED] ..."
            out.write(c_code + "\n")
        else:
            out.write("  [Decompilation failed]\n")

    decomp.dispose()
except Exception as e:
    out.write("  Decompiler error: %s\n" % str(e))

out.write("\n=== DEEP ANALYSIS COMPLETE ===\n")
out.close()

print("DEEP ANALYSIS SAVED: %s" % out_path)
