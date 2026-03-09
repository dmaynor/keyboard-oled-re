#!/usr/bin/env python3
"""Ghidra firmware diff — compare Bragi handlers between v1.18.42 and v2.8.59.

Uses PyGhidra headless API directly. Analyzes both firmware binaries and produces
a detailed diff of Bragi command handlers, property dispatchers, and screen modes.

Usage:
    cd /home/dmaynor/code/keyboard-oled-re
    GHIDRA_INSTALL_DIR=/opt/ghidra_12.0.4_PUBLIC .venv/bin/python tools/ghidra_fw_diff.py
"""

import os
import sys
import json
from pathlib import Path
from collections import defaultdict

# Must set before importing pyghidra
os.environ['GHIDRA_INSTALL_DIR'] = '/opt/ghidra_12.0.4_PUBLIC'

import pyghidra

FLASH_BASE = 0x08020000

BRAGI_CMDS = {
    0x01: "SET_PROPERTY", 0x02: "GET_PROPERTY", 0x05: "UNBIND",
    0x06: "WRITE_BEGIN", 0x07: "WRITE_CONT", 0x08: "READ",
    0x09: "DESCRIBE", 0x0B: "CREATE_FILE", 0x0C: "DELETE_FILE",
    0x0D: "OPEN_FILE", 0x0F: "RESET_FACTORY", 0x12: "PING",
    0x1B: "SESSION",
}

PROPERTY_IDS = {
    0x02: "BragiVersion", 0x03: "FirmwareVersion", 0x09: "DeviceType",
    0x15: "BatteryLevel", 0x29: "PollRate", 0x3E: "DANGER_DO_NOT_SET",
    0x41: "BootloaderMode", 0xD9: "ScreenMode", 0xE1: "LEDMode",
    0xE5: "Profile", 0x115: "EffectType",
}

INTERESTING_PATTERNS = [
    "screen", "display", "oled", "lcd", "bitmap", "image", "frame",
    "bragi", "hid", "usb", "file", "write", "read", "property",
    "compress", "lzw", "touchgfx", "animation", "ID6D", "Nord", "VANGUARD",
]


