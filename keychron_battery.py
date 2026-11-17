#!/usr/bin/env python3
"""Tray applet showing the battery level of a Keychron mouse (M6, M5, ...).

Talks Keychron's vendor HID protocol over /dev/hidraw* (the same one the
Keychron Launcher web app uses), so it works both wired over USB-C and
wirelessly through the 2.4 GHz receiver:

    host -> device   output report 0xB3, cmd 0x06   (status request)
    device -> host   input report  0xB4, cmd 0x06   (status response)
                     byte 20: bit 7 = charging, bits 0-6 = battery %

Usage:
    keychron_battery.py            run the tray applet
    keychron_battery.py --probe    list candidate devices and query each one
    keychron_battery.py --once     print the battery level and exit
"""

from __future__ import annotations

import errno
import os
import select
import sys
import time
from dataclasses import dataclass
from pathlib import Path

KEYCHRON_VID = 0x3434
REPORT_ID_CMD = 0xB3
REPORT_ID_RESP = 0xB4
CMD_STATUS = 0x06
BATTERY_OFFSET = 20
DEFAULT_REPORT_LEN = 64
RESPONSE_TIMEOUT_S = 0.5
QUERY_RETRIES = 3

# Known 2.4 GHz receivers (Ultra-Link 8K, Link-KM). When both the receiver and
# a cable are attached, the cable is preferred because it reports charging.
RECEIVER_PIDS = {0xD028, 0xD029}

POLL_INTERVAL_S = 60        # battery query while the mouse answers
RETRY_INTERVAL_S = 15       # while the mouse is asleep / not answering
SCAN_INTERVAL_S = 3         # cheap sysfs check for plug / unplug
LOW_WARN = (20, 10, 5)      # notify once when crossing each of these


# --------------------------------------------------------------------------
# Device discovery
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    node: str               # /dev/hidrawN
    pid: int
    name: str
    out_len: int            # output report length incl. report-ID byte

    @property
    def is_receiver(self) -> bool:
        return self.pid in RECEIVER_PIDS


def _output_report_len(desc: bytes, report_id: int) -> int | None:
    """Return the byte length (incl. ID byte) of an output report, or None if
    the descriptor does not declare that report ID as an output report."""
    i = 0
    size = count = 0
    rid = 0
    stack: list[tuple[int, int, int]] = []
    bits = 0
    found = False
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item
            if i + 1 >= len(desc):
                break
            i += 3 + desc[i + 1]
            continue
        n = (0, 1, 2, 4)[b & 3]
        data = int.from_bytes(desc[i + 1:i + 1 + n], "little")
        tag = b & 0xFC
        if tag == 0x74:        # Report Size
            size = data
        elif tag == 0x94:      # Report Count
            count = data
        elif tag == 0x84:      # Report ID
            rid = data
        elif tag == 0xA4:      # Push
            stack.append((size, count, rid))
        elif tag == 0xB4:      # Pop
            if stack:
                size, count, rid = stack.pop()
        elif tag == 0x90:      # Output (main item)
            if rid == report_id:
                found = True
                bits += size * count
        i += 1 + n
    if not found:
        return None
    return (bits + 7) // 8 + 1


def find_candidates() -> list[Candidate]:
    out = []
    for dev in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        try:
            uevent = (dev / "device" / "uevent").read_text()
        except OSError:
            continue
        props = dict(l.split("=", 1) for l in uevent.splitlines() if "=" in l)
        try:
            _bus, vid, pid = (int(x, 16) for x in props.get("HID_ID", "").split(":"))
        except ValueError:
            continue
        if vid != KEYCHRON_VID:
            continue
        try:
            desc = (dev / "device" / "report_descriptor").read_bytes()
        except OSError:
            continue
        length = _output_report_len(desc, REPORT_ID_CMD)
        if length is None:
            continue
        out.append(Candidate(
            node=f"/dev/{dev.name}",
            pid=pid,
            name=props.get("HID_NAME", "Keychron device"),
            out_len=max(length, 2),
        ))
    # cable first, receiver last
    out.sort(key=lambda c: c.is_receiver)
    return out


# --------------------------------------------------------------------------
# Query
# --------------------------------------------------------------------------

@dataclass
class Reading:
    percent: int
    charging: bool
    source: Candidate
    raw: bytes


class QueryError(Exception):
    pass


class PermissionProblem(QueryError):
    pass


def _drain(fd: int) -> None:
    while True:
        try:
            if not os.read(fd, 256):
                return
        except BlockingIOError:
            return


