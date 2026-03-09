# Corsair Vanguard 96 LCD Protocol Findings

## Device Info
- **Product**: Corsair CORSAIR VANGUARD 96 Mechanical Gaming Keyboard
- **USB ID**: `1B1C:2B0D`
- **Display**: 248x170 IPS LCD (color)
- **Retail Name**: "K70 Wired Mechanical Gaming Keyboard" / "VANGUARD 96"

## USB Topology

4 HID interfaces:

| Interface | hidraw | Report Size | Purpose |
|-----------|--------|-------------|---------|
| IF#0 (input0) | hidraw0 | 32 bytes | Standard keyboard HID |
| IF#1 (input1) | hidraw1 | 32 bytes | NKRO / media keys |
| IF#2 (input2) | hidraw2 | **1024 bytes** | **Bragi protocol** (vendor) |
| IF#3 (input3) | hidraw3 | 32 bytes | Rotary dial |

**Note**: hidraw numbers can shift after device crash/reset. Use uevent matching (`VANGUARD` + `input2`) to find the Bragi interface reliably.

## Bragi Protocol (Old Format, 2-byte Header)

This device uses the "old" Bragi protocol (VER1_0). `BragiVersion` property (261) returns 0.

### Packet Format
```
Outgoing: [0x00 (HID report ID)] + [0x08 + subDevAddr, cmdId, ...payload] padded to 1024 bytes
Response: [0x00, cmdId, status, ...data] (1024 bytes from device)
```

- `0x08` = `Device_Itself` constant
- `status` byte: `0x00` = success, `0x06` = error/invalid operation

### Command IDs

| ID | Name | Payload | Notes |
|----|------|---------|-------|
| 0x01 | SET | `[propId_LE16, value_bytes...]` | Set property |
| 0x02 | GET | `[propId_LE16]` | Get property |
| 0x05 | UNBIND/CLOSE | `[count, bufferIdx...]` or `[handle, 0x00]` | Close handle / unbind buffers |
| 0x06 | WRITE_BEGIN | `[bufferIdx, totalLen_LE32, data...]` | First write chunk |
| 0x07 | WRITE_CONT | `[bufferIdx, data...]` | Continuation chunks |
| 0x08 | READ | `[bufferIdx]` | Read from buffer |
| 0x09 | DESCRIBE | `[bufferIdx]` | Get buffer size |
| 0x0B | CREATE | `[fileId_LE16]` | Create file |
| 0x0C | DELETE | `[fileId_LE16]` | Delete file |
| 0x0D | OPEN | `[bufferIdx, fileId_LE16]` | Open file into buffer |
| 0x1B | SESSION | `[0x01, token[4], 0x00]` | Start host session |

### Properties

| Property ID | Name | Read Value | Notes |
|-------------|------|------------|-------|
| 0x01 (1) | Mode | 1 or 4 | Operating mode |
| 0x03 (3) | Operating Mode | 1=SELF_OPERATED, 2=HOST_CONTROLLED | **Key for LCD control** |
| 0x0105 (261) | BragiVersion | 0 | Old protocol, no V1.5 support |
| 0xF0 (240) | Startup Animation | varies | Screen startup animation setting |
| 0xF2 (242) | Width | 320 | LCD width (display reports 320, actual canvas is 248) |
| 0xF3 (243) | Height | 170 | LCD height |
| 0xE6 (230) | Screen Present | 1 | Has screen = true |
| 0x40 (64) | Unknown | 62 (0x3E) | Points to file 62? |
| 0x41 (65) | Unknown | 1 | LCD-related toggle |
| 0x107 (263) | Screen Index | — | Screen widget index (no response in old protocol) |

### Files and Resources

| ID | Type | Size | Purpose |
|----|------|------|---------|
| 0x3F (63) | Resource | 84,320 bytes | LCD framebuffer (248x170x2 = RGB565) |
| 0x3E (62) | File | Variable | **Active display file** — controls what LCD shows |
| 0x6D67 (28007) | File | 4 bytes (default) | Default screen resource config |
| 0x01 | Resource | varies | RGB LED lighting data |

### File 28007 (Default Screen Resource)
- Default content: `[56, 0, 0, 0]` (4 bytes)
- Byte 0: Format type — `56` = static image, `102` = GIF
- Bytes 1-3: Resource pointer / flags
- Listed in device config as `defaultScreenResources: [28007]`

### File 62 (Active Display)
- Controls what the LCD shows
- `selectWidget` in Web Hub reads a resource file and writes its content here
- Initially returns error 0x06 on OPEN — requires a stale handle to be closed first
- **Successfully opened and written** after closing stale handles and proper sequencing

## Corsair Custom BMP Format

The Web Hub's `convertToBMP` creates images in this format:

```
Offset  Content
0x00    [0x48, 0x00]                — Corsair magic prefix
0x02    "BM" + standard BMP header  — 54-byte BMP header at offset 2
0x38    Pixel data                  — 24-bit, GRB order (NOT BGR), bottom-up rows
EOF-4   LE32 timestamp              — 4-byte Unix timestamp appended at end
```

Key differences from standard BMP:
- **2-byte prefix** `[0x48, 0x00]` before BMP header
- **GRB pixel order** (Green, Red, Blue) — NOT the standard BGR
- **4-byte timestamp** appended after pixel data
- Rows are 4-byte aligned (standard BMP padding)
- Bottom-up row order (standard BMP orientation)

For 248x170 at 24-bit: `2 (prefix) + 54 (header) + 248*3*170 (pixels, ~126K) + padding + 4 (timestamp) ≈ 126,540 bytes`

## V1.5 Protocol (NOT Supported)

The V1.5 protocol uses 4-byte headers: `[subDevAddr, direction, sessionId, cmdId]`

