# Keyboard OLED/LCD Reverse Engineering

Reverse engineering the display protocols for keyboard OLED/LCD screens to enable native Linux control without vendor software.

## Target Devices

### ASUS ROG Azoth X (OLED)
- **USB IDs**: Wired `0B05:1C24`, Bluetooth `0B05:1C27`, OMNI Receiver `0B05:1ACE`
- **Display**: 128x40 OLED
- **Status**: OLED mode switching working, display modes discovered via blind probing
- **Protocol**: HID vendor commands on interface 1 (wired) / interface 2 (receiver), 64-byte packets

### SteelSeries Apex Pro Gen 3 (OLED)
- **USB ID**: `1038:1622`
- **Display**: 128x40 OLED (SSD1309-like)
- **Status**: Full OLED write working - arbitrary text and images
- **Protocol**: HID feature reports, 1-bit monochrome framebuffer (640 bytes), 128x40 pixels

### Corsair Vanguard 96 (LCD)
- **USB ID**: `1B1C:2B0D`
- **Display**: Color LCD (likely 480x176 RGB565)
- **Status**: Bragi protocol partially decoded, LCD resource 0x3F found (84,320 bytes), writes don't update display yet. iCUE installer running in Windows VM — USB capture pending.
- **Protocol**: Corsair Bragi protocol (handle-based resource system via HID)
- **Next step**: Complete iCUE install, capture USB traffic to discover LCD write sequence

## Project Structure

```
keyboard-oled-re/
├── README.md                        # This file
├── findings.md                      # ROG Azoth X protocol findings (detailed)
├── azoth_oled.py                    # ROG Azoth X OLED control tool
├── azoth-x-capture-handoff.md       # Windows USB capture guide for Armoury Crate
├── qemu_type.sh                     # Helper: type text into QEMU VM via monitor sendkey
├── pics/                            # Photo evidence organized by RE phase
│   ├── 01-azoth-oled-probing/       # Initial HID probing & mode discovery
│   ├── 02-azoth-oled-modes/         # Mode demos & framing experiments
│   ├── 03-steelseries-oled/         # SteelSeries Apex Pro Gen 3 discovery
│   ├── 04-steelseries-writes/       # OLED writes, animations, call sign
│   ├── 05-corsair-lcd-probing/      # Corsair Vanguard 96 LCD probing
│   ├── 06-corsair-boot-sequence/    # Corsair keyboard boot animation capture
│   ├── 07-corsair-post-boot/        # Post-boot LCD state
│   └── 08-win11-vm-setup/           # Windows 11 VM install & iCUE setup (VNC captures)
├── ckb-next/                        # Corsair ckb-next source (gitignored)
├── vm/                              # Windows 11 VM (gitignored, see VM Setup)
└── venv/                            # Python virtualenv (gitignored)
```

## Key Findings

### ROG Azoth X OLED Modes (0x6x command family)
| Command | Mode | Notes |
|---------|------|-------|
| `0x61` | Animation | Default ROG globe animation |
| `0x63` | Dashboard | Date/time/stats (needs host data feed) |
| `0x64` | System Monitor | CPU/GPU/temp (needs host data feed) |
| `0x65` | Mail/Notifications | Shows envelope icon |
| `0x66` | CPU Usage | Shows "CPU Usage 0%" without host data |
| `0x68` | Unknown | Blank - possibly custom image slot |
| `0x69` | **OLED OFF** | **DANGEROUS** - requires MCU reset to recover |

### SteelSeries Apex Pro Gen 3 OLED Write Protocol
1. Open HID feature report interface
2. Send 640-byte framebuffer (128x40, 1-bit packed, MSB-first)
3. Prefix with report ID and command bytes
4. Display updates immediately - call sign "VIOLATOR ACTUAL" successfully rendered

### Corsair Vanguard 96 Bragi Protocol
- Uses handle-based resource system (open handle, write data, close handle)
- `BRAGI_MAGIC = 0x08`, commands: SET(0x01), GET(0x02), OPEN_HANDLE(0x0d), WRITE_DATA(0x06)
- Resource `0x3F` = LCD framebuffer (84,320 bytes)
- Resource `0x01` = RGB LED lighting
- Writing to 0x3F doesn't update display - likely missing an init/commit sequence
- Need to capture iCUE traffic to find the missing steps

