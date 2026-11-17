# keychron-battery

KDE/StatusNotifier tray icon showing the battery level of a Keychron mouse
(M6, M6 8K, M5, …), wired over USB-C or wireless through the 2.4 GHz receiver.

It speaks Keychron's vendor HID protocol over `/dev/hidraw*` (the same one the
Keychron Launcher uses; protocol from csutcliff/keychron-battery-dkms):
output report `B3 06` → input report `B4 06 …`, byte 20 = battery
(bit 7 = charging, bits 0–6 = %). No kernel module needed.

## Install

One-liner:

    curl -fsSL https://raw.githubusercontent.com/gabors0/keychron-battery/main/install.sh | bash

or from a clone:

    git clone https://github.com/gabors0/keychron-battery && cd keychron-battery
    ./install.sh

The installer:

- installs PySide6 if missing (dnf / apt / pacman / zypper, or `pip --user`; asks first)
- copies the applet to `~/.local/share/keychron-battery`, links `~/.local/bin/keychron-battery`
- adds an app-menu entry and a login autostart entry
- installs the udev rule `/etc/udev/rules.d/70-keychron-hidraw.rules` (sudo) so
  your user can talk to the mouse
- starts the applet

Options: `--no-udev`, `--no-autostart`, `--no-deps`, `--dev` (symlink to the
checkout instead of copying), `-y` (don't ask), `--uninstall`.

## Debug

    keychron-battery --probe   # lists candidate hidraw nodes, dumps raw replies
    keychron-battery --once    # prints e.g. "73%" or "73% charging"

Tray: left-click = refresh now. Icon shows the % with a level bar
(green / orange ≤20 / red ≤10 / blue + bolt while charging, dimmed = last known
value while the mouse is asleep). Notifies once at 20 %, 10 % and 5 %.
