# Corsair Vanguard 96 LCD Protocol Findings

## Device Info
- **Product**: Corsair CORSAIR VANGUARD 96 Mechanical Gaming Keyboard
- **USB ID**: `1B1C:2B0D`
- **Display**: 248x170 IPS LCD (color), reports 320x170 via properties
- **Firmware**: v1.18.42 (current), v2.8.59 available from Corsair CDN
- **MCU**: STM32U5A9 (ARM Cortex-M33, 160MHz, 4MB flash, 2.5MB SRAM)
- **UI Framework**: ST TouchGFX with DMA2D (Chrom-ART) hardware acceleration
- **RTOS**: Azure RTOS (ThreadX + USBX HID class)
- **LCD Controller**: ILI-series, SPI-driven (no LTDC parallel RGB)
- **Build**: Properties 19/20 suggest build date/time encoding

## USB Topology

4 HID interfaces:

| Interface | hidraw | Report Size | Usage Page | Usage | Purpose |
|-----------|--------|-------------|------------|-------|---------|
| IF#0 (input0) | hidraw0 | varies | 0x0001 | 0x02 | Mouse/multimedia control |
| IF#1 (input1) | hidraw1 | varies | 0x0001 | 0x06 | Keyboard HID |
| IF#2 (input2) | hidraw2 | **1024 bytes** | **0xFF42** | **0x01** | **Bragi command endpoint** |
| IF#3 (input3) | hidraw3 | **64 bytes** | **0xFF42** | **0x02** | **Notification endpoint** (silent) |

**Note**: hidraw numbers shift after device crash/reset. Use uevent matching (`VANGUARD` + `input2`) for command endpoint.

### Notification Endpoint (IF#3)
- Tested extensively: **zero notifications received** during all operations
- Monitored during: mode switches, file writes, property changes, cookie updates
- Writing to this endpoint succeeds but produces no response
- Web Hub JS expects notifications here (PropertyValueChange, KeyPress, etc.) but device doesn't send any

## Bragi Protocol (Old Format, 2-byte Header)

This device uses the "old" Bragi protocol (VER1_0). `BragiVersion` property (96) returns 0.

### Packet Format
```
Outgoing: [0x00 (HID report ID)] + [0x08, cmdId, ...payload] padded to 1024 bytes
Response: [0x00, cmdId, status, ...data] (1024 bytes from device)
```

- `0x08` = `Device_Itself` constant
- `status` byte: `0x00` = success, various error codes

### Status Codes
| Code | Meaning |
|------|---------|
| 0x00 | Success |
| 0x01 | Invalid Argument Value |
| 0x02 | Insufficient Buffer Size |
| 0x03 | Invalid State |
| 0x04 | Command Not Supported |
| 0x05 | Property Not Supported |
| 0x06 | Invalid Device Address |
| 0x07 | Hardware Error |
| 0x09 | Invalid Operation |

### Complete Command IDs

| ID | Hex | Name | Payload | Notes |
|----|-----|------|---------|-------|
| 1 | 0x01 | SET_PROPERTY | `[propId_LE16, value_bytes...]` | Set property |
| 2 | 0x02 | GET_PROPERTY | `[propId_LE16]` | Get property, value at resp[3:] |
| 3 | 0x03 | GET_MULTIPLE | `[propIds...]` | Batch property get |
| 5 | 0x05 | UNBIND | `[handle, 0x00]` | Close handle/unbind buffer |
| 6 | 0x06 | WRITE_BEGIN | `[bufferIdx, totalLen_LE32, data...]` | First write chunk |
| 7 | 0x07 | WRITE_CONT | `[bufferIdx, data...]` | Continuation chunks |
| 8 | 0x08 | READ | `[bufferIdx]` | Read from buffer, data at resp[3:] |
| 9 | 0x09 | DESCRIBE | `[bufferIdx]` | Get size: resp[5:9] = actual size |
| 10 | 0x0A | CALIBRATION | `[mode, type]` | Magnetic switch calibration only |
| 11 | 0x0B | CREATE_FILE | `[fileId_LE16]` | Create file |
| 12 | 0x0C | DELETE_FILE | `[fileId_LE16]` | Delete file |
| 13 | 0x0D | OPEN_FILE | `[bufferIdx, fileId_LE16]` | Open file into buffer |
| 15 | 0x0F | RESET_FACTORY | `[mode]` | Modes 1-9 all return OK |
| 18 | 0x12 | PING | `[0x01, 0x00]` | Pairing/ping |
| 27 | 0x1B | SESSION | `[0x01, token[4], 0x00]` | Start host session |

