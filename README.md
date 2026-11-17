# keychron-battery

KDE/StatusNotifier tray icon showing the battery level of a Keychron mouse
(M6, M6 8K, M5, …), wired over USB-C or wireless through the 2.4 GHz receiver.

It speaks Keychron's vendor HID protocol over `/dev/hidraw*` (the same one the
Keychron Launcher uses; protocol from csutcliff/keychron-battery-dkms):
output report `B3 06` → input report `B4 06 …`, byte 20 = battery
(bit 7 = charging, bits 0–6 = %). No kernel module needed.

## Install

    ./install.sh          # symlinks ~/.local/bin/keychron-battery, autostart,
                          # and installs the udev rule (asks for sudo)
    keychron-battery &

## Debug

    keychron-battery --probe   # lists candidate hidraw nodes, dumps raw replies
    keychron-battery --once    # prints e.g. "73%" or "73% charging"

Tray: left-click = refresh now. Icon shows the % with a level bar
(green / orange ≤20 / red ≤10 / blue + bolt while charging, dimmed = last known
value while the mouse is asleep). Notifies once at 20 %, 10 % and 5 %.