def query(c: Candidate, verbose: bool = False) -> Reading:
    try:
        fd = os.open(c.node, os.O_RDWR | os.O_NONBLOCK)
    except PermissionError as e:
        raise PermissionProblem(f"no permission to open {c.node}") from e
    except OSError as e:
        raise QueryError(f"cannot open {c.node}: {e.strerror}") from e
    try:
        for attempt in range(QUERY_RETRIES):
            if attempt:
                time.sleep(0.1)
            _drain(fd)
            req = bytearray(c.out_len)
            req[0], req[1] = REPORT_ID_CMD, CMD_STATUS
            try:
                os.write(fd, bytes(req))
            except OSError as e:
                if e.errno in (errno.ENODEV, errno.ENOENT):
                    raise QueryError("device went away") from e
                if verbose:
                    print(f"  write failed: {e}")
                continue
            deadline = time.monotonic() + RESPONSE_TIMEOUT_S
            while (left := deadline - time.monotonic()) > 0:
                r, _, _ = select.select([fd], [], [], left)
                if not r:
                    break
                try:
                    data = os.read(fd, 256)
                except BlockingIOError:
                    continue
                if verbose:
                    print(f"  recv {len(data):2d}B: {data[:24].hex(' ')}")
                if len(data) > BATTERY_OFFSET and data[0] == REPORT_ID_RESP \
                        and data[1] == CMD_STATUS:
                    v = data[BATTERY_OFFSET]
                    pct = v & 0x7F
                    if pct > 100:
                        raise QueryError(f"implausible battery byte 0x{v:02x}")
                    return Reading(pct, bool(v & 0x80), c, data)
        raise QueryError("no response (mouse asleep or out of range?)")
    finally:
        os.close(fd)


def read_battery(cands: list[Candidate] | None = None) -> Reading:
    cands = find_candidates() if cands is None else cands
    if not cands:
        raise QueryError("no Keychron mouse or receiver found")
    errors: list[QueryError] = []
    for c in cands:
        try:
            return query(c)
        except QueryError as e:
            errors.append(e)
    perm = [e for e in errors if isinstance(e, PermissionProblem)]
    raise perm[0] if perm else errors[0]


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def cli_probe() -> int:
    cands = find_candidates()
    if not cands:
        print("No Keychron hidraw interface declaring report 0xB3 found.")
        print("All Keychron hidraw devices:")
        for dev in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
            try:
                ue = (dev / "device" / "uevent").read_text()
            except OSError:
                continue
            if ":00003434:" in ue:
                print(f"  /dev/{dev.name}: " + " ".join(
                    l for l in ue.splitlines() if l.startswith(("HID_ID", "HID_NAME"))))
        return 1
    for c in cands:
        kind = "receiver" if c.is_receiver else "wired"
        print(f"{c.node}  pid={c.pid:04x}  {kind}  out_len={c.out_len}  {c.name}")
        try:
            r = query(c, verbose=True)
            print(f"  => {r.percent}%{' (charging)' if r.charging else ''}")
        except QueryError as e:
            print(f"  => error: {e}")
            if isinstance(e, PermissionProblem):
                rule = Path("/etc/udev/rules.d/70-keychron-hidraw.rules")
                print("     " + ("udev rule is installed; replug the device or run:\n"
                               "     sudo udevadm trigger --action=change --subsystem-match=hidraw"
                               if rule.exists() else
                               f"udev rule missing: run install.sh (installs {rule})"))
    return 0


def cli_once() -> int:
    try:
        r = read_battery()
    except QueryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{r.percent}%{' charging' if r.charging else ''}")
    return 0


# --------------------------------------------------------------------------
# Tray applet
# --------------------------------------------------------------------------