**Note**: Command 0x0E (14) does not exist. No commands between OPEN(0x0D) and RESET(0x0F).

### V1.5 Protocol (NOT Supported)
- 4-byte header: `[subDevAddr, direction(1=req), sessionId, cmdId]`
- **All V1.5 commands return None** — device does not respond
- Confirmed: this device only speaks V1.0 (2-byte header)

## Properties (Complete Scan 0-300)

54 responsive properties found. Key ones:

| Property | Name | Value | Raw | Notes |
|----------|------|-------|-----|-------|
| 1 | Device Status | 4 | `04 00 00 00` | |
| 2 | Polling Rate | 1000 | `e8 03 00 00` | 1000ms default |
| 3 | **Operating Mode** | 1 | `01 00 00 00` | **1=SELF, 2=HOST, 3=BOOTLOADER** |
| 4 | Connection Type | 0 | `00 00 00 00` | |
| 9 | Battery/Charging | 1 | `01 00 00 00` | |
| 10 | Battery Level | 5 | `05 00 00 00` | |
| 17 | Vendor ID | 0x1B1C | `1c 1b 00 00` | Corsair |
| 18 | Product ID | 0x2B0D | `0d 2b 00 00` | Vanguard 96 |
| 19 | FW Build Date | | `01 12 2a 00` | Version/date encoding |
| 20 | FW Build Time | | `01 06 0c 00` | Version/time encoding |
| 56 | Hardware Layout | 5 | `05 00 00 00` | |
| 57 | Connection Count | 1 | `01 00 00 00` | |
| 61 | Storage Size | 0x02000000 | `00 00 00 02` | 33MB? |
| 62 | Max File Size | 0x01E580 | `00 80 e5 01` | ~124KB |
| 64 | | 0xCC | `cc 00 00 00` | 204 |
| 65 | | 1 | `01 00 00 00` | Read-only (SET returns 0x05) |
| 96 | **Bragi Version** | **0** | `00 00 00 00` | **Oldest protocol version** |
| 150 | Device Features | 7 | `07 00 00 00` | |
| 226 | LED Color 1 | 0x00FF00 | `00 00 ff 00` | Settable |
| 228 | LED Color 2 | 0x00FF00 | `00 00 ff 00` | Settable |
| 234 | Animation Timing | 1000 | `e8 03 00 00` | Settable, 1000ms |
| 242 | **Screen Width** | **320** | `40 01 00 00` | Full panel width |
| 243 | **Screen Height** | **170** | `aa 00 00 00` | |
| 260 | | 7 | `07 00 00 00` | Settable |
| 261 | | 0 | `00 00 00 00` | Read-only |

Properties that accept SET (status 0): 225, 226, 227, 228, 234, 235, 238, 239, 251-260
Properties that reject SET (status 5): 65, 230, 261, 265

## Hardware Resources

Resources opened via `[0x08, 0x0D, handle, resId, 0x00, 0x00]`:

| Resource ID | Size | Contents | Purpose |
|-------------|------|----------|---------|
| 0x02 | 138 bytes | 0x39/0x3B values (ASCII '9'/';') | Key calibration data |
| 0x0F | 6 bytes | `13 00 01 00 60 6d` | **Profile pointer**: version=19, index=1, profileFile=28000 |
| 0x11 | 70 bytes | "Siil" magic + file ID list | **Master File Allocation Table** |
| 0x22 | 70 bytes | `FF FF FF FF FF FF 00...` | Empty allocation bitmap |
| 0x2E | 70 bytes | `FF FF FF FF FF FF 00...` | Empty allocation bitmap |
| 0x3F | 84,320 bytes | Framebuffer data | **LCD framebuffer** (248×170×2 RGB565) |

Resources 0x00, 0x01 can't be OPEN'd but ARE referenced by config (built-in ROM scenes).
Resources 0x40-0x7F all fail OPEN with status 6.

### Resource 0x0F (Profile Pointer)
```
Offset 0: 0x0013 (19)    — version or count
Offset 2: 0x0001 (1)     — active profile index
Offset 4: 0x6D60 (28000) — profile file ID
```

### Resource 0x11 (Master File Table)
```
Offset 0-1: 0xAB5B       — checksum/magic
Offset 2-3: 0x0021 (33)  — total file count
Offset 4-7: "Siil"       — filesystem magic
Offset 8+:  uint16 LE file IDs:
  15, 28000-28007, 62, 10, 15, 61, 65, 76, 77,
  32000, 28100-28106, 28200-28203, 28300
```