## VM Setup (for Corsair USB traffic capture)

The `vm/` directory is gitignored (contains an 80GB QCOW2 disk image and transient state). Here's how to recreate it from scratch.

### Prerequisites

```bash
sudo apt install qemu-system-x86 ovmf swtpm socat
pip install vncdotool   # or install in the project venv
```

You also need a Windows 11 ISO (e.g. `Win11_25H2_English_x64.iso`).

### Create the VM

```bash
mkdir -p vm/tpm

# Create 80GB disk image
qemu-img create -f qcow2 vm/win11.qcow2 80G

# Copy writable UEFI variable store (Secure Boot / MS keys variant)
cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd vm/OVMF_VARS.fd
```

### Install Windows 11

```bash
# Start TPM 2.0 emulator (required for Win11)
swtpm socket --tpmstate dir=vm/tpm --tpm2 \
  --ctrl type=unixio,path=vm/tpm/swtpm-sock &

# Launch VM with install ISO
qemu-system-x86_64 \
  -name "Win11-iCUE" -enable-kvm -machine q35,accel=kvm \
  -cpu host -smp 8 -m 8G \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.ms.fd \
  -drive if=pflash,format=raw,file=vm/OVMF_VARS.fd \
  -chardev socket,id=chrtpm,path=vm/tpm/swtpm-sock \
  -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
  -drive file=vm/win11.qcow2,format=qcow2,if=none,id=disk0 \
  -device ahci,id=ahci -device ide-hd,drive=disk0,bus=ahci.0 \
  -cdrom /path/to/Win11_25H2_English_x64.iso \
  -boot order=d,menu=on,splash-time=15000 \
  -device usb-ehci,id=usb -device usb-tablet,id=tablet0 \
  -device usb-host,vendorid=0x1b1c,productid=0x2b0d \
  -display gtk -vnc :0 \
  -device e1000,netdev=net0 \
  -netdev user,id=net0 \
  -global driver=cfi.pflash01,property=secure,value=on \
  -audio driver=none \
  -monitor unix:vm/qemu-monitor.sock,server,nowait
```

**Key notes:**
- Uses AHCI/SATA disk (not VirtIO) so Windows sees the drive without extra drivers
- Uses **e1000 NIC** (not virtio-net-pci) — Windows has built-in e1000 drivers, no extras needed
- `-vnc :0` enables VNC on port 5900 for programmatic control
- `-device usb-host,vendorid=0x1b1c,productid=0x2b0d` passes the Corsair keyboard through
- `-boot splash-time=15000` gives time to hit a key to boot from CD
- TPM 2.0 + Secure Boot (OVMF ms variant) satisfies Windows 11 requirements
- VM gets IP `10.0.2.15` via QEMU user-mode NAT (gateway `10.0.2.2`)

### Boot from CD (first install only)

The "Press any key to boot from CD" prompt times out fast. Spam keys via the monitor socket right after launch:

```bash
# In a separate terminal, immediately after starting QEMU:
for i in $(seq 1 30); do
  echo "sendkey ret" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
  sleep 0.3
done
```

### Windows 11 OOBE Notes

- **Offline account bypass**: Windows 11 OOBE requires internet sign-in by default. At the "Let's connect you to a network" screen, press `Shift+F10` to open a command prompt, type `oobe\bypassnro`, and the VM will reboot. After reboot, an "I don't have internet" option appears.
- **Local account**: Created as `ViolatorActual` with no password.
- The OOBE Next buttons at 1280x800 resolution are at approximately y=680.

### Interact via VNC

VNC bypasses QEMU's GTK mouse capture requirement, making it reliable for scripted control:

```bash
VNC="venv/bin/vncdo -s localhost:0"
$VNC move 500 400 click 1   # click at pixel coordinates
$VNC capture screenshot.png  # capture screen
$VNC key enter               # send keypress
```

