#!/usr/bin/env bash
# Installs the applet for the current user and the udev rule system-wide.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

mkdir -p ~/.local/bin ~/.local/share/applications ~/.config/autostart
ln -sf "$here/keychron_battery.py" ~/.local/bin/keychron-battery
cp "$here/keychron-battery.desktop" ~/.local/share/applications/
cp "$here/keychron-battery.desktop" ~/.config/autostart/
echo "Installed ~/.local/bin/keychron-battery (+ menu entry and autostart)"

if ! cmp -s "$here/70-keychron-hidraw.rules" /etc/udev/rules.d/70-keychron-hidraw.rules; then
  echo "Installing udev rule (needs sudo)…"
  sudo install -m644 "$here/70-keychron-hidraw.rules" /etc/udev/rules.d/
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=hidraw --attr-match=idVendor=3434 || true
fi
echo "Done. Start it now with: keychron-battery &"