### Resource 0x3F (LCD Framebuffer)
- Size: 84,320 bytes = 248 × 170 × 2 (RGB565 LE)
- **Read-only in practice**: firmware animation overwrites instantly
- Writes succeed but data doesn't persist (animation loop overwrites)
- Even zeroing all settable properties doesn't stop the animation
- OPEN parameters: 4th byte must be 0x00, 5th byte is ignored

### Display Scene Resources (Built-in, ROM-based)
| Resource ID | Display Output |
|-------------|---------------|
| 0x00 | Corsair logo animation (default) |
| 0x01 | Blue/purple nebula Corsair logo |
| 0x02 | Distorted colorful pattern |
| 0x3F | Corsair logo animation (same as 0x00) |

## File System Architecture

### Key Files

| File ID | Size | Purpose |
|---------|------|---------|
| 15 | varies | Profiles list |
| 61 | varies | Screen resource map |
| **62** | 4-16 bytes | **Active display config** — determines what LCD shows |
| 28000 | 70 bytes | Active profile (name, cookie, file references) |
| 28001 | 94 bytes | Properties file (key-value pairs) |
| 28002-28005 | varies | Additional profile data |
| 28006 | 6 bytes | Screen modes layout |
| **28007** | **4 bytes** | **Default screen config: `38 00 00 00`** |
| 28100-28106 | varies | Additional device files |
| 28200-28203 | varies | Image/resource files |
| 28203 | 163,260 bytes | Factory BMP image (320×170, 24-bit, Corsair format) |
| 28300 | varies | User image files |

### File 62 (Active Display Config)
Controls what the LCD displays. Format:
```
Byte 0: type (0x38 = static image, 0x66 = GIF, 0x42 = battery widget)
Byte 1: 0x00
Bytes 2-3: resourceId (uint16 LE) — HARDWARE RESOURCE ID, NOT file ID
Bytes 4-15: padding/reserved (optional, can be 4 or 16 bytes total)
```

**CRITICAL**: On this firmware (Bragi v0), `resourceId` must be a hardware resource ID (0-127).
File system IDs (28xxx range) produce noise/static (uninitialized VRAM).

### File 28007 (Default Config)
Factory default: `38 00 00 00` — points to resource 0 (Corsair logo animation)

### File 61 (Screen Resource Map)
Format:
```
Bytes 0-1: header (uint16 LE) — factory default is 0x0000, Web Hub writes 0x0044
Bytes 2-3: count (uint16 LE) — number of resource map entries
Per entry (8 bytes):
  Bytes 0-1: resourceId (uint16 LE)
  Bytes 2-3: resourceAddress (uint16 LE) — file ID containing image data
  Bytes 4-7: hash (4 bytes)
```

### File 28000 (Profile)
```
Bytes 0-1: header (format marker)
Bytes 2-3: profile internal ID (uint16 LE)
Bytes 4-7: cookie (uint32 LE) — Unix timestamp, used as change signal
Bytes 8+: file references (28001, 28002, etc.), screen modes layout pointer
Bytes 30+: profile name (ASCII, null-terminated) — "VANGUARD 96 Default Profile 1"
```

### File 28001 (Properties)
```
Bytes 0-1: header
Bytes 2-3: count (uint16 LE) — number of property entries
Per entry (6 bytes):
  Bytes 0-1: propertyId (uint16 LE)
  Bytes 2-5: value (4 bytes)
```

### File 28006 (Screen Modes Layout)
```
Bytes 0-1: header (0x37, 0x00)
Bytes 2-3: row count (uint16 LE) — usually 1
Per row: count (byte) + fileIds (uint16 LE each)
```
Default: `37 00 01 01 67 6d` = header + 1 row + count 1 + fileId 28007

## Corsair Custom BMP Format

The Web Hub's `convertToBMP` creates images in this format:

```
Offset  Content
0x00    [0x48, 0x00]                — Corsair magic prefix (2 bytes)
0x02    "BM" + standard BMP header  — 54-byte BMP header
0x38    Pixel data                  — 24-bit, GRB order (NOT BGR), bottom-up rows
EOF-4   LE32 timestamp              — 4-byte Unix timestamp appended at end
```

