#!/usr/bin/env python3
"""Ghidra headless post-analysis script for Corsair Vanguard 96 firmware.

Extracts functions, strings, and cross-references to key addresses.
Runs via Ghidra's analyzeHeadless -postScript.
"""

# Ghidra Python (Jython) script — runs inside Ghidra's JVM
# @category Analysis
# @keybinding
# @menupath
# @toolbar

import json

FLASH_BASE = 0x08020000

# Key addresses we want xrefs for
KEY_ADDRESSES = {
    0x08044A0C: "compression_dispatch",     # TBB compression router
    0x0804763C: "lzw9_blitcopy",           # LZW9 RGB565 decompressor
    0x08083454: "lzw9_vtable",             # DecompressorL8_LZW9 vtable
    0x0808212C: "bitmap_table",            # TouchGFX bitmap table
    0x080265D0: "rle_decompressor",        # RLE handler
    0x08025400: "l4_decompressor",         # L4 format handler
}

# Bragi command IDs to search for in dispatch
BRAGI_CMDS = {
    0x01: "SET_PROPERTY",
    0x02: "GET_PROPERTY",
    0x05: "UNBIND",
    0x06: "WRITE_BEGIN",
    0x07: "WRITE_CONT",
    0x08: "READ",
    0x09: "DESCRIBE",
    0x0B: "CREATE_FILE",
    0x0C: "DELETE_FILE",
    0x0D: "OPEN_FILE",
    0x0F: "RESET_FACTORY",
    0x12: "PING",
    0x1B: "SESSION",
}

output = {}

# Get all functions
fm = currentProgram.getFunctionManager()
funcs = []
func = fm.getFunctionAt(currentProgram.getMinAddress())
if func is None:
    func_iter = fm.getFunctions(True)
else:
    func_iter = fm.getFunctions(True)

count = 0
for f in func_iter:
    entry = f.getEntryPoint().getOffset()
    body = f.getBody()
    size = body.getNumAddresses() if body else 0
    funcs.append({
        "address": "0x%08X" % entry,
        "name": f.getName(),
        "size": size,
    })
    count += 1

output["total_functions"] = count
output["functions_by_size"] = sorted(funcs, key=lambda x: x["size"], reverse=True)[:50]

# Get all defined strings
string_table = currentProgram.getListing()
data_iter = string_table.getDefinedData(True)
strings_found = []
for d in data_iter:
    dt = d.getDataType()
    if dt is not None and ("string" in dt.getName().lower() or "char" in dt.getName().lower()):
        try:
            val = d.getValue()
            if val and len(str(val)) > 3:
                strings_found.append({
                    "address": "0x%08X" % d.getAddress().getOffset(),
                    "value": str(val)[:200],
                })
        except:
            pass

output["strings"] = strings_found[:200]

# Find xrefs to key addresses
from ghidra.program.model.symbol import ReferenceManager
ref_mgr = currentProgram.getReferenceManager()

xref_results = {}
af = currentProgram.getAddressFactory()
for addr_val, name in KEY_ADDRESSES.items():
    try:
        addr = af.getDefaultAddressSpace().getAddress(addr_val)
        refs = ref_mgr.getReferencesTo(addr)
        ref_list = []
        for r in refs:
            from_addr = r.getFromAddress().getOffset()
            ref_list.append("0x%08X" % from_addr)
        xref_results[name] = {
            "address": "0x%08X" % addr_val,
            "xrefs_to": ref_list,
        }
    except:
        xref_results[name] = {"address": "0x%08X" % addr_val, "error": "address not found"}

output["key_xrefs"] = xref_results

# Find functions that reference HID/USB strings
usb_funcs = []
for f in fm.getFunctions(True):
    entry = f.getEntryPoint()
    name = f.getName()
    if any(kw in name.lower() for kw in ["hid", "usb", "ux_", "bragi", "command", "dispatch"]):
        usb_funcs.append({
            "address": "0x%08X" % entry.getOffset(),
            "name": name,
        })
output["usb_hid_functions"] = usb_funcs

# Look for switch/case tables (TBB/TBH patterns) — potential command dispatchers
# Find functions with many outgoing calls (likely dispatchers)
dispatcher_candidates = []
for f in fm.getFunctions(True):
    body = f.getBody()
    if body is None:
        continue
    size = body.getNumAddresses()
    if size < 50:
        continue

    # Count called functions
    called = set()
    call_refs = f.getCalledFunctions(monitor)
    for called_func in call_refs:
        called.add(called_func.getEntryPoint().getOffset())

    if len(called) >= 8:
        dispatcher_candidates.append({
            "address": "0x%08X" % f.getEntryPoint().getOffset(),
            "name": f.getName(),
            "size": size,
            "calls_count": len(called),
        })

output["dispatcher_candidates"] = sorted(dispatcher_candidates, key=lambda x: x["calls_count"], reverse=True)[:30]

# Write output
out_path = "/home/dmaynor/code/keyboard-oled-re/firmware/ghidra_analysis.json"
with open(out_path, "w") as fp:
    # Jython doesn't have json.dumps with indent, do manual
    fp.write("{\n")
    keys = list(output.keys())
    for ki, k in enumerate(keys):
        v = output[k]
        fp.write('  "%s": ' % k)
        if isinstance(v, int):
            fp.write("%d" % v)
        elif isinstance(v, list):
            fp.write("[\n")
            for vi, item in enumerate(v):
                fp.write("    {")
                ikeys = list(item.keys())
                for iki, ik in enumerate(ikeys):
                    iv = item[ik]
                    if isinstance(iv, int):
                        fp.write('"%s": %d' % (ik, iv))
                    else:
                        fp.write('"%s": "%s"' % (ik, str(iv).replace('"', '\\"').replace('\n', '\\n')))
                    if iki < len(ikeys) - 1:
                        fp.write(", ")
                fp.write("}")
                if vi < len(v) - 1:
                    fp.write(",")
                fp.write("\n")
            fp.write("  ]")
        elif isinstance(v, dict):
            fp.write("{\n")
            dkeys = list(v.keys())
            for di, dk in enumerate(dkeys):
                dv = v[dk]
                fp.write('    "%s": ' % dk)
                if isinstance(dv, dict):
                    fp.write("{")
                    ddkeys = list(dv.keys())
                    for ddi, ddk in enumerate(ddkeys):
                        ddv = dv[ddk]
                        if isinstance(ddv, list):
                            fp.write('"%s": [%s]' % (ddk, ", ".join('"%s"' % x for x in ddv)))
                        else:
                            fp.write('"%s": "%s"' % (ddk, str(ddv).replace('"', '\\"')))
                        if ddi < len(ddkeys) - 1:
                            fp.write(", ")
                    fp.write("}")
                else:
                    fp.write('"%s"' % str(dv).replace('"', '\\"'))
                if di < len(dkeys) - 1:
                    fp.write(",")
                fp.write("\n")
            fp.write("  }")
        if ki < len(keys) - 1:
            fp.write(",")
        fp.write("\n")
    fp.write("}\n")

print("OUTPUT SAVED: %s" % out_path)
print("Functions: %d" % count)
print("Strings: %d" % len(strings_found))
print("Dispatcher candidates: %d" % len(dispatcher_candidates))