**Tested and confirmed NOT working on this device:**
- No response on any of the 4 interfaces
- Tested all first-byte values (0x00-0x80)
- Tested both 64-byte and 1024-byte packet sizes
- BragiVersion property (261) returns 0, confirming no V1.5 support

## Web Hub Device Configuration

From Corsair Web Hub JavaScript (`corsair_webhub_B49DVBx0.js`):

```json
{
  "type": "keyboard",
  "vid": "1b1c",
  "pid": "2b0d",
  "displaySupported": true,
  "screenWidth": 248,
  "screenHeight": 170,
  "defaultScreenResources": [28007],
  "configInHostControl": false,
  "supportMultiSessionControl": true,
  "services": ["LightingService", "ControlDialService", "ScreenService", ...]
}
```

## Web Hub LCD Update Flow (from JS analysis)

The Web Hub `changeScreenWidget` function:

```
1. selectWidget(widgetId):
   a. readFile(widgetId)                    — read resource file (e.g., 28007)
   b. writeFile(62, data)                   — write content to active display
   c. updateScreenIndexProperty(profile, widgetId)  — set property 263
2. setOperatingMode(SELF_OPERATED)          — transition back to self-operated
```

`writeFile` internally:
```
1. openFile(fileId, bufferIndex=0)
2. If open fails: unbindBuffers([0]) → createFile(fileId) → openFile(fileId, 0)
3. writeBufferBegin(data, bufferIndex=0)
4. writeBuffer(continuationData, bufferIndex=0)  — repeat until done
5. unbindBuffers([0])                            — always in finally block
```

`readFile` internally:
```
1. openFile(fileId, bufferIndex=0)
2. describeBuffer(0)                         — get total size
3. readBuffer(0) in loop                     — read chunks until size reached
4. unbindBuffers([0])                        — always in finally block
```

## Experimental Results

### What Works
- GET/SET properties via Bragi protocol
- Starting sessions (cmd 0x1B) — returns sessionId=0
- CREATE, OPEN, WRITE, READ files (28007, etc.)
- Writing data to file 62 (active display) — **126KB BMP accepted**
- Mode transitions (SELF_OPERATED ↔ HOST_CONTROLLED)
- Operating mode changes affect LCD status indicators (lock icon, polling rate)

### What Doesn't Work (Yet)
- **LCD main image area doesn't update** — Corsair logo animation persists
- Property 263 (Screen Index) returns no data via GET
- corsair_lcd_tool protocol (opcode 0x02) — designed for AIO cooler LCDs, not keyboards
- V1.5 protocol — device doesn't respond

### Observable LCD Changes from Script Execution
After running `lcd_session_write.py`:
- Lock icon disappeared from LCD
- Polling rate indicator changed to 1K
- Keyboard key backlighting turned on (was off)
- Main Corsair logo/animation unchanged

These changes confirm the firmware processes our writes and mode transitions.

### Approaches Tested

| Approach | Script | Result |
|----------|--------|--------|
| corsair_lcd_tool protocol (opcode 0x02, JPEG) | `lcd_direct_write.py` | No effect — wrong protocol for keyboards |
| Multiple opcode variants | `lcd_debug_write.py` | No effect |
| Bragi OPEN resource 0x3F + write | `lcd_jpeg_test.py` | Write succeeds, no display change |
| Software mode + LCD protocol | `lcd_debug_write.py` | No effect |
| V1.5 protocol on all interfaces | `lcd_v15_write.py` | No response from device |
| Bragi file write (file 28007) | `lcd_bragi_file_write.py` | Write succeeds, no display change |
| Session + file 62 write | `lcd_session_write.py` | **Write succeeds, status icons change** |

## Known Issues and Gotchas

1. **Stale handles**: If a previous script left a buffer/handle open, subsequent OPENs fail with error 0x06. Always close/unbind handles first.
2. **Packet padding**: Interface 2 expects 1024-byte packets. Using 64-byte packets causes silent failures or error 0x06.
3. **SET property 0x3E crashes device**: Writing to property 62 causes `OSError: [Errno 71] Protocol error` and USB disconnect. Avoid.
4. **File 28007 corruption**: Writing large data to file 28007 overwrites the 4-byte config. Must DELETE + CREATE + WRITE to restore.
5. **hidraw number shifts**: After device crash/reconnect, hidraw numbers change. Use uevent-based device discovery.

## Next Steps

1. **Proper image format + file 62 activation sequence**: The write to file 62 succeeded and changed status icons. Need to determine if:
   - File 62 should contain a 4-byte config pointing to a resource, or the full BMP
   - A specific property or mode transition triggers the main image refresh
   - The screen index property (263) needs to be set correctly
2. **Capture actual Web Hub traffic**: Use usbmon/Wireshark while the Web Hub changes the LCD to see the exact packet sequence
3. **Firmware update investigation**: BragiVersion=0 may indicate old firmware without full LCD support
4. **Try HOST_CONTROLLED mode during write, then SELF_OPERATED after** as the refresh trigger

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `bragi_probe.py` | General Bragi protocol exploration — property reads, resource enumeration |
| `lcd_write_test.py` | Early LCD write test via Bragi OPEN/WRITE |
| `lcd_read_full.py` | Read full contents of LCD resource 0x3F |
| `lcd_sw_mode_test.py` | Test software mode switching |
| `lcd_jpeg_test.py` | JPEG image write via Bragi file operations |
| `lcd_direct_write.py` | corsair_lcd_tool protocol test (opcode 0x02) |
| `lcd_debug_write.py` | Multi-approach debug test (5 methods) |
| `lcd_v15_write.py` | V1.5 protocol test |
| `lcd_bragi_file_write.py` | Bragi file-based write with Web Hub file IDs |
| `lcd_session_write.py` | **Latest** — session-based write with file 62 |