Key differences from standard BMP:
- **2-byte prefix** `[0x48, 0x00]` before BMP header
- **GRB pixel order** (Green, Red, Blue) — NOT the standard BGR
- **4-byte timestamp** appended after pixel data
- Rows are 4-byte aligned (standard BMP padding)
- Bottom-up row order (standard BMP orientation)

Factory BMP (file 28203): 320×170 pixels, 24-bit, 163,260 bytes total.

## Web Hub LCD Update Flow (from JS Analysis)

### Complete Protocol (from `updateImageScreenAsync`)
```
1.  setProperty(3, HOST_CONTROLLED=2)     — switch to host mode
2.  generateNumberFileID()                 — random file ID for image resource
3.  updateScreenResourceMap([id], ADD)     — register in file 61
4.  generateNumberFileID()                 — random file ID for layout config
5.  createScreenConfig({header:[56,0], resourceId:imageFileId})
6.  writeFile(layoutFileId, configBytes)   — write layout config
7.  updateScreenModeProfile(profileId, layoutId, ADD)  — add to file 28006
8.  updateScreenIndexProperty(profileId, layoutId)     — set property 263 in file 28001
9.  writeFile(imageFileId, bmpData)        — write Corsair BMP to resource file
10. updateCookie()                         — write new timestamp to file 28000
11. setProperty(3, SELF_OPERATED=1)        — switch back, triggers display reload
```

### selectWidget(layoutId) — Display Switch
```
1. readFile(layoutId)                — read config from layout file
2. writeFile(62, configData)         — write to active display file
3. updateScreenIndexProperty(...)    — set property 263
4. setProperty(3, SELF_OPERATED=1)   — in finally block
```

## CRITICAL FINDING: File-Based Rendering Not Supported

**This firmware (Bragi v0) cannot render images from file system IDs.**

### Evidence
Exhaustive testing across 8+ scripts with systematic variation:

| Config resourceId | Resource Map | Cookie | Result |
|-------------------|-------------|--------|--------|
| 0x00 (resource) | any | any | **Corsair logo animation** |
| 0x01 (resource) | any | any | **Nebula Corsair image** |
| 0x3F (resource) | any | any | **Corsair logo animation** |
| 28203 (factory BMP) | correct header (0x44) | updated | **NOISE (static)** |
| 28200 (our BMP) | correct header | updated | **NOISE** |
| 28300 (test BMP) | correct header | updated | **NOISE** |
| 60000 (nonexistent) | N/A | N/A | **NOISE** |
| Any file ID | Any format | Any | **NOISE** |

### Why
- Config's `resourceId` field is treated as a hardware resource selector (0-127)
- File IDs (28xxx range) are out of hardware resource range
- Firmware attempts to read from hardware resource address, gets uninitialized VRAM → noise
- The animation loop is hardcoded and cannot be stopped via any discovered property or command
- Resource 0x3F framebuffer writes succeed but animation instantly overwrites

### Implication
The Web Hub's file-based image upload flow was designed for **newer firmware** that supports mapping file data to display resources. This Bragi v0 firmware only supports built-in ROM-based display scenes selected by hardware resource ID.

## Approaches Tested (Comprehensive)

| Approach | Script | Result |
|----------|--------|--------|
| V1.0 file write to 28007 | `lcd_bragi_file_write.py` | Write OK, no display change |
| Session + file 62 write | `lcd_session_write.py` | Status icons changed, main area unchanged |
| Full WebHub flow (8-step) | `lcd_full_flow.py` | NOISE |
| Cookie fix (correct file 28000) | `lcd_cookie_test.py` | NOISE (4 variations) |
| Profile path only | `lcd_profile_path.py` | Corsair logo (fallback) |
| Factory BMP (28203) via config | `lcd_factory_test.py` | NOISE — even known-good factory BMP |
| Resource 0x00/0x01/0x3F via config | `lcd_factory_test.py` | **WORKS** — built-in scenes display |
| Resource scan (0-127) | `lcd_resource_scan.py` | 6 openable, only 0x3F has framebuffer |
| Write RGB565 to 0x3F (SELF mode) | `lcd_framebuffer_race.py` | Animation overwrites instantly |
| Write to 0x3F (HOST mode, stay) | `lcd_framebuffer_race.py` | Animation overwrites |
| Write to 0x3F + instant SELF switch | `lcd_framebuffer_race.py` | Animation overwrites |
| Rapid-fire writes to 0x3F (10x) | `lcd_framebuffer_race.py` | Animation overwrites |
| Write to resources 0x40-0x7F | `lcd_framebuffer_race.py` | All OPEN fail (status 6) |
| Control register modification (0x0F, 0x11) | `lcd_control_regs.py` | Write fails (read-only resources) |
| Zero all settable properties | `lcd_control_regs.py` | Animation continues |
| V1.5 protocol (4-byte headers) | `lcd_v15_protocol.py` | No response — not supported |
| Notification endpoint monitoring | `lcd_notification_monitor.py` | Zero notifications |
| Correct resource map header (0x44) | `lcd_correct_map_header.py` | NOISE — header doesn't matter |
| Full flow + correct header + factory BMP | `lcd_correct_map_header.py` | NOISE |
| Copy 28007 to file 62 | `lcd_correct_map_header.py` | **WORKS** — restores default display |