def run_tray() -> int:
    from PySide6.QtCore import QObject, QRectF, Qt, QThread, QTimer, Signal, Slot
    from PySide6.QtGui import (QAction, QColor, QFont, QFontMetrics, QIcon, QPainter,
                               QPixmap, QPolygonF)
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    class Worker(QObject):
        result = Signal(object)          # Reading | QueryError
        devices = Signal(object)         # list[Candidate]
        request = Signal()               # emitted from the GUI thread

        def __init__(self):
            super().__init__()
            self.request.connect(self.refresh)
            self._cands: list[Candidate] = []
            self._next_query = 0.0

        @Slot()
        def tick(self):
            cands = find_candidates()
            if cands != self._cands:
                self._cands = cands
                self.devices.emit(cands)
                self._next_query = 0.0   # (un)plugged: query right away
            if time.monotonic() >= self._next_query:
                self.refresh()

        @Slot()
        def refresh(self):
            try:
                r = read_battery(self._cands)
                self._next_query = time.monotonic() + POLL_INTERVAL_S
                self.result.emit(r)
            except QueryError as e:
                self._next_query = time.monotonic() + RETRY_INTERVAL_S
                self.result.emit(e)

    def make_icon(text: str, level: int | None, charging: bool,
                  dim: bool) -> QIcon:
        app = QApplication.instance()
        fg = app.palette().windowText().color()
        if dim:
            fg.setAlphaF(0.45)
        if level is None or charging:
            accent = QColor("#3daee9") if charging else fg
        elif level <= 10:
            accent = QColor("#da4453")
        elif level <= 20:
            accent = QColor("#f67400")
        else:
            accent = QColor("#27ae60")

        icon = QIcon()
        for s in (16, 22, 24, 32, 48, 64):
            pm = QPixmap(s, s)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            # level bar along the bottom
            bar_h = max(2, round(s * 0.14))
            bar = QRectF(0.5, s - bar_h - 0.5, s - 1, bar_h)
            p.setPen(Qt.NoPen)
            track = QColor(fg)
            track.setAlphaF(track.alphaF() * 0.25)
            p.setBrush(track)
            p.drawRoundedRect(bar, bar_h / 2, bar_h / 2)
            if level is not None:
                filled = QRectF(bar.x(), bar.y(), bar.width() * level / 100, bar_h)
                p.setBrush(accent)
                p.drawRoundedRect(filled, bar_h / 2, bar_h / 2)
            # number (shrunk to fit; leaves room on the right for the bolt)
            area_h = s - bar_h - 1
            bolt_w = s * 0.3 if charging else 0
            text_rect = QRectF(0, 0, s - bolt_w, area_h)
            f = QFont()
            f.setBold(True)
            px = round(area_h * 0.95)
            while px > 6:
                f.setPixelSize(px)
                if QFontMetrics(f).horizontalAdvance(text) <= text_rect.width():
                    break
                px -= 1
            p.setFont(f)
            p.setPen(accent if (level is not None and level <= 20 and not charging) else fg)
            p.drawText(text_rect, Qt.AlignCenter, text)
            # charging bolt in its own column on the right
            if charging:
                bw, bh = bolt_w, area_h * 0.8
                x0, y0 = s - bw, (area_h - bh) / 2
                pts = ((0.6, 0), (0, 0.58), (0.45, 0.58), (0.3, 1), (1, 0.38), (0.55, 0.38), (0.8, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(accent)
                p.drawPolygon(QPolygonF([QPointF(x0 + x * bw, y0 + y * bh) for x, y in pts]))
            p.end()
            icon.addPixmap(pm)
        return icon

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("keychron-battery")
    app.setDesktopFileName("keychron-battery")

    tray = QSystemTrayIcon()
    tray.setIcon(make_icon("–", None, False, True))
    tray.setToolTip("Keychron mouse: searching…")

    menu = QMenu()
    status_action = QAction("Searching…")
    status_action.setEnabled(False)
    menu.addAction(status_action)
    menu.addSeparator()
    refresh_action = QAction(QIcon.fromTheme("view-refresh"), "Refresh now")
    menu.addAction(refresh_action)
    quit_action = QAction(QIcon.fromTheme("application-exit"), "Quit")
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)

    thread = QThread()
    worker = Worker()
    worker.moveToThread(thread)
    thread.start()

    state = {"last": None, "last_time": None, "warned": set(), "devices": []}

    def on_devices(cands):
        state["devices"] = cands
        if not cands:
            state["last"] = None

    def on_result(res):
        if isinstance(res, Reading):
            prev = state["last"]
            state["last"], state["last_time"] = res, time.time()
            via = "receiver" if res.source.is_receiver else "USB cable"
            chg = " — charging" if res.charging else ""
            line = f"{res.percent}%{chg}"
            tray.setIcon(make_icon(str(res.percent), res.percent, res.charging, False))
            tray.setToolTip(f"Keychron mouse: {line}\nvia {via}")
            status_action.setText(f"Battery {line} (via {via})")
            if res.charging:
                state["warned"].clear()
            else:
                for t in LOW_WARN:
                    if res.percent <= t and t not in state["warned"]:
                        state["warned"].update(x for x in LOW_WARN if x >= t)
                        if prev is not None or res.percent <= LOW_WARN[-1]:
                            tray.showMessage("Keychron mouse battery low",
                                             f"{res.percent}% left — time to charge.",
                                             QSystemTrayIcon.Warning, 8000)
                        break
            return

        err = str(res)
        last = state["last"]
        if isinstance(res, PermissionProblem):
            msg = ("No access to /dev/hidraw — replug the mouse/receiver "
                   "(or run install.sh for the udev rule)")
        elif not state["devices"]:
            msg = "Mouse/receiver not connected"
        else:
            msg = err
        if last is not None and state["devices"]:
            age = int((time.time() - state["last_time"]) / 60)
            ago = "just now" if age < 1 else f"{age} min ago"
            tray.setIcon(make_icon(str(last.percent), last.percent, False, True))
            tray.setToolTip(f"Keychron mouse: {last.percent}% ({ago})\n{msg}")
            status_action.setText(f"Battery {last.percent}% ({ago}) — {msg}")
        else:
            tray.setIcon(make_icon("–", None, False, True))
            tray.setToolTip(f"Keychron mouse\n{msg}")
            status_action.setText(msg)

    worker.devices.connect(on_devices)
    worker.result.connect(on_result)

    timer = QTimer()
    timer.setInterval(SCAN_INTERVAL_S * 1000)
    timer.timeout.connect(worker.tick)
    timer.start()
    refresh_action.triggered.connect(worker.request.emit)
    QTimer.singleShot(0, worker.tick)

    def on_activated(reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.MiddleClick):
            worker.request.emit()
    tray.activated.connect(on_activated)

    tray.show()
    app.aboutToQuit.connect(lambda: (thread.quit(), thread.wait(2000)))
    return app.exec()


def main() -> int:
    if "--probe" in sys.argv:
        return cli_probe()
    if "--once" in sys.argv:
        return cli_once()
    return run_tray()


if __name__ == "__main__":
    sys.exit(main())
