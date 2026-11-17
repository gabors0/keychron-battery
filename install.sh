#!/usr/bin/env bash
# Installer for keychron-battery (Keychron mouse battery tray applet).
#
#   ./install.sh                 install from this checkout
#   curl -fsSL https://raw.githubusercontent.com/gabors0/keychron-battery/main/install.sh | bash
#                                install straight from GitHub
#
# Options:
#   --uninstall      remove everything this script installed
#   --dev            symlink to the checkout instead of copying (for hacking)
#   --no-udev        skip the udev rule (no sudo needed)
#   --no-autostart   don't start the applet at login
#   --no-deps        don't try to install PySide6
#   -y, --yes        don't ask before installing packages
set -euo pipefail

REPO="gabors0/keychron-battery"
BRANCH="main"
RAW="https://raw.githubusercontent.com/$REPO/$BRANCH"
FILES=(keychron_battery.py keychron-battery.desktop 70-keychron-hidraw.rules)

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/keychron-battery"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/keychron-battery"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
UDEV_RULE="/etc/udev/rules.d/70-keychron-hidraw.rules"

uninstall=0 dev=0 udev=1 autostart=1 deps=1 yes=0
for arg in "$@"; do
  case "$arg" in
    --uninstall)    uninstall=1 ;;
    --dev)          dev=1 ;;
    --no-udev)      udev=0 ;;
    --no-autostart) autostart=0 ;;
    --no-deps)      deps=0 ;;
    -y|--yes)       yes=1 ;;
    -h|--help)      sed -n '2,16p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Ask y/n; reads from the terminal so it also works under `curl | bash`.
confirm() {
  (( yes )) && return 0
  [[ -r /dev/tty ]] || return 1
  local ans
  read -r -p "$1 [Y/n] " ans < /dev/tty || return 1
  [[ -z "$ans" || "$ans" =~ ^[Yy] ]]
}

as_root() {
  if [[ $EUID -eq 0 ]]; then "$@"
  elif command -v sudo >/dev/null; then sudo "$@"
  elif command -v doas >/dev/null; then doas "$@"
  else die "need root for: $* (install sudo or run the command as root)"
  fi
}

stop_running() {
  # Only "python3 [/path/]keychron[-_]battery[.py] ...", anchored at the start
  # of the command line so unrelated processes mentioning the name survive.
  pkill -u "$(id -u)" -f '^(\S*/)?python3?(\.[0-9]+)? (\S*/)?keychron[-_]battery(\.py)?( |$)' \
    2>/dev/null && sleep 0.5 || true
}

# ---------------------------------------------------------------- uninstall
if (( uninstall )); then
  say "Stopping applet"
  stop_running
  say "Removing user files"
  rm -f "$BIN" "$DESKTOP_DIR/keychron-battery.desktop" "$AUTOSTART_DIR/keychron-battery.desktop"
  rm -rf "$APP_DIR"
  if (( udev )) && [[ -e "$UDEV_RULE" ]]; then
    say "Removing udev rule (needs root)"
    as_root rm -f "$UDEV_RULE"
    as_root udevadm control --reload-rules || true
  fi
  say "Uninstalled."
  exit 0
fi

# ---------------------------------------------------------------- sources
src=""
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "$script_dir" && -f "$script_dir/keychron_battery.py" ]]; then
  src="$script_dir"
else
  (( dev )) && die "--dev only works when run from a checkout"
  command -v curl >/dev/null || die "curl is required"
  src="$(mktemp -d)"
  trap 'rm -rf "$src"' EXIT
  say "Downloading from github.com/$REPO"
  for f in "${FILES[@]}"; do
    curl -fsSL "$RAW/$f" -o "$src/$f" || die "failed to download $f"
  done
fi

# ---------------------------------------------------------------- deps
command -v python3 >/dev/null || die "python3 is required"

have_pyside() { python3 -c 'import PySide6.QtWidgets' >/dev/null 2>&1; }

if (( deps )) && ! have_pyside; then
  pkgs=() pm=()
  if   command -v dnf     >/dev/null; then pm=(dnf install -y);           pkgs=(python3-pyside6)
  elif command -v apt-get >/dev/null; then pm=(apt-get install -y);       pkgs=(python3-pyside6.qtwidgets python3-pyside6.qtgui python3-pyside6.qtcore)
  elif command -v pacman  >/dev/null; then pm=(pacman -S --needed --noconfirm); pkgs=(pyside6)
  elif command -v zypper  >/dev/null; then pm=(zypper install -y);        pkgs=(python3-pyside6)
  fi
  if (( ${#pm[@]} )) && confirm "PySide6 is missing. Install ${pkgs[*]} with ${pm[0]}?"; then
    as_root "${pm[@]}" "${pkgs[@]}" || warn "package install failed"
  fi
  if ! have_pyside && confirm "Install PySide6 with pip (--user) instead?"; then
    python3 -m pip install --user PySide6 \
      || python3 -m pip install --user --break-system-packages PySide6 \
      || warn "pip install failed"
  fi
  have_pyside || die "PySide6 is not available; install it and re-run (or use --no-deps)"
elif ! have_pyside; then
  warn "PySide6 not found; the tray applet won't start until it is installed"
fi

# ---------------------------------------------------------------- files
stop_running
mkdir -p "$BIN_DIR" "$DESKTOP_DIR"
if (( dev )); then
  say "Linking $BIN -> $src/keychron_battery.py"
  rm -rf "$APP_DIR"
  ln -sf "$src/keychron_battery.py" "$BIN"
else
  say "Installing to $APP_DIR"
  mkdir -p "$APP_DIR"
  install -m755 "$src/keychron_battery.py" "$APP_DIR/keychron_battery.py"
  ln -sf "$APP_DIR/keychron_battery.py" "$BIN"
fi
chmod +x "$src/keychron_battery.py" 2>/dev/null || true

# Absolute Exec path, so it works even if ~/.local/bin isn't on PATH.
desktop="$(sed "s|^Exec=.*|Exec=$BIN|" "$src/keychron-battery.desktop")"
printf '%s\n' "$desktop" > "$DESKTOP_DIR/keychron-battery.desktop"
if (( autostart )); then
  mkdir -p "$AUTOSTART_DIR"
  printf '%s\n' "$desktop" > "$AUTOSTART_DIR/keychron-battery.desktop"
  say "Enabled autostart"
else
  rm -f "$AUTOSTART_DIR/keychron-battery.desktop"
fi

# ---------------------------------------------------------------- udev
if (( udev )); then
  if cmp -s "$src/70-keychron-hidraw.rules" "$UDEV_RULE" 2>/dev/null; then
    say "udev rule already installed"
  else
    say "Installing udev rule $UDEV_RULE (needs root)"
    as_root install -m644 "$src/70-keychron-hidraw.rules" "$UDEV_RULE"
    as_root udevadm control --reload-rules
  fi
  # Re-apply rules to already-plugged devices. (Not --attr-match=idVendor:
  # that only checks the hidraw node itself, never its USB parent, so it
  # matches nothing.) A change event on every hidraw node is harmless.
  as_root udevadm trigger --action=change --subsystem-match=hidraw || true
  as_root udevadm settle --timeout=5 || true
fi

case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on your PATH" ;; esac

# ---------------------------------------------------------------- start
if [[ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]] && have_pyside; then
  say "Starting applet"
  setsid -f "$BIN" >/dev/null 2>&1 < /dev/null || nohup "$BIN" >/dev/null 2>&1 &
fi

say "Done. Plug in the mouse or receiver; check with: keychron-battery --probe"