## USB Behavior

Mode transitions (SET property 3) trigger full USB disconnect/re-enumeration:
1. USB disconnect event
2. New device number assigned
3. All 4 interfaces re-probed
4. New hidraw numbers assigned
5. Previous file descriptors become invalid

Scripts must handle re-enumeration with reconnect logic (~3-5 second wait).

## Known Issues and Gotchas

1. **Stale handles**: Previous scripts leaving handles open → OPEN fails with 0x06. Always close all handles first.
2. **1024-byte packets required**: Interface 2 expects 1024-byte HID reports. Smaller packets cause failures.
3. **SET property 0x3E crashes device**: Writing to property 62 causes protocol error and USB disconnect.
4. **DESCRIBE size ambiguity**: resp[5:9] = actual data size, resp[4:8] = allocated/sector size. Use offset 5 first with sanity check.
5. **Resource 0x0F and 0x11 are read-only**: Writes fail despite successful OPEN.
6. **Factory reset modes (0x0F cmd)**: All 9 modes return success but no visible effect. Use with caution.

## Firmware Analysis (v2.8.59)

### Acquisition
- **CDN**: `https://www.corsair.com/firmware-storage/firmware/public/`
- **Mapping**: `/mapping.json` → device manifest → firmware zip
- **Manifest**: `manifests/vanguard-96_1b1c_2b0d_manifest.json`
- **Firmware**: `fw/VANGUARD96_2.8.59.zip` (1.38MB)
- **Binary**: `VANGUARD96_App_v2.8.59.bin` (1,819,552 bytes)
- **Integrity**: SHA-512, CRC32-MPEG2 validation for flashing
- **Flash method**: Bragi `apply-extended` command in bootloader mode

### MCU Identification: STM32U5A9

Evidence:
| Factor | Value | Significance |
|--------|-------|-------------|
| Initial SP | `0x200D9CF8` | 871KB SRAM used (STM32U5 has 2.5MB) |
| Flash base | `0x08000000` | Standard STM32 |
| GPIO addresses | `0x4202xxxx` | STM32U5-specific bus mapping |
| DMA2D | 7 refs at `0x4C000000` | Chrom-ART 2D graphics accelerator |
| OCTOSPI1 | `0x44021000` | External flash for resources |
| Vector table | 132 IRQs max | Matches STM32U5A9 |
| USB stack | `ux_slave_class_hid` | Azure RTOS USBX |
| Source path | `../Core/Src/cs_api_LightingSystem/Lightings.c` | Corsair SDK |
| Filesystem | `Simple File Sys` + `Siil` magic | Custom FS on OCTOSPI flash |

### Active Peripherals (from IRQ vector table)
- **RTC** (IRQ 2) — real-time clock
- **TIM1** (IRQ 29-30) — timer capture/compare
- **TIM2** (IRQ 31) — general timer
- **EXTI5-7** (IRQ 46-48) — external interrupts (buttons/switches)
- **SDMMC1** (IRQ 52) — possibly NAND storage
- **GPDMA1_CH2** (IRQ 59) — DMA for SPI/LCD transfers
- **OCTOSPI1** (IRQ 73) — external flash
- **SPI3/LPTIM4** (IRQ 76) — LCD SPI interface
- **TIM15** (IRQ 85) — animation timing
- **DMA2D** (IRQ 118) — hardware 2D blitter

### Firmware Memory Layout
```
0x000000 - 0x062100  Code + rodata (392KB)
0x062100 - 0x06261C  TouchGFX bitmap table (66 entries × 20 bytes)
0x06261C - 0x0B1B30  Font data, key maps, config tables (319KB)
0x0B1B30 - 0x1BC3A0  Compressed bitmap data (1,066KB)
```

