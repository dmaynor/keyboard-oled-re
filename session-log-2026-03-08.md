# Session Log — 2026-03-08

## Operator
David Maynor ("Violator Actual") + Claude Opus 4.6 (AI assistant driving automation)

## Objective
Set up a Windows 11 QEMU/KVM virtual machine to capture USB/HID traffic between Corsair iCUE software and the Corsair Vanguard 96 keyboard LCD. The captured protocol data will be used to reverse engineer the LCD write sequence that is currently missing from our Linux-side Bragi protocol implementation.

## Host System
- **Machine**: System76 Thelio (Linux, kernel 6.18.7)
- **OS**: Pop!_OS / Ubuntu-based
- **QEMU**: 8.2.2
- **Python**: 3.12 (virtualenv at `venv/`)
- **Workspace**: `/home/dmaynor/code/keyboard-oled-re/`

---

## Timeline

### Phase 1: Documentation & Git Setup (~15:30–16:30)

**Goal**: Document all prior RE work and create a public GitHub repo.

1. **Reviewed existing files**: `azoth_oled.py`, `findings.md`, `azoth-x-capture-handoff.md`, scattered photos in root directory.

2. **Created `README.md`** with:
   - Target device table (ROG Azoth X, SteelSeries Apex Pro Gen 3, Corsair Vanguard 96)
   - Protocol findings for all three keyboards
   - Project structure diagram
   - VM setup/interaction guide
   - Safety notes (dangerous commands to avoid)

3. **Created `.gitignore`** excluding:
   - `vm/` (80GB QCOW2 disk image + transient state)
   - `venv/`, `__pycache__/`, `*.pyc`
   - `ckb-next/` (reference checkout with its own git repo)
   - `*.iso`, `*.ppm` (QEMU temp files)
   - `/*.jpg`, `/*.png` (stray root images — allows `pics/` subdirs)
   - Editor files (`.swp`, `.idea/`, `.vscode/`)

4. **Organized photos** into `pics/` with chronological subdirectories:
   - `01-azoth-oled-probing/` — 12 photos of initial HID probing
   - `02-azoth-oled-modes/` — 5 photos of OLED mode demos
   - `03-steelseries-oled/` — 5 photos of SteelSeries discovery
   - `04-steelseries-writes/` — 16 photos of OLED writes and call sign
   - `05-corsair-lcd-probing/` — 4 photos of Corsair LCD probing
   - `06-corsair-boot-sequence/` — 50 photos of Corsair boot animation
   - `07-corsair-post-boot/` — 2 photos of post-boot LCD state

5. **Initialized git repo**, configured identity:
   ```
   git config user.name "David Maynor"
   git config user.email "dmaynor@gmail.com"
   ```

6. **Created GitHub repo** via `gh repo create dmaynor/keyboard-oled-re --public --source=.`

7. **Commits pushed**:
   - `ffb6846` — Initial commit: keyboard OLED/LCD reverse engineering project
   - `9955120` — Add Armoury Crate USB capture handoff guide for ROG Azoth X
   - `d7bf92a` — Add photo evidence organized by RE phase

### Phase 2: Windows 11 VM Creation (~16:30–17:30)

**Goal**: Create a QEMU/KVM VM with Windows 11 for running iCUE.

1. **Created VM infrastructure**:
   ```bash
   mkdir -p vm/tpm
   qemu-img create -f qcow2 vm/win11.qcow2 80G
   cp /usr/share/OVMF/OVMF_VARS_4M.ms.fd vm/OVMF_VARS.fd
   ```

2. **Started TPM 2.0 emulator** (required for Windows 11):
   ```bash
   swtpm socket --tpmstate dir=vm/tpm --tpm2 \
     --ctrl type=unixio,path=vm/tpm/swtpm-sock &
   ```

3. **Launched QEMU** with:
   - q35 machine type with KVM acceleration
   - 8 CPU cores, 8GB RAM
   - OVMF UEFI firmware (MS Secure Boot variant)
   - TPM 2.0 via swtpm
   - AHCI/SATA disk (Windows has built-in drivers, unlike VirtIO)
   - USB tablet for absolute mouse positioning
   - Corsair keyboard USB passthrough (`1B1C:2B0D`)
   - VNC on `:0` (port 5900) for programmatic control
   - QEMU monitor on Unix socket for sendkey/screendump
   - Initially used `virtio-net-pci` NIC (changed later to e1000)