def analyze_firmware(fw_path, project_name, project_dir):
    """Analyze a single firmware binary and return structured results."""
    print(f"\n{'='*60}")
    print(f"Analyzing: {fw_path}")
    print(f"{'='*60}")

    results = {
        'firmware': os.path.basename(fw_path),
        'functions': [],
        'bragi_candidates': [],
        'screen_candidates': [],
        'prop_candidates': [],
        'strings': [],
        'large_functions': [],
    }

    with pyghidra.open_program(
        fw_path,
        project_location=project_dir,
        project_name=project_name,
        language="ARM:LE:32:Cortex",
        loader="ghidra.app.util.opinion.BinaryLoader",
    ) as flat_api:
        from ghidra.program.flatapi import FlatProgramAPI
        from ghidra.app.decompiler import DecompInterface
        from ghidra.util.task import ConsoleTaskMonitor

        program = flat_api.getCurrentProgram()
        fm = program.getFunctionManager()
        listing = program.getListing()
        refMgr = program.getReferenceManager()
        af = program.getAddressFactory()
        monitor = ConsoleTaskMonitor()

        def get_addr(offset):
            return af.getDefaultAddressSpace().getAddress(offset)

        # --- Function catalog ---
        print("  Cataloging functions...")
        func_iter = fm.getFunctions(True)
        all_funcs = []
        for func in func_iter:
            body = func.getBody()
            size = body.getNumAddresses() if body else 0
            entry = func.getEntryPoint().getOffset()
            name = func.getName()
            all_funcs.append((entry, name, size))
        results['total_functions'] = len(all_funcs)
        print(f"  Found {len(all_funcs)} functions")

        # Large functions
        large = [(e, n, s) for e, n, s in all_funcs if s >= 500]
        large.sort(key=lambda x: -x[2])
        results['large_functions'] = [
            {'entry': hex(e), 'name': n, 'size': s} for e, n, s in large[:50]
        ]

        # --- Bragi + property handler scan ---
        print("  Scanning for Bragi/property handlers...")
        func_iter = fm.getFunctions(True)
        for func in func_iter:
            body = func.getBody()
            if body is None:
                continue
            size = body.getNumAddresses()
            if size < 80:
                continue

            instr_iter = listing.getInstructions(body, True)
            cmp_values = []
            has_tbb = False
            has_tbh = False

            while instr_iter.hasNext():
                instr = instr_iter.next()
                mnemonic = instr.getMnemonicString()

                if mnemonic == "tbb":
                    has_tbb = True
                if mnemonic == "tbh":
                    has_tbh = True
                if mnemonic == "cmp":
                    op_str = instr.toString()
                    try:
                        if "#0x" in op_str:
                            val_str = op_str.split("#0x")[-1].split()[0].rstrip("])},")
                            cmp_values.append(int(val_str, 16))
                        elif "#" in op_str:
                            parts = op_str.split("#")
                            if len(parts) > 1:
                                val_str = parts[-1].strip().rstrip("])},").split()[0]
                                if val_str.isdigit():
                                    cmp_values.append(int(val_str))
                    except:
                        pass

            bragi_matches = [v for v in set(cmp_values) if v in BRAGI_CMDS]
            prop_matches = [v for v in set(cmp_values) if v in PROPERTY_IDS]
            screen_modes = [v for v in set(cmp_values) if 0x1E <= v <= 0x35]

            entry = func.getEntryPoint().getOffset()
            fname = func.getName()

            # Get callers
            callers = func.getCallingFunctions(monitor)
            caller_list = [
                {'entry': hex(cf.getEntryPoint().getOffset()), 'name': cf.getName()}
                for cf in callers
            ]

            if len(bragi_matches) >= 3:
                results['bragi_candidates'].append({
                    'entry': hex(entry),
                    'name': fname,
                    'size': size,
                    'bragi_cmds': {hex(v): BRAGI_CMDS[v] for v in sorted(bragi_matches)},
                    'all_cmp': [hex(v) for v in sorted(set(cmp_values))],
                    'has_tbb': has_tbb,
                    'has_tbh': has_tbh,
                    'callers': caller_list,
                })

            if len(prop_matches) >= 2:
                results['prop_candidates'].append({
                    'entry': hex(entry),
                    'name': fname,
                    'size': size,
                    'prop_ids': {hex(v): PROPERTY_IDS[v] for v in sorted(prop_matches)},
                    'all_cmp': [hex(v) for v in sorted(set(cmp_values))],
                    'callers': caller_list,
                })

            if len(screen_modes) >= 3:
                results['screen_candidates'].append({
                    'entry': hex(entry),
                    'name': fname,
                    'size': size,
                    'modes': [hex(m) for m in sorted(screen_modes)],
                    'callers': caller_list,
                })

        results['bragi_candidates'].sort(key=lambda x: -len(x['bragi_cmds']))
        results['prop_candidates'].sort(key=lambda x: -len(x['prop_ids']))
        results['screen_candidates'].sort(key=lambda x: -len(x['modes']))

        print(f"  Bragi handlers: {len(results['bragi_candidates'])}")
        print(f"  Property handlers: {len(results['prop_candidates'])}")
        print(f"  Screen dispatchers: {len(results['screen_candidates'])}")

        # --- Interesting strings ---
        print("  Scanning strings...")
        data_iter = listing.getDefinedData(True)
        count = 0
        while data_iter.hasNext() and count < 100000:
            d = data_iter.next()
            count += 1
            try:
                val = d.getValue()
                if val is None:
                    continue
                s = str(val)
                if len(s) < 3 or len(s) > 200:
                    continue
                s_lower = s.lower()
                for pattern in INTERESTING_PATTERNS:
                    if pattern.lower() in s_lower:
                        addr = d.getAddress().getOffset()
                        refs = refMgr.getReferencesTo(d.getAddress())
                        ref_funcs = []
                        for r in refs:
                            func = fm.getFunctionContaining(r.getFromAddress())
                            if func:
                                ref_funcs.append(hex(func.getEntryPoint().getOffset()))
                        results['strings'].append({
                            'addr': hex(addr),
                            'string': s,
                            'refs': ref_funcs,
                        })
                        break
            except:
                pass
        print(f"  Interesting strings: {len(results['strings'])}")

        # --- Decompile top handlers ---
        print("  Decompiling key handlers...")
        decomp = DecompInterface()
        decomp.openProgram(program)

        results['decompiled'] = {}
        targets = []
        for c in results['bragi_candidates'][:5]:
            targets.append((int(c['entry'], 16), c['name'], 'bragi'))
        for c in results['screen_candidates'][:2]:
            targets.append((int(c['entry'], 16), c['name'], 'screen'))
        for c in results['prop_candidates'][:3]:
            if not any(t[0] == int(c['entry'], 16) for t in targets):
                targets.append((int(c['entry'], 16), c['name'], 'property'))

        for addr_val, fname, category in targets:
            addr = get_addr(addr_val)
            func = fm.getFunctionContaining(addr)
            if func is None:
                continue
            dr = decomp.decompileFunction(func, 60, monitor)
            if dr and dr.getDecompiledFunction():
                c_code = dr.getDecompiledFunction().getC()
                key = f"0x{addr_val:08X}"
                results['decompiled'][key] = {
                    'name': fname,
                    'category': category,
                    'code': c_code[:8000],
                }
                print(f"    Decompiled {fname} ({len(c_code)} chars)")

        decomp.dispose()

    return results