**Known issue**: `vncdo type` sends `;` instead of `:` (colon). Use the QEMU monitor `sendkey` command for text containing colons (see `qemu_type.sh`).

**Screenshot helper** (auto-incrementing filenames):
```bash
V="venv/bin/vncdo -s localhost:0"
N(){ SNAP="vnc_$(printf '%03d' $(ls vnc_*.png 2>/dev/null | wc -l)).png"; $V capture "$SNAP"; echo "$SNAP"; }
```

### Interact via QEMU Monitor

```bash
# Send a command to the QEMU monitor
echo "sendkey ret" | socat - UNIX-CONNECT:vm/qemu-monitor.sock

# Take a screenshot (PPM format, full resolution)
echo "screendump vm/snap.ppm" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
python3 -c "from PIL import Image; Image.open('vm/snap.ppm').save('vm/snap.png')"

# Accept UAC prompts (VNC mouse clicks don't work on secure desktop)
echo "sendkey alt-y" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
```

### qemu_type.sh — Type Text via Monitor

`vncdo type` has a colon mapping bug (sends `;` instead of `:`). The `qemu_type.sh` helper types arbitrary text via QEMU monitor `sendkey` commands, correctly handling colons, backslashes, quotes, and all special characters:

```bash
# Type a URL into the VM
./qemu_type.sh 'https://example.com/path'
# Then press Enter
echo "sendkey ret" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
```

### Run the VM (after install)

Same as the install command but remove the `-cdrom` line and change boot to `-boot order=c`.

### Download iCUE in the VM

From a PowerShell prompt inside the VM (typed via `qemu_type.sh`):

```powershell
Invoke-WebRequest -Uri "https://www3.corsair.com/software/CUE_V5/public/modules/windows/installer/Install iCUE.exe" -OutFile "$env:USERPROFILE\Desktop\iCUE_setup.exe"
Start-Process "$env:USERPROFILE\Desktop\iCUE_setup.exe"
# UAC prompt: use Alt+Y via QEMU monitor (VNC can't click secure desktop)
```

## Tools & Dependencies

- **Python 3.12** + virtualenv (`hidapi`, `Pillow`)
- **QEMU/KVM 8.2.2** with OVMF (UEFI) and swtpm (TPM 2.0)
- **socat** for QEMU monitor socket communication
- **vncdotool** for VNC-based VM control (mouse clicks, screenshots)
- **qemu_type.sh** (custom) for typing text into VM via QEMU monitor sendkey — works around vncdo colon bug
- **ckb-next** source as Corsair Bragi protocol reference

## Lessons Learned

### VNC Automation Pitfalls
- `vncdo type` maps `:` to `;` — use QEMU monitor `sendkey shift-semicolon` instead (wrapped in `qemu_type.sh`)
- `vncdo type` also can't send `"` (double quotes) — use `sendkey shift-apostrophe`
- UAC secure desktop prompts don't respond to VNC mouse clicks — use `sendkey alt-y` via QEMU monitor
- VNC coordinate latency is ~250ms per `vncdo` invocation — no need for long sleeps between actions
- Screenshot early and often for evidence logging

### Windows 11 VM Tips
- Use **e1000** NIC, not virtio-net-pci (no built-in Windows driver for virtio)
- Use **AHCI/SATA** disk, not VirtIO (same reason)
- `oobe\bypassnro` via Shift+F10 cmd prompt skips mandatory Microsoft account sign-in
- Edge first-run sign-in modal blocks everything — use `Win+R` -> `cmd` to get a working shell
- PowerShell quoting in cmd.exe is fragile — launch `powershell` interactively instead of `powershell -command '...'`
- `curl.exe` in Windows cmd doesn't support single quotes — use double quotes or PowerShell `Invoke-WebRequest`

## Safety Notes

- **ROG Azoth X**: Avoid `0x69` (OLED shutdown) and `0xFC-0xFF` (USB controller crash). Recovery requires physical mode switch.
- **Corsair**: Bragi protocol writes appear safe; resource handles auto-close on timeout.
- Always photograph/screenshot display state before and after probing for evidence.