4. **Boot-from-CD workaround**: The "Press any key to boot from CD" prompt has a short timeout. Solved by spamming `sendkey ret` via the QEMU monitor socket immediately after launch:
   ```bash
   for i in $(seq 1 30); do
     echo "sendkey ret" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
     sleep 0.3
   done
   ```

5. **Windows installer proceeded normally** through disk selection and file copying.

### Phase 3: Windows 11 OOBE Automation (~17:30–19:00)

**Goal**: Complete Windows 11 Out-of-Box Experience via VNC automation.

**Tools used**:
- `vncdotool` (`vncdo`) — mouse moves, clicks, screenshots, key presses
- QEMU monitor socket via `socat` — sendkey for special chars, screendump for full-res captures
- Auto-incrementing screenshot helper:
  ```bash
  V="venv/bin/vncdo -s localhost:0"
  N(){ SNAP="vnc_$(printf '%03d' $(ls vnc_*.png 2>/dev/null | wc -l)).png"; $V capture "$SNAP"; echo "$SNAP"; }
  ```

**OOBE Steps & Issues**:

1. **Region/keyboard selection** — Clicked through via VNC at 1280x800 resolution. Discovered OOBE Next buttons are at approximately y=680.

2. **Accessibility panel accidentally opened** — Clicked accessibility icon instead of Next. Dismissed by clicking neutral area.

3. **"Let's connect you to a network" blocker** — Windows 11 OOBE requires internet for Microsoft account sign-in. With the initial VirtIO NIC, Windows had no network driver.
   - **Fix**: Shift+F10 opened a command prompt, typed `oobe\bypassnro`, VM rebooted. After reboot, "I don't have internet" option appeared.

4. **Account creation** — User rejected "User" as too generic. Created local account `ViolatorActual` with no password.

5. **Privacy settings** — Toggled all privacy options off, clicked Accept.

6. **Windows desktop reached** — Login screen appeared, clicked through to desktop.

### Phase 4: Networking Fix (~19:00–19:15)

**Problem**: VirtIO NIC has no built-in Windows driver. No network = can't download iCUE.

**Fix**: Shut down VM, relaunched with `-device e1000,netdev=net0` instead of `-device virtio-net-pci,netdev=net0`. Windows has a built-in Intel e1000 driver.

**Result**: `ipconfig` showed `10.0.2.15` via QEMU user-mode NAT. Internet working.

### Phase 5: iCUE Download & Install (~19:15–19:45)

**Goal**: Download and install Corsair iCUE in the Windows VM.

1. **Edge sign-in modal** — Edge's first-run experience showed a persistent Microsoft sign-in dialog that blocked browser interaction. Worked around with `Win+R` -> `cmd` to get a command prompt.

2. **Download attempt 1: PowerShell via cmd** — Failed. Single quotes from `vncdo type` conflicted with PowerShell's quoting when launched via `cmd /c powershell -command '...'`:
   ```
   Unexpected token 'https' in expression or statement.
   ```

3. **Download attempt 2: curl.exe** — Failed. `vncdo type` sends `;` instead of `:`, so URLs became `https;//www3.corsair.com/...`:
   ```
   curl: (3) URL rejected: Bad hostname
   ```

4. **Root cause discovered**: `vncdo type` has a **keysym mapping bug** — it sends semicolon (`;`) instead of colon (`:`) for the `:` character.

5. **Created `qemu_type.sh`** — A helper script that types text into the VM via QEMU monitor `sendkey` commands, correctly mapping all special characters:
   - `:` → `sendkey shift-semicolon`
   - `\` → `sendkey backslash`
   - `"` → `sendkey shift-apostrophe`
   - `%` → `sendkey shift-5`
   - All alphanumeric + 30+ special chars handled
   - Initial version had a bug: `'\\'` in bash case statement doesn't match a single `\`. Fixed to `\\)` (unquoted).