def diff_results(r1, r2):
    """Compare two firmware analysis results and produce a diff report."""
    lines = []
    lines.append("=" * 80)
    lines.append("FIRMWARE VERSION DIFF")
    lines.append(f"  {r1['firmware']} vs {r2['firmware']}")
    lines.append("=" * 80)

    # Function counts
    lines.append(f"\nFunction count: {r1['total_functions']} vs {r2['total_functions']}")

    # Compare large functions by size pattern
    lines.append(f"\nLarge functions (>=500B): {len(r1['large_functions'])} vs {len(r2['large_functions'])}")

    # Find matching large functions by size proximity
    lines.append("\nSize-matched large functions (likely same function):")
    r1_large = {f['size']: f for f in r1['large_functions']}
    r2_large = {f['size']: f for f in r2['large_functions']}
    matched_sizes = set()
    for s1 in sorted(r1_large.keys(), reverse=True):
        for s2 in sorted(r2_large.keys(), reverse=True):
            if s2 in matched_sizes:
                continue
            if abs(s1 - s2) <= max(s1, s2) * 0.05:  # 5% tolerance
                lines.append(f"  {r1_large[s1]['entry']} ({s1}B) <-> {r2_large[s2]['entry']} ({s2}B)")
                matched_sizes.add(s2)
                break

    # Bragi handler diff
    lines.append(f"\n{'='*80}")
    lines.append("BRAGI HANDLER COMPARISON")
    lines.append(f"{'='*80}")

    lines.append(f"\nv1.18.42 handlers ({len(r1['bragi_candidates'])}):")
    for c in r1['bragi_candidates'][:10]:
        cmds = ", ".join(f"{k}={v}" for k, v in c['bragi_cmds'].items())
        lines.append(f"  {c['entry']} {c['name']} ({c['size']}B): {cmds}")

    lines.append(f"\nv2.8.59 handlers ({len(r2['bragi_candidates'])}):")
    for c in r2['bragi_candidates'][:10]:
        cmds = ", ".join(f"{k}={v}" for k, v in c['bragi_cmds'].items())
        lines.append(f"  {c['entry']} {c['name']} ({c['size']}B): {cmds}")

    # Match handlers by command set similarity
    lines.append("\nBragi handler matching (by command set):")
    for c1 in r1['bragi_candidates']:
        cmds1 = set(c1['bragi_cmds'].keys())
        best_match = None
        best_overlap = 0
        for c2 in r2['bragi_candidates']:
            cmds2 = set(c2['bragi_cmds'].keys())
            overlap = len(cmds1 & cmds2)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = c2
        if best_match and best_overlap >= 2:
            cmds1_set = set(c1['bragi_cmds'].keys())
            cmds2_set = set(best_match['bragi_cmds'].keys())
            added = cmds2_set - cmds1_set
            removed = cmds1_set - cmds2_set
            common = cmds1_set & cmds2_set
            lines.append(f"\n  v1 {c1['entry']}({c1['size']}B) <-> v2 {best_match['entry']}({best_match['size']}B)")
            lines.append(f"    Common commands ({len(common)}): {', '.join(sorted(common))}")
            if added:
                added_strs = [k + "=" + BRAGI_CMDS.get(int(k, 16), "?") for k in sorted(added)]
                lines.append("    ADDED in v2: " + ", ".join(added_strs))
            if removed:
                removed_strs = [k + "=" + BRAGI_CMDS.get(int(k, 16), "?") for k in sorted(removed)]
                lines.append("    REMOVED in v2: " + ", ".join(removed_strs))
            size_delta = best_match['size'] - c1['size']
            if abs(size_delta) > 50:
                lines.append(f"    Size delta: {size_delta:+d} bytes")

    # Property handler diff
    lines.append(f"\n{'='*80}")
    lines.append("PROPERTY HANDLER COMPARISON")
    lines.append(f"{'='*80}")

    lines.append(f"\nv1.18.42 prop handlers ({len(r1['prop_candidates'])}):")
    for c in r1['prop_candidates'][:10]:
        props = ", ".join(f"{k}={v}" for k, v in c['prop_ids'].items())
        lines.append(f"  {c['entry']} ({c['size']}B): {props}")

    lines.append(f"\nv2.8.59 prop handlers ({len(r2['prop_candidates'])}):")
    for c in r2['prop_candidates'][:10]:
        props = ", ".join(f"{k}={v}" for k, v in c['prop_ids'].items())
        lines.append(f"  {c['entry']} ({c['size']}B): {props}")

    # Screen mode diff
    lines.append(f"\n{'='*80}")
    lines.append("SCREEN MODE DISPATCHER COMPARISON")
    lines.append(f"{'='*80}")

    lines.append(f"\nv1.18.42 screen dispatchers ({len(r1['screen_candidates'])}):")
    for c in r1['screen_candidates'][:5]:
        lines.append(f"  {c['entry']} ({c['size']}B): modes={c['modes']}")

    lines.append(f"\nv2.8.59 screen dispatchers ({len(r2['screen_candidates'])}):")
    for c in r2['screen_candidates'][:5]:
        lines.append(f"  {c['entry']} ({c['size']}B): modes={c['modes']}")

    # String diff
    lines.append(f"\n{'='*80}")
    lines.append("STRING COMPARISON")
    lines.append(f"{'='*80}")

    s1_set = {s['string'] for s in r1['strings']}
    s2_set = {s['string'] for s in r2['strings']}

    only_v1 = s1_set - s2_set
    only_v2 = s2_set - s1_set
    common = s1_set & s2_set

    lines.append(f"\nCommon strings: {len(common)}")
    lines.append(f"Only in v1.18.42: {len(only_v1)}")
    for s in sorted(only_v1):
        lines.append(f"  - \"{s}\"")
    lines.append(f"Only in v2.8.59: {len(only_v2)}")
    for s in sorted(only_v2):
        lines.append(f"  + \"{s}\"")

    # Decompiled function comparison
    lines.append(f"\n{'='*80}")
    lines.append("DECOMPILED FUNCTION SUMMARIES")
    lines.append(f"{'='*80}")

    lines.append(f"\nv1.18.42 decompiled functions:")
    for key, d in r1.get('decompiled', {}).items():
        lines.append(f"\n--- {key} {d['name']} [{d['category']}] ---")
        # Show just the function signature and first few meaningful lines
        code_lines = d['code'].split('\n')
        for cl in code_lines[:50]:
            lines.append(f"  {cl}")
        if len(code_lines) > 50:
            lines.append(f"  ... [{len(code_lines)-50} more lines]")

    lines.append(f"\nv2.8.59 decompiled functions:")
    for key, d in r2.get('decompiled', {}).items():
        lines.append(f"\n--- {key} {d['name']} [{d['category']}] ---")
        code_lines = d['code'].split('\n')
        for cl in code_lines[:50]:
            lines.append(f"  {cl}")
        if len(code_lines) > 50:
            lines.append(f"  ... [{len(code_lines)-50} more lines]")

    return "\n".join(lines)