### Embedded Animation Frames
- **60 frames** at 248×170, L8 compressed with per-frame CLUT
- **3 icon bitmaps**: 1× 170×120, 2× 28×28
- **8 additional** 28×28 icon entries after main table
- Each frame: ~17.5KB data + 692-byte CLUT (palette)
- Compression ratio: ~42% of raw L8 (21% of raw RGB565)
- Total bitmap data: ~1MB (58% of firmware binary)
- Compression: TouchGFX proprietary L8 with RGB565 CLUT

### TouchGFX Bitmap Table Structure
```
Offset from 0x06212C, 20 bytes per entry:
  [data_ptr:u32]    — flash address of compressed pixel data
  [extra_ptr:u32]   — flash address of CLUT (Color Look-Up Table)
  [width:u16]       — pixel width
  [height:u16]      — pixel height
  [solid_rect:u32]  — solid rectangle info (x,y pairs)
  [type_info:u32]   — format/flags (0x60aa20f8 for 248×170 L8)
```

### Why Animation Can't Be Stopped
TouchGFX runs its own render loop with hardware DMA2D acceleration. The animation frames are read directly from internal flash by the Chrom-ART DMA engine, bypassing any Bragi file system interaction. This is a hardware-accelerated render pipeline that the Bragi protocol has no control over.

### Compatible Dev Boards (for firmware emulation)
1. **STM32U5A9J-DK** — exact chip match, has LCD + DMA2D + OCTOSPI + USB (~$80-100)
2. **NUCLEO-U5A5ZJ-Q** — same peripheral set, no built-in LCD (~$30-40)
3. **B-U585I-IOT02A** — lower-end U5, has OCTOSPI + USB but no DMA2D (~$55)

### Key Strings Found in Binary
```
"CORSAIR VANGUARD 96 Mechanical Gaming Keyboard"
"VANGUARD 96 Default Profile 1"
"Simple File Sys "
"Firmware version: S9"
"Bootloader"
"../Core/Src/cs_api_LightingSystem/Lightings.c"
"../TouchGFX/target/generated/STM32DMA.cpp"
"ux_slave_class_hid"
```

## Possible Next Steps

1. **Flash firmware v2.8.59**: May enable file-based LCD rendering (needs explicit user approval)
2. **Decompress animation frames**: Reverse the TouchGFX L8 compression to extract frame images
3. **Run firmware on STM32U5A9J-DK**: Dev board emulation for safe experimentation
4. **Ghidra/radare2 analysis**: Full disassembly to find Bragi command dispatch and display render code
5. **USB traffic capture**: Use usbmon/Wireshark with iCUE on Windows to capture working LCD update
6. **SPI flash analysis**: Direct hardware access to the external OCTOSPI flash

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `bragi_probe.py` | General Bragi protocol exploration |
| `lcd_full_flow.py` | Complete WebHub 8-step update flow |
| `lcd_cookie_test.py` | Cookie mechanism verification (4 approaches) |
| `lcd_read_factory.py` | Read factory files with hex dump analysis |
| `lcd_verify_write.py` | Write/read round-trip integrity verification |
| `lcd_profile_path.py` | Profile-based vs file 62 display path test |
| `lcd_factory_test.py` | Systematic config variation (resources vs files) |
| `lcd_resource_scan.py` | Hardware resource enumeration (0-127) |
| `lcd_framebuffer_race.py` | Direct framebuffer write timing attacks |
| `lcd_control_regs.py` | Small resource analysis + property brute-force |
| `lcd_v15_protocol.py` | V1.5 protocol test + firmware version probe |
| `lcd_notification_monitor.py` | Second HID endpoint notification monitoring |
| `lcd_correct_map_header.py` | Resource map header correction test |
| `webhid_test.html` | WebHID test page for Chrome browser |

## Firmware Files

| File | Purpose |
|------|---------|
| `firmware/VANGUARD96_App_v2.8.59.bin` | Firmware binary (1.8MB ARM Cortex-M33) |
| `firmware/VANGUARD96_App_v2.8.59.json` | Firmware metadata (version, integrity hash) |
| `VANGUARD96_2.8.59.zip` | Original firmware download from Corsair CDN |
| `firmware/frames/frame_XX_data.bin` | Extracted compressed animation frame data |
| `firmware/frames/frame_XX_extra.bin` | Extracted CLUT (palette) data per frame |

## Photo Documentation

All test results photographed in `pics/` subdirectories:
- `pics/10-full-flow/` through `pics/20-correct-header/`
- Binary file dumps in `factory_dumps/` and `resource_dumps/`