6. **Download attempt 3: PowerShell via qemu_type.sh** — Launched `powershell` interactively, then typed via `qemu_type.sh`:
   ```powershell
   Invoke-WebRequest -Uri "https://www3.corsair.com/software/CUE_V5/public/modules/windows/installer/Install iCUE.exe" -OutFile "$env:USERPROFILE\Desktop\iCUE_setup.exe"
   ```
   **Result**: Downloaded successfully. `dir` confirmed `iCUE_setup.exe` at 34,224,432 bytes (~33MB).

7. **Launched installer**:
   ```powershell
   Start-Process "$env:USERPROFILE\Desktop\iCUE_setup.exe"
   ```

8. **UAC prompt appeared** — VNC mouse clicks don't work on the Windows secure desktop. Solved via QEMU monitor:
   ```bash
   echo "sendkey alt-y" | socat - UNIX-CONNECT:vm/qemu-monitor.sock
   ```

9. **iCUE installer opened** — Language selection dialog appeared. Initially accidentally selected Italian by clicking wrong coordinates.

10. **Coordinate calibration** — Used QEMU screendump + PIL crop/zoom to precisely map radio button positions at 1280x800. Found the cursor at (640,400) landed on "French (Français)", confirming coordinate system was correct but my initial estimates were off.

11. **Selected English** — Clicked at calibrated position (620, 325). Dialog switched to English.

12. **Found hidden Next button** — The yellow "Next" button was at the far right edge of the dialog, nearly invisible in the small VNC thumbnails. Found it by zooming into the bottom-right corner of the dialog via PIL cropping. Clicked at (960, 625).

13. **iCUE pre-installation packages downloading** — Installer began downloading components. Session paused here for documentation.

### Phase 6: Documentation Update & Git Push (~19:45–20:00)

1. **Moved VNC screenshots** — Copied 48 VNC captures from `vm/` to `pics/08-win11-vm-setup/`.

2. **Collected desktop screenshots** from `~/Pictures/` — 9 screenshots showing the full VM workflow from UEFI boot through iCUE installer. Copied with descriptive filenames:
   - `desktop_uefi-boot.png` — BIOS "Press any key to boot from CD"
   - `desktop_win11-installing.png` — Windows installer + terminal
   - `desktop_oobe-network.png` — "Let's connect you to a network" screen
   - `desktop_oobe-account.png` — "Who's going to use this device?"
   - `desktop_oobe-privacy.png` — Privacy settings
   - `desktop_win11-login.png` — Windows login for ViolatorActual
   - `desktop_edge-signin-block.png` — Edge sign-in modal blocking
   - `desktop_cmd-ipconfig.png` — cmd.exe with ipconfig showing 10.0.2.15
   - `desktop_icue-installing.png` — iCUE installer with bamboo wallpaper

3. **Collected webcam photos** from `~/Pictures/Webcam/` — 2 relevant photos of ROG Azoth X OLED display closeups added to `pics/01-azoth-oled-probing/`. Excluded: 2 Crucial SSD box photos and 1 blurry accidental photo.

4. **Copied `qemu_type.sh`** from `vm/` (gitignored) to repo root.

5. **Updated `README.md`** extensively:
   - Updated Corsair status to reflect iCUE in progress
   - Added `qemu_type.sh` and `pics/08-win11-vm-setup/` to project structure
   - Changed QEMU launch command to use e1000 NIC
   - Added "Windows 11 OOBE Notes" section
   - Added vncdo colon bug documentation
   - Added screenshot helper pattern
   - Added UAC `alt-y` workaround
   - Added `qemu_type.sh` usage docs
   - Added iCUE download instructions
   - Added "Lessons Learned" section with VNC and Windows 11 tips

6. **Committed and pushed**: `f945f47` — Win11 VM setup, iCUE install, qemu_type.sh helper, and evidence (61 files)

7. **Renamed default branch**: `master` → `main` via `git branch -m master main`, updated GitHub default, deleted old `master` remote branch.

---

## Tools Used