def main():
    base = Path("/home/dmaynor/code/keyboard-oled-re")
    project_dir = str(base / "ghidra_project")

    firmwares = [
        (str(base / "firmware" / "Nord_App_v1.18.42.bin"), "Nord_v1_18_42"),
        (str(base / "firmware" / "VANGUARD96_App_v2.8.59.bin"), "Vanguard_v2_8_59"),
    ]

    all_results = []
    for fw_path, proj_name in firmwares:
        r = analyze_firmware(fw_path, proj_name, project_dir)
        # Save individual results
        json_path = str(base / "firmware" / f"ghidra_analysis_{proj_name}.json")
        with open(json_path, 'w') as f:
            json.dump(r, f, indent=2)
        print(f"  Saved: {json_path}")
        all_results.append(r)

    # Generate diff
    diff_text = diff_results(all_results[0], all_results[1])
    diff_path = str(base / "firmware" / "ghidra_fw_diff.txt")
    with open(diff_path, 'w') as f:
        f.write(diff_text)
    print(f"\nDiff saved: {diff_path}")

    # Also save diff as JSON
    diff_json = {
        'v1': {
            'firmware': all_results[0]['firmware'],
            'total_functions': all_results[0]['total_functions'],
            'bragi_handlers': len(all_results[0]['bragi_candidates']),
            'prop_handlers': len(all_results[0]['prop_candidates']),
            'screen_dispatchers': len(all_results[0]['screen_candidates']),
        },
        'v2': {
            'firmware': all_results[1]['firmware'],
            'total_functions': all_results[1]['total_functions'],
            'bragi_handlers': len(all_results[1]['bragi_candidates']),
            'prop_handlers': len(all_results[1]['prop_candidates']),
            'screen_dispatchers': len(all_results[1]['screen_candidates']),
        },
    }
    diff_json_path = str(base / "firmware" / "ghidra_fw_diff.json")
    with open(diff_json_path, 'w') as f:
        json.dump(diff_json, f, indent=2)

    print("\n=== DIFF ANALYSIS COMPLETE ===")


if __name__ == '__main__':
    main()