| Tool | Purpose |
|------|---------|
| `qemu-system-x86_64` | x86_64 VM with KVM acceleration |
| `swtpm` | TPM 2.0 emulator (Windows 11 requirement) |
| `socat` | Communicate with QEMU monitor Unix socket |
| `vncdotool` (`vncdo`) | VNC client for mouse/keyboard/screenshot automation |
| `qemu_type.sh` (custom) | Type text into VM via QEMU monitor sendkey |
| `Pillow` (Python) | Convert PPM screendumps to PNG, crop/zoom for analysis |
| `gh` (GitHub CLI) | Create repo, set default branch |
| `git` | Version control |

## Key Bugs Discovered

### vncdo colon-to-semicolon bug
- **Symptom**: `vncdo type 'https://...'` produces `https;//...` in the VM
- **Root cause**: vncdo sends the wrong X11 keysym for the `:` character
- **Impact**: All URLs and Windows paths typed via vncdo are broken
- **Workaround**: Use QEMU monitor `sendkey shift-semicolon` (wrapped in `qemu_type.sh`)

### vncdo double-quote bug
- **Symptom**: `vncdo type` cannot send `"` characters
- **Workaround**: Use QEMU monitor `sendkey shift-apostrophe`

### UAC secure desktop vs VNC
- **Symptom**: VNC mouse clicks have no effect on UAC "Do you want to allow..." dialogs
- **Root cause**: UAC runs on a separate secure desktop that doesn't receive VNC input
- **Workaround**: `echo "sendkey alt-y" | socat - UNIX-CONNECT:vm/qemu-monitor.sock`

### qemu_type.sh backslash matching
- **Symptom**: Script printed "Unknown char: \" for backslash characters
- **Root cause**: In bash, `'\\'` inside single quotes is a two-character pattern `\\`, which doesn't match a single `\`
- **Fix**: Changed case pattern from `'\\'` to `\\)` (unquoted escape)

## Artifacts Produced

### Files committed to repo
- `README.md` (updated) — Comprehensive project docs with VM setup guide and lessons learned
- `qemu_type.sh` (new) — QEMU monitor text input helper
- `pics/08-win11-vm-setup/` (new) — 57 evidence screenshots
  - `vnc_001.png` through `vnc_047.png` — VNC automation captures
  - `vnc_check.png`, `vnc_snap.png` — Calibration/diagnostic captures
  - `desktop_*.png` — 9 full desktop screenshots from host
- `pics/01-azoth-oled-probing/azoth-oled-closeup-*.jpg` (new) — 2 webcam photos

### Files in vm/ (gitignored, local only)
- `vm/win11.qcow2` — 80GB QCOW2 disk with Windows 11 installed
- `vm/OVMF_VARS.fd` — UEFI variable store (Secure Boot keys)
- `vm/tpm/` — TPM 2.0 persistent state
- `vm/qemu-monitor.sock` — QEMU monitor Unix socket
- `vm/qemu_type.sh` — Original copy (also copied to repo root)
- `vm/snap_*.ppm`, `vm/snap_*.png` — QEMU screendump temp files

## Current VM State

- **Windows 11** installed and booted, user `ViolatorActual` logged in
- **Network**: e1000 NIC, IP `10.0.2.15` via QEMU user-mode NAT
- **iCUE installer**: Running, was downloading pre-installation packages when session paused
- **Corsair Vanguard 96**: USB passthrough configured (`-device usb-host,vendorid=0x1b1c,productid=0x2b0d`)
- **PowerShell window open** in Windows Terminal

## Next Steps

1. Resume iCUE installation (may already be complete in the running VM)
2. Verify Corsair Vanguard 96 is detected by iCUE
3. Set up USB traffic capture (Wireshark + USBPcap, or Python HID logger)
4. Capture iCUE ↔ keyboard communication during LCD operations
5. Analyze captured traffic to find the missing LCD init/commit sequence
6. Implement LCD write from Linux using discovered protocol
7. Update project documentation and push findings

## Git Log

```
f945f47 Win11 VM setup, iCUE install, qemu_type.sh helper, and evidence
d7bf92a Add photo evidence organized by RE phase
9955120 Add Armoury Crate USB capture handoff guide for ROG Azoth X
ffb6846 Initial commit: keyboard OLED/LCD reverse engineering project
```

Branch renamed from `master` to `main` at end of session.
