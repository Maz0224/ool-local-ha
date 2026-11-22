"""OOL Studio V6 — disabled-device exclusion, groups, scanner, diagnostics.

Built from packets captured from the owner's Gemmy Orchestra of Lights app.
The owner has verified that global RGB and preset packets work, and that
selecting only the projector in the official app scopes a later RGB command.

A5 single-device selection and global RGB commands were verified by the owner.
The program still verifies selection before EVERY manual command or effect.
Only ONE finite animation can run at a time, to avoid overwhelming the hub.
No port scanning, automatic retries, or unlimited animation loops.
Power/Bluetooth/volume packets came from the owner's iPhone capture.
Owner reports A6 09 power applies to the currently selected devices.
Groups select all members and send a single command after verified selection.
No automatic network commands at startup. Scanning is READ ONLY (A5 query).
New IDs are registered only when ADD SELECTED TO STUDIO is clicked.
No recovery-flashing firmware.

Windows 11 / Python 3.9+; standard library only.
"""

from __future__ import annotations

import colorsys
import ipaddress
import json
import queue
import socket
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

HUB_IP = "192.168.0.151"
HUB_PORT = 8087
CONNECT_TIMEOUT = 4.0
FRAME_TIMEOUT = 3.5
MIN_ACTION_INTERVAL = 4.0
MIN_EFFECT_INTERVAL = 1.5
MAX_EFFECT_FRAMES = 20
CONFIG_FILE = Path(__file__).with_name("ool_multidevice_labels.json")
GROUP_FILE = Path(__file__).with_name("ool_multidevice_groups.json")
REGISTER_FILE = Path(__file__).with_name("ool_registered_devices.json")
DISABLED_FILE = Path(__file__).with_name("ool_disabled_devices.json")
HA_EXPORT_FILE = Path(__file__).with_name("ool_ha_devices.json")
# Starts excluded because the official app also failed to deselect this net light.
DEFAULT_DISABLED_IDS = {"A1 00 C7 16 31 24"}


def read_disabled_ids():
    try:
        raw = json.loads(DISABLED_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set(DEFAULT_DISABLED_IDS)
    except (OSError, ValueError):
        return set(DEFAULT_DISABLED_IDS)
    if not isinstance(raw, list):
        return set(DEFAULT_DISABLED_IDS)
    good = set()
    for entry in raw:
        if not isinstance(entry, str):
            continue
        try:
            id_bytes = bytes.fromhex(entry)
            if len(id_bytes) == 6:
                good.add(hex_data(id_bytes))
        except ValueError:
            continue
    return good


def write_disabled_ids(ids):
    temp = DISABLED_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(sorted(ids), indent=2) + "\n", encoding="utf-8")
    temp.replace(DISABLED_FILE)


# 6-byte identifiers from hub's A5 status response, confirmed present in capture.
# Device identities beyond projector are *tentative* until tested.
DEVICES = [
    ("Net lights A (verify)", "A1 00 C7 16 31 24", "#FF4D67"),
    ("Net lights B (verify)", "A1 00 3F 3E 22 24", "#4E8BFF"),
    ("Socket bulb (verify)", "07 00 64 19 31 24", "#FFD26A"),
    ("Projector (confirmed)", "05 00 9B 43 31 24", "#C183FF"),
]
BUILTIN_DEVICES = tuple(DEVICES)


def read_registered_devices() -> list[tuple[str, str, str]]:
    """Read extra hub-discovered IDs from disk; never trust malformed IDs."""
    try:
        raw = json.loads(REGISTER_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    extras = []
    seen = {item[1] for item in DEVICES}
    for item in raw:
        if not isinstance(item, dict):
            continue
        ident = item.get("id")
        name = item.get("name")
        color = item.get("color")
        if not isinstance(ident, str):
            continue
        try:
            parsed = bytes.fromhex(ident)
            canonical = parsed.hex(" ").upper()
        except ValueError:
            continue
        if len(parsed) != 6 or canonical in seen:
            continue
        name = name.strip()[:60] if isinstance(name, str) and name.strip() else "Discovered OOL Light"
        color = color if isinstance(color, str) else "#66D9CB"
        try:
            parse_color(color)
        except ValueError:
            color = "#66D9CB"
        seen.add(canonical)
        extras.append((name, canonical, color))
    return extras


def write_registered_devices(items: list[tuple[str, str, str]]) -> None:
    """Persist only additional registered IDs, independent of saved aliases/groups."""
    payload = [{"id": ident, "name": name, "color": color} for name, ident, color in items]
    tmp = REGISTER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(REGISTER_FILE)


A5_QUERY = bytes.fromhex("03 00 A5 00 01")
A5_SELECT_PREFIX = bytes.fromhex("0D 00 A5 01 01 06 84 01 20")
A5_DESELECT_PREFIX = bytes.fromhex("0B 00 A5 02 01 06 84")
PROJECTOR_ID = bytes.fromhex("05 00 9B 43 31 24")


def device_power_packet(on: bool) -> bytes:
    """A6 09 power command, scoped to the selected decoration(s)."""
    return bytes.fromhex("03 00 A6 09") + bytes([int(on)])


def projector_bluetooth_packet(on: bool) -> bytes:
    """Captured Bluetooth mode command. Projector must be selected first."""
    return bytes.fromhex("03 00 A6 0D") + bytes([int(on)])


def projector_volume_packet(value: int) -> bytes:
    """Captured 0–100 slider shape: duplicate value bytes."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("Volume must be an integer from 0 to 100")
    return bytes.fromhex("0B 00 AA 00 00 01 04") + bytes([value, value]) + bytes(4)



def rgb_packet(rgb: tuple[int, int, int]) -> bytes:
    if len(rgb) != 3 or any(not 0 <= n <= 255 for n in rgb):
        raise ValueError("Each RGB channel must be 0..255")
    # Identical packet shape to the working custom-color controller.
    # The last byte remains 0x64; its purpose has not been established.
    return bytes.fromhex("0B 00 AA 00 00 03") + bytes(rgb) + bytes.fromhex("00 00 00 64")


def parse_color(text: str) -> tuple[int, int, int]:
    digits = text.strip().lstrip("#")
    if len(digits) != 6 or any(c not in "0123456789ABCDEFabcdef" for c in digits):
        raise ValueError("Enter a HEX color such as #FF2827")
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def hex_data(data: bytes) -> str:
    return data.hex(" ").upper()


def make_fade(start: tuple[int, int, int], end: tuple[int, int, int], count: int):
    if not 2 <= count <= MAX_EFFECT_FRAMES:
        raise ValueError("Invalid fade frame count")
    return [
        tuple(round(a + (b - a) * i / (count - 1)) for a, b in zip(start, end))
        for i in range(count)
    ]


def make_rainbow(count: int):
    if not 2 <= count <= MAX_EFFECT_FRAMES:
        raise ValueError("Invalid rainbow frame count")
    return [
        tuple(round(x * 255) for x in colorsys.hsv_to_rgb(i / count, 1, 1))
        for i in range(count)
    ]


class UnrecognizedSelectionStatus(ValueError):
    """A5 status frame exists, but selected-slot values cannot be trusted."""


class HubSession:
    """Persistent TCP connection with length-prefixed framing."""

    def __init__(self, host: str):
        self.sock = socket.create_connection((host, HUB_PORT), timeout=CONNECT_TIMEOUT)
        self.sock.settimeout(FRAME_TIMEOUT)
        self.buf = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.sock.close()

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def recv_frame(self) -> bytes:
        deadline = time.monotonic() + FRAME_TIMEOUT
        while True:
            if len(self.buf) >= 2:
                length = int.from_bytes(self.buf[:2], "little")
                if not 0 < length <= 4096:
                    raise ValueError("Unexpected hub response length: " + str(length))
                if len(self.buf) >= length + 2:
                    payload = bytes(self.buf[2:length + 2])
                    del self.buf[:length + 2]
                    return payload
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for a complete A5 response")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Hub closed connection while waiting for status")
            self.buf.extend(chunk)

    def query_selection(self) -> tuple[dict[bytes, bytes], int]:
        self.send(A5_QUERY)
        for _ in range(5):
            payload = self.recv_frame()
            if payload[:2] != bytes.fromhex("A5 00"):
                continue
            if len(payload) < 8 or (len(payload) - 8) % 8:
                raise ValueError("Unexpected A5 device-list layout: " + hex_data(payload))
            if payload[4:8] != bytes.fromhex("06 84 FF FF"):
                raise ValueError("Unexpected A5 device-list header: " + hex_data(payload[:8]))
            selected_count = payload[2]
            unselected_count = payload[3]
            num_records = (len(payload) - 8) // 8
            if selected_count + unselected_count != num_records:
                raise ValueError("A5 record count doesn't match header")
            records = {}
            for i in range(8, len(payload), 8):
                rec = payload[i:i + 8]
                identifier, marker = rec[:6], rec[6:8]
                if identifier in records:
                    raise ValueError("Duplicate device ID in hub response")
                records[identifier] = marker
            selected = [key for key, val in records.items() if val != b"\xff\x00"]
            invalid = [(ident, mark) for ident, mark in records.items()
                       if mark != b"\xff\x00" and
                       (mark[1] != 0 or not 1 <= mark[0] <= num_records)]
            if len(selected) != selected_count or invalid:
                # Keep the raw reply. A fifth OOL decoration may cause a new
                # marker encoding, a temporarily inconsistent status, or
                # a hub limitation. NEVER interpret an unknown value as a
                # verified selection: show the packet for analysis instead.
                marks = ", ".join(hex_data(ident) + ":" + hex_data(mark)
                                  for ident, mark in records.items())
                raise UnrecognizedSelectionStatus(
                    f"A5 header says {selected_count} selected, markers show "
                    f"{len(selected)} of {num_records}; "
                    f"IDs/markers: {marks}; RAW A5: {hex_data(payload)}"
                )
            return records, selected_count
        raise ValueError("No A5 selection response received")


def selection_ids(records: dict[bytes, bytes]) -> set[bytes]:
    """Return the devices selected in the hub's A5 status response."""
    return {identifier for identifier, marker in records.items() if marker != b"\xff\x00"}


def show_selection(records: dict[bytes, bytes], logger, label: str) -> None:
    logger(f"{label}: {len(selection_ids(records))} selected / {len(records)} devices")
    for identifier, marker in records.items():
        state = "not selected" if marker == b"\xff\x00" else f"slot {marker[0]}"
        logger("  " + hex_data(identifier) + "  " + state)


def verify_device_list(records: dict[bytes, bytes]) -> None:
    """Known IDs must be present; never alter unknown selected devices."""
    registered = {bytes.fromhex(dev[1]) for dev in DEVICES}
    missing = registered - set(records)
    if missing:
        raise ValueError("The hub is missing registered device(s): " +
                         ", ".join(hex_data(x) for x in sorted(missing)) +
                         ". Scan paired devices before sending commands.")
    unknown_selected = selection_ids(records) - registered
    if unknown_selected:
        raise ValueError("An unregistered device is selected on the hub: " +
                         ", ".join(hex_data(x) for x in sorted(unknown_selected)) +
                         ". Add it using the scanner before controlling other lights.")


def await_selection(session: HubSession, expected: set[bytes], logger, step: str) -> None:
    """Poll STATUS only. Never blindly re-send selection mutations.

    A5 changes can be visible a little later than the TCP write. Previously the
    program sent DESELECT and SELECT together, then saw both devices selected.
    Confirm every intermediate mutation before sending the next one.
    """
    last_invalid = None
    attempts = 6
    for attempt in range(1, attempts + 1):
        # A short quiet period lets the hub process the previous A5 mutation.
        time.sleep(0.35 if attempt == 1 else 0.45)
        try:
            records, _ = session.query_selection()
        except UnrecognizedSelectionStatus as exc:
            # An A5 status may be transitional immediately after selecting a
            # newly paired device. Retry only READ-ONLY status queries; do NOT
            # issue additional selection changes or lighting commands.
            last_invalid = str(exc)
            logger(f"  {step}: unexpected A5 status ({attempt}/{attempts}); "
                   f"retrying READ-ONLY query. {last_invalid}")
            continue
        verify_device_list(records)
        actual = selection_ids(records)
        if actual == expected:
            logger(f"  {step} confirmed (status read {attempt}/{attempts}).")
            return
        logger(f"  {step} pending ({attempt}/{attempts}): "
               f"{len(actual)} selected, expected {len(expected)}")
    if last_invalid:
        raise UnrecognizedSelectionStatus(
            f"{step}: selection not verified after {attempts} status reads; "
            f"no lighting command sent. LAST STATUS: {last_invalid}"
        )
    raise ValueError(
        f"{step} did not take effect after {attempts} status reads; "
        "selection unverified, no lighting command sent"
    )


def select_members(session: HubSession, targets, logger, disabled_ids=frozenset()) -> None:
    """Select exactly the desired nonempty set, failing closed if hub disagrees.

    Allows one target (individual light) or several targets (synchronized group).
    Every select/deselect is followed by an A5 status confirmation, never a
    blind sleep followed by an unrelated selection mutation.
    """
    wanted = set(targets)
    disabled = set(disabled_ids)
    if wanted & disabled:
        raise ValueError("Disabled device is in the target; action blocked")
    expected_ids = {bytes.fromhex(dev[1]) for dev in DEVICES}
    if not wanted or not wanted <= expected_ids:
        raise ValueError("Group has missing or unknown members; nothing sent")

    before, _ = session.query_selection()
    verify_device_list(before)
    show_selection(before, logger, "BEFORE")
    current = selection_ids(before)
    # A disabled device still selected on the physical hub WILL receive any
    # common AA/A6 command. Never silently bypass this invariant.
    stuck = current & disabled
    if stuck:
        raise ValueError("A disabled device is still selected on the HUB (" +
                         ", ".join(hex_data(x) for x in sorted(stuck)) +
                         "). OOL Studio won't send a shared command that could change it. "
                         "Deselect it in the official app or disconnect it safely.")
    if current == wanted:
        logger(f"Selection already correct: {len(wanted)} device(s).")
        return

    # First remove any outsiders, one command at a time, verifying after EACH.
    # Iterating the hub's live record order makes results easy to trace.
    for identifier in before:
        if identifier not in current or identifier in wanted:
            continue
        session.send(A5_DESELECT_PREFIX + identifier)
        logger("DESELECT " + hex_data(identifier))
        current.remove(identifier)
        await_selection(session, current, logger, "DESELECT " + hex_data(identifier))

    # Only after previous deselections are confirmed do we add new members.
    for identifier in before:
        if identifier not in wanted or identifier in current:
            continue
        session.send(A5_SELECT_PREFIX + identifier)
        logger("SELECT " + hex_data(identifier))
        current.add(identifier)
        await_selection(session, current, logger, "SELECT " + hex_data(identifier))

    after, _ = session.query_selection()
    verify_device_list(after)
    show_selection(after, logger, "AFTER")
    if selection_ids(after) != wanted:
        raise ValueError("Final selection mismatch; no lighting command sent")
    logger(f"Selection verified: exactly {len(wanted)} requested device(s).")


def isolate_target(session: HubSession, target: bytes, logger, disabled_ids=frozenset()) -> None:
    """Individual target through the same safe selection state machine."""
    select_members(session, (target,), logger, disabled_ids)


class Studio(tk.Tk):
    BG = "#0C1422"
    PANEL = "#19263A"
    TEXT = "#EDF3FC"
    MUTED = "#A7B9CE"
    ACCENT = "#5C84FF"

    def __init__(self):
        super().__init__()
        # Rebuild the registry on every new window; no duplicate IDs in tests/restarts.
        DEVICES[:] = BUILTIN_DEVICES
        self.extra_devices = read_registered_devices()
        DEVICES.extend(self.extra_devices)
        self.disabled_ids = read_disabled_ids()
        self.device_disable_vars = [tk.BooleanVar(value=dev[1] in self.disabled_ids)
                                    for dev in DEVICES]
        self.action_buttons = {}
        self.title("OOL Studio V6 — Device Exclusions + Groups + Scanner")
        self.geometry("1000x1050")
        self.minsize(850, 800)
        self.configure(bg=self.BG)
        self.host = tk.StringVar(value=HUB_IP)
        self.status = tk.StringVar(value="Ready. Nothing sent until you press a button.")
        self.busy = False
        self.stop_event = threading.Event()
        self.fx_start = tk.StringVar(value="#FF2827")
        self.fx_steps = tk.StringVar(value="8")
        self.fx_interval = tk.StringVar(value="2.0")
        self.volume_var = tk.IntVar(value=50)
        self.volume_label_var = tk.StringVar(value="50%")
        self.bluetooth_last = tk.StringVar(value="Last sent: unknown")
        self.volume_last = tk.StringVar(value="Last sent: unknown")
        self.audio_window = None
        self.groups_window = None
        self.scanner_window = None
        self.scanner_table = None
        self.scan_status = tk.StringVar(value="Not scanned yet. Click SCAN NOW to read paired devices.")
        self.scan_records = None
        self.scan_host = None
        self.scan_at = None
        self.groups = self.load_groups()
        self.group_name_var = tk.StringVar(value="")
        self.group_hex_var = tk.StringVar(value="#FF2827")
        self.group_members = [tk.BooleanVar(value=False) for _ in DEVICES]
        self.group_last_var = tk.StringVar(value="No group commands sent yet.")
        self.group_list = None
        self.active_group_name = None
        self.device_power_vars = [tk.StringVar(value="Power: unknown") for _ in DEVICES]
        self.last_bluetooth_command = None
        self.last_action = float("-inf")
        self.messages = queue.Queue()
        self.controls = []
        self.device_grid = None
        self.group_checks_frame = None
        self.device_vars = []
        self.name_vars = []
        self.swatches = []
        self._aliases = self.load_aliases()

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Dark.TEntry", fieldbackground="#101B2C", foreground=self.TEXT,
                        insertcolor=self.TEXT, padding=6)

        header = tk.Frame(self, bg=self.BG, padx=22, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="OOL STUDIO", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 23, "bold")).pack(anchor="w")
        tk.Label(header, text="Independent controls  •  device scanner  •  saved groups  •  projector audio  •  stopped by default",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 12))
        ipbar = tk.Frame(header, bg=self.BG)
        ipbar.pack(fill="x")
        self.lbl(ipbar, "Hub IP").pack(side="left")
        self.host_entry = ttk.Entry(ipbar, textvariable=self.host, width=19)
        self.host_entry.pack(side="left", padx=(9, 12))
        self.lbl(ipbar, ":8087", self.MUTED).pack(side="left")
        self.refresh_button = self.btn(ipbar, "SCAN DEVICES", self.open_scanner_panel)
        self.refresh_button.pack(side="right", padx=(8, 0))
        self.stop_button = self.btn(ipbar, "■ STOP ANIMATION", self.stop_effect, bg="#873A4A")
        self.stop_button.pack(side="right")
        self.controls.append(self.refresh_button)
        self.audio_button = self.btn(ipbar, "PROJECTOR AUDIO", self.open_audio_panel,
                                     bg="#5C588B")
        self.audio_button.pack(side="right", padx=(0, 8))
        self.groups_button = self.btn(ipbar, "GROUPS", self.open_groups_panel, bg="#267457")
        self.groups_button.pack(side="right", padx=(0, 8))
        self.ha_export_button = self.btn(ipbar, "EXPORT HA SETTINGS", self.export_ha_settings)
        self.ha_export_button.pack(side="right", padx=(0, 8))


        body = tk.Frame(self, bg=self.BG, padx=22)
        body.pack(fill="both", expand=True)
        self.device_canvas = tk.Canvas(body, bg=self.BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.device_canvas.yview)
        self.device_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.device_canvas.pack(side="left", fill="both", expand=True)
        self.device_grid = tk.Frame(self.device_canvas, bg=self.BG)
        self.device_grid.grid_columnconfigure(0, weight=1, uniform="cards")
        self.device_grid.grid_columnconfigure(1, weight=1, uniform="cards")
        canvas_item = self.device_canvas.create_window((0, 0), window=self.device_grid, anchor="nw")
        self.device_grid.bind("<Configure>", lambda _evt:
                              self.device_canvas.configure(scrollregion=self.device_canvas.bbox("all")))
        self.device_canvas.bind("<Configure>", lambda evt:
                                self.device_canvas.itemconfigure(canvas_item, width=evt.width))
        self.bind_all("<MouseWheel>", self.scroll_devices, add="+")
        for idx in range(len(DEVICES)):
            self.add_device_card(idx)

        fxbar = tk.Frame(self, bg=self.BG, padx=22, pady=7)
        fxbar.pack(fill="x")
        self.lbl(fxbar, "Fade start HEX").pack(side="left")
        ttk.Entry(fxbar, textvariable=self.fx_start, width=11).pack(side="left", padx=(8, 16))
        self.lbl(fxbar, "Frames 2–20").pack(side="left")
        ttk.Entry(fxbar, textvariable=self.fx_steps, width=5).pack(side="left", padx=(8, 16))
        self.lbl(fxbar, "Seconds/frame ≥1.5").pack(side="left")
        ttk.Entry(fxbar, textvariable=self.fx_interval, width=6).pack(side="left", padx=(8, 0))

        foot = tk.Frame(self, bg=self.BG, padx=22, pady=15)
        foot.pack(fill="both", expand=False)
        row = tk.Frame(foot, bg=self.BG)
        row.pack(fill="x")
        self.lbl(row, "Rename devices above, then save labels locally.", self.MUTED).pack(side="left")
        self.btn(row, "SAVE LABELS", self.save_aliases).pack(side="right")
        tk.Label(foot, textvariable=self.status, bg=self.BG, fg="#ADD0FF",
                 anchor="w", justify="left", wraplength=840,
                 font=("Segoe UI", 10)).pack(fill="x", pady=(12, 6))
        self.output = tk.Text(foot, height=8, bg="#0B1120", fg="#B9F6D4",
                              font=("Consolas", 9), state="disabled", wrap="word")
        self.output.pack(fill="x")
        self.log("Nothing sent automatically. Close the iPhone OOL app before using Windows.")
        self.log("Device names other than the projector remain provisional until you identify them physically.")
        self.log("Targeting uses A5 status verification on EVERY action, including animations.")
        self.log("Effects target ONE device at a time, are finite, and start only when clicked.")
        self.log("A6 09 power is selection-scoped: individual devices and groups now supported.")
        self.log("Each A5 change is verified before the next selection mutation.")
        self.log("V5: unexpected A5 values show RAW status and trigger read-only status rechecks (fail-closed).")
        self.log("Groups select all members together; a group command is sent ONCE.")
        self.log("Projector volume isolates projector before sending; slider does not auto-send.")
        self.log("Power/Bluetooth/volume show the last SEND, not live device readback.")
        self.log("SCAN DEVICES is read-only; ADD SELECTED registers a new device locally (no hub commands).")
        self.log("DISABLE excludes a light from Studio commands/groups, but cannot deselect it on the hub.")
        self.log("If a disabled light remains hub-selected, commands stop instead of affecting that light.")
        self.after(75, self.poll)

    def scroll_devices(self, event):
        """Scroll the device cards without hijacking the scanner/group windows."""
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget is self.device_canvas:
                if event.delta:
                    steps = max(1, round(abs(event.delta) / 120))
                    self.device_canvas.yview_scroll(-steps if event.delta > 0 else steps, "units")
                return
            widget = widget.master

    def add_device_card(self, idx):
        default_name, id_text, color = DEVICES[idx]
        dev = tk.Frame(self.device_grid, bg=self.PANEL, padx=17, pady=14,
                       highlightthickness=1, highlightbackground="#31415A")
        dev.grid(row=idx // 2, column=idx % 2, sticky="nsew", padx=6, pady=6)
        name = tk.StringVar(value=self._aliases.get(id_text, default_name))
        self.name_vars.append(name)
        top = tk.Frame(dev, bg=self.PANEL)
        top.pack(fill="x")
        tk.Label(top, text=f"DEVICE {idx + 1}", bg=self.PANEL, fg="#8CAEFF",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        namefield = tk.Entry(top, textvariable=name, bg="#122037", fg=self.TEXT,
                             insertbackground=self.TEXT, relief="flat",
                             font=("Segoe UI", 12, "bold"), width=27)
        namefield.pack(fill="x", pady=(4, 8), ipady=5)
        tk.Label(dev, text=id_text, bg=self.PANEL, fg=self.MUTED,
                 font=("Consolas", 9)).pack(anchor="w", pady=(0, 10))

        col = tk.Frame(dev, bg=self.PANEL)
        col.pack(fill="x", pady=(0, 10))
        preview = tk.Frame(col, bg=color, height=52, width=52, highlightthickness=1,
                           highlightbackground="#67748B")
        preview.pack(side="left", padx=(0, 13))
        preview.pack_propagate(False)
        color_var = tk.StringVar(value=color)
        self.device_vars.append(color_var)
        self.swatches.append(preview)
        field = ttk.Entry(col, textvariable=color_var, width=11)
        field.pack(side="left", fill="x", expand=True)
        field.bind("<Return>", lambda _evt, i=idx: self.sync_swatch(i))
        choose = self.btn(col, "PICK", lambda i=idx: self.choose(i))
        choose.pack(side="left", padx=(7, 0))
        self.controls.append(choose)

        send = self.btn(dev, "SEND COLOR TO THIS DEVICE", lambda i=idx: self.send_color(i),
                        bg=self.ACCENT)
        send.pack(fill="x", ipady=3)
        self.controls.append(send)
        powerline = tk.Frame(dev, bg=self.PANEL)
        powerline.pack(fill="x", pady=(7, 0))
        on_btn = self.btn(powerline, "ON", lambda i=idx: self.power_device(i, True), bg="#267457")
        off_btn = self.btn(powerline, "OFF", lambda i=idx: self.power_device(i, False), bg="#854557")
        on_btn.pack(side="left", fill="x", expand=True, padx=(0, 4))
        off_btn.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.controls.extend([on_btn, off_btn])
        tk.Label(dev, textvariable=self.device_power_vars[idx], bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        fxbuttons = tk.Frame(dev, bg=self.PANEL)
        fxbuttons.pack(fill="x", pady=(7, 0))
        fade = self.btn(fxbuttons, "▶ FADE HERE", lambda i=idx: self.start_effect(i, "fade"), bg="#3A597A")
        fade.pack(side="left", fill="x", expand=True, padx=(0, 4))
        rain = self.btn(fxbuttons, "▶ RAINBOW HERE", lambda i=idx: self.start_effect(i, "rainbow"), bg="#614C87")
        rain.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.controls.extend([fade, rain])
        self.action_buttons[idx] = (send, on_btn, off_btn, fade, rain)
        disabled_row = tk.Frame(dev, bg=self.PANEL)
        disabled_row.pack(fill="x", pady=(9, 0))
        tk.Checkbutton(
            disabled_row, text="Disable device (keep saved)",
            variable=self.device_disable_vars[idx],
            command=lambda i=idx: self.toggle_disabled(i),
            bg=self.PANEL, fg="#FFD09A", selectcolor="#122037",
            activebackground=self.PANEL, activeforeground="#FFD09A",
            highlightthickness=0, font=("Segoe UI", 9, "bold")
        ).pack(side="left")
        self.apply_disabled_buttons()
        return dev

    def open_scanner_panel(self):
        """A5 device-list viewer; scanning never selects or changes a decoration."""
        if self.scanner_window is not None and self.scanner_window.winfo_exists():
            self.scanner_window.lift()
            self.scanner_window.focus_force()
            return
        window = tk.Toplevel(self)
        self.scanner_window = window
        window.title("OOL Studio — Scan & Add Devices")
        window.geometry("910x540")
        window.minsize(760, 450)
        window.configure(bg=self.BG)
        window.transient(self)

        header = tk.Frame(window, bg=self.BG, padx=20, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="PAIRED DEVICE SCANNER", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(header, text="Read-only A5 status query • no discovery broadcasts, pairing, or light changes",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(4, 0))

        frame = tk.Frame(window, bg=self.PANEL, padx=16, pady=14)
        frame.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        style = ttk.Style(self)
        style.configure("OOLScan.Treeview", background="#101B2C", fieldbackground="#101B2C",
                        foreground=self.TEXT, rowheight=32, borderwidth=0, font=("Segoe UI", 10))
        style.configure("OOLScan.Treeview.Heading", background="#2A3B56",
                        foreground=self.TEXT, font=("Segoe UI", 10, "bold"))
        style.map("OOLScan.Treeview", background=[("selected", self.ACCENT)],
                  foreground=[("selected", "white")])
        columns = ("name", "id", "selection", "mapping")
        table = ttk.Treeview(frame, columns=columns, show="headings", height=9,
                             selectmode="browse", style="OOLScan.Treeview")
        self.scanner_table = table
        for col, caption, width in (("name", "Device name", 240),
                                    ("id", "6-byte ID", 210),
                                    ("selection", "Selected in hub", 150),
                                    ("mapping", "Mapping", 125)):
            table.heading(col, text=caption)
            table.column(col, width=width, anchor="w", stretch=col == "name")
        table.pack(fill="both", expand=True)
        table.tag_configure("unknown", foreground="#FFCE7A")
        row = tk.Frame(frame, bg=self.PANEL)
        row.pack(fill="x", pady=(14, 0))
        scan = self.btn(row, "SCAN NOW", self.scan_devices, bg="#267457")
        scan.pack(side="left")
        self.controls.append(scan)
        self.btn(row, "ADD SELECTED TO STUDIO", self.register_scanned_device,
                 bg="#267457").pack(side="left", padx=(8, 0))
        self.btn(row, "COPY ID", self.copy_scanned_id).pack(side="left", padx=(8, 0))
        self.btn(row, "EXPORT JSON", self.export_scanned_devices).pack(side="right")

        tk.Label(window, textvariable=self.scan_status, bg=self.BG, fg="#ADD0FF",
                 justify="left", anchor="w", wraplength=840,
                 font=("Segoe UI", 10)).pack(fill="x", padx=22, pady=(0, 6))
        tk.Label(window, text="Shows the hub's registered/paired device records. Selected is NOT the same as powered on.\n"
                 "Select an unknown ID, then click ADD SELECTED TO STUDIO. This saves it locally without changing any lights.",
                 bg=self.BG, fg=self.MUTED, justify="left", anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x", padx=22, pady=(0, 17))
        self.render_scan_table()

    def scan_devices(self):
        if self.busy:
            self.scan_status.set("Another operation is running; try again after it finishes.")
            return
        left = MIN_ACTION_INTERVAL - (time.monotonic() - self.last_action)
        if left > 0:
            self.scan_status.set(f"Wait {left:.1f}s before scanning again.")
            return
        try:
            ipaddress.IPv4Address(self.host.get().strip())
        except ipaddress.AddressValueError:
            self.scan_status.set("Enter a valid hub IPv4 address in the main window.")
            return
        self.scan_status.set("Querying paired device list (read-only)...")
        self.launch("scan")

    def render_scan_table(self):
        table = self.scanner_table
        if table is None or not table.winfo_exists():
            return
        table.delete(*table.get_children())
        if not self.scan_records:
            return
        known = {ident.upper(): self.name_vars[i].get().strip() or DEVICES[i][0]
                 for i, (_, ident, _) in enumerate(DEVICES)}
        unknown_count = 0
        for ident, marker in self.scan_records.items():
            dev_id = hex_data(ident)
            mapped = dev_id in known
            if not mapped:
                unknown_count += 1
            name = known.get(dev_id, f"Unknown device {unknown_count}")
            selection = "Not selected" if marker == b"\xff\x00" else f"Selected (slot {marker[0]})"
            table.insert("", "end", values=(name, dev_id, selection,
                                              "Known ID" if mapped else "New / unknown"),
                         tags=() if mapped else ("unknown",))

    def register_scanned_device(self):
        """User-approved registration; no network packets, live UI update."""
        if self.busy:
            self.scan_status.set("Finish the current hub operation before adding a device.")
            return
        table = self.scanner_table
        if table is None or not table.winfo_exists() or not table.selection():
            self.scan_status.set("Select the new device in the scanner table first.")
            return
        if self.scan_records is None:
            self.scan_status.set("Run SCAN NOW before adding a device.")
            return
        if self.scan_host != self.host.get().strip():
            self.scan_status.set("Hub address changed; scan again before registering.")
            return
        id_text = table.item(table.selection()[0], "values")[1]
        try:
            identifier = bytes.fromhex(id_text)
        except (ValueError, TypeError):
            self.scan_status.set("Invalid device ID in selected row.")
            return
        if identifier not in self.scan_records or len(identifier) != 6:
            self.scan_status.set("That device was not in the last verified scan.")
            return
        if identifier in {bytes.fromhex(dev[1]) for dev in DEVICES}:
            self.scan_status.set("That device is already registered in OOL Studio.")
            return
        idx = len(DEVICES)
        existing_ids = {dev[1] for dev in DEVICES}
        default_all_group = set(self.groups.get("All Lights", [])) == existing_ids
        name = f"New OOL Light {idx + 1}"
        color = "#66D9CB"
        entry = (name, hex_data(identifier), color)
        updated = self.extra_devices + [entry]
        try:
            write_registered_devices(updated)
        except OSError as error:
            self.scan_status.set("Couldn't save new device: " + str(error))
            return
        self.extra_devices = updated
        DEVICES.append(entry)
        self.group_members.append(tk.BooleanVar(value=False))
        self.device_disable_vars.append(tk.BooleanVar(value=False))
        self.device_power_vars.append(tk.StringVar(value="Power: unknown"))
        card = self.add_device_card(idx)
        self.add_group_checkbox(idx)
        if default_all_group:
            # Auto-extend only a true all-devices group. Preserve custom subsets.
            self.groups["All Lights"].append(entry[1])
            try:
                self.persist_groups()
            except OSError as error:
                self.log("Warning: couldn't save updated All Lights group: " + str(error))
            if self.active_group_name == "All Lights":
                self.group_members[idx].set(True)
        self.update_idletasks()
        height = max(1, self.device_grid.winfo_height())
        self.device_canvas.yview_moveto(min(1.0, card.winfo_y() / height))
        self.render_scan_table()
        self.scan_status.set(f"Added {name} ({entry[1]}). Rename it in the main window, "
                             "click SAVE LABELS, and add it to groups as desired.")
        self.log(f"REGISTERED {entry[1]} as '{name}' — saved locally, no hub command sent.")
        self.status.set(f"{name} added to device controls. Scroll down to find it.")

    def copy_scanned_id(self):
        table = self.scanner_table
        if table is None or not table.winfo_exists() or not table.selection():
            self.scan_status.set("Select a device in the table first.")
            return
        dev_id = table.item(table.selection()[0], "values")[1]
        self.clipboard_clear()
        self.clipboard_append(dev_id)
        self.scan_status.set(f"Copied device ID {dev_id}.")

    def export_scanned_devices(self):
        if self.scan_records is None:
            self.scan_status.set("Scan the hub before exporting.")
            return
        destination = filedialog.asksaveasfilename(
            parent=self.scanner_window, title="Export OOL paired devices",
            initialfile="ool_scanned_devices.json", defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not destination:
            return
        known = {ident.upper(): self.name_vars[i].get().strip() or DEVICES[i][0]
                 for i, (_, ident, _) in enumerate(DEVICES)}
        data = {
            "hub_ip": self.scan_host,
            "queried_at_local": self.scan_at,
            "read_only": True,
            "note": "Hub paired-device list; selection does not indicate power or online status.",
            "devices": [
                {"id": hex_data(ident), "name": known.get(hex_data(ident)),
                 "known_to_studio": hex_data(ident) in known,
                 "selected": marker != b"\xff\x00",
                 "selection_slot": marker[0] if marker != b"\xff\x00" else None}
                for ident, marker in self.scan_records.items()
            ],
        }
        try:
            Path(destination).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError as err:
            self.scan_status.set("Export failed: " + str(err))
            return
        self.scan_status.set("Exported " + str(len(self.scan_records)) + " device IDs to " + destination)

    def open_audio_panel(self):
        if self.audio_window is not None and self.audio_window.winfo_exists():
            self.audio_window.lift()
            self.audio_window.focus_force()
            return
        window = tk.Toplevel(self)
        self.audio_window = window
        window.title("OOL Studio — Projector Bluetooth + Volume")
        window.geometry("570x375")
        window.minsize(500, 350)
        window.configure(bg=self.BG)
        window.transient(self)
        projector = tk.Frame(window, bg=self.PANEL, padx=20, pady=16)
        projector.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(projector, text="PROJECTOR  ·  BLUETOOTH + VOLUME", bg=self.PANEL,
                 fg="#8CAEFF", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(projector, textvariable=self.bluetooth_last, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 7))
        bt_buttons = tk.Frame(projector, bg=self.PANEL)
        bt_buttons.pack(fill="x")
        bt_toggle = self.btn(bt_buttons, "TOGGLE BT", self.toggle_bluetooth, bg="#4D639A")
        bt_on = self.btn(bt_buttons, "BLUETOOTH ON", lambda: self.launch("bluetooth", True), bg="#267457")
        bt_off = self.btn(bt_buttons, "BLUETOOTH OFF", lambda: self.launch("bluetooth", False), bg="#854557")
        for btn in (bt_toggle, bt_on, bt_off):
            btn.pack(side="left", fill="x", expand=True, padx=4)
            self.controls.append(btn)
        volume_row = tk.Frame(projector, bg=self.PANEL)
        volume_row.pack(fill="x", pady=(14, 0))
        tk.Label(volume_row, text="VOLUME", bg=self.PANEL, fg=self.TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(volume_row, textvariable=self.volume_label_var, bg=self.PANEL, fg=self.TEXT,
                 font=("Segoe UI", 9)).pack(side="right")
        tk.Scale(projector, from_=0, to=100, orient="horizontal", variable=self.volume_var,
                 command=lambda v: self.volume_label_var.set(f"{int(float(v))}%"),
                 showvalue=False, bg=self.PANEL, fg=self.TEXT,
                 highlightthickness=0, troughcolor="#253751", activebackground="#86A7FF",
                 sliderlength=16, relief="flat").pack(fill="x")
        apply_btn = self.btn(projector, "APPLY PROJECTOR VOLUME", self.apply_volume, bg="#5C588B")
        apply_btn.pack(fill="x")
        self.controls.append(apply_btn)
        tk.Label(projector, textvariable=self.volume_last, bg=self.PANEL, fg=self.MUTED,
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 0))
        tk.Label(projector, text="Projector-only selection is verified before every command.\n"
                 "Displays last sent command, not live projector status.",
                 bg=self.PANEL, fg=self.MUTED, justify="left", anchor="w",
                 font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 0))

    def apply_disabled_buttons(self):
        for i, buttons in self.action_buttons.items():
            is_disabled = DEVICES[i][1] in self.disabled_ids
            for control in buttons:
                if control.winfo_exists():
                    control.configure(state="disabled" if is_disabled or self.busy else "normal")

    def toggle_disabled(self, idx):
        ident = DEVICES[idx][1]
        desired = self.device_disable_vars[idx].get()
        if self.busy:
            self.device_disable_vars[idx].set(ident in self.disabled_ids)
            self.status.set("Finish the current hub action before changing disabled devices.")
            return
        updated = set(self.disabled_ids)
        if desired:
            updated.add(ident)
        else:
            updated.discard(ident)
        try:
            write_disabled_ids(updated)
        except OSError as exc:
            self.device_disable_vars[idx].set(ident in self.disabled_ids)
            self.status.set("Couldn't save disabled devices: " + str(exc))
            return
        self.disabled_ids = updated
        self.apply_disabled_buttons()
        self.log(("DISABLED " if desired else "RE-ENABLED ") + ident +
                 " — no hub command was sent")
        self.status.set("Device " + ("disabled" if desired else "enabled") +
                        " in Studio. Hub selection is unchanged.")

    def export_ha_settings(self):
        # Export names/IDs/groups, not passwords, tokens or network configuration.
        entries = [{"id": ident, "name": self.name_vars[i].get().strip() or default,
                    "enabled": ident not in self.disabled_ids}
                   for i, (default, ident, _) in enumerate(DEVICES)]
        payload = {"devices": entries,
                   "groups": [{"name": name, "members": members}
                              for name, members in self.groups.items()]}
        try:
            HA_EXPORT_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            self.status.set("HA export failed: " + str(exc))
            return
        self.status.set("Exported " + HA_EXPORT_FILE.name + " for the HA custom integration.")
        self.log("HA EXPORT " + str(HA_EXPORT_FILE) + " — no hub commands")

    def load_groups(self):
        """Groups store immutable device identifiers; labels may be renamed."""
        allowed = {id_text.upper() for _, id_text, _ in DEVICES}
        defaults = {
            "All Lights": [id_text for _, id_text, _ in DEVICES],
            "Both Net Lights": [DEVICES[0][1], DEVICES[1][1]],
        }
        try:
            data = json.loads(GROUP_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return defaults
            clean = {}
            for name, members in data.items():
                if not isinstance(name, str) or not name.strip() or len(name) > 60:
                    continue
                if not isinstance(members, list):
                    continue
                choices = []
                for raw in members:
                    if isinstance(raw, str) and raw.upper() in allowed and raw.upper() not in choices:
                        choices.append(raw.upper())
                if choices:
                    clean[name] = choices
            return clean
        except FileNotFoundError:
            return defaults
        except (OSError, ValueError):
            return defaults

    def persist_groups(self):
        GROUP_FILE.write_text(json.dumps(self.groups, indent=2) + "\n", encoding="utf-8")

    def open_groups_panel(self):
        if self.groups_window is not None and self.groups_window.winfo_exists():
            self.groups_window.lift()
            self.groups_window.focus_force()
            return
        window = tk.Toplevel(self)
        self.groups_window = window
        window.title("OOL Studio — Custom Device Groups")
        window.geometry("845x590")
        window.minsize(720, 520)
        window.configure(bg=self.BG)
        window.transient(self)
        top = tk.Frame(window, bg=self.BG, padx=18, pady=13)
        top.pack(fill="x")
        tk.Label(top, text="CUSTOM DEVICE GROUPS", bg=self.BG, fg=self.TEXT,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w")
        tk.Label(top, text="Make your own sets, e.g. Both Net Lights or Outdoor. Power/color actions target ONLY group members.",
                 bg=self.BG, fg=self.MUTED, font=("Segoe UI", 9)).pack(anchor="w")
        main = tk.Frame(window, bg=self.BG, padx=16)
        main.pack(fill="both", expand=True)
        left = tk.Frame(main, bg=self.PANEL, padx=13, pady=13)
        left.pack(side="left", fill="both", padx=4)
        tk.Label(left, text="SAVED GROUPS", bg=self.PANEL, fg="#8CAEFF",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.group_list = tk.Listbox(left, height=14, width=25, bg="#122037", fg=self.TEXT,
                                     selectbackground=self.ACCENT, selectforeground="white",
                                     bd=0, highlightthickness=0, exportselection=False,
                                     font=("Segoe UI", 10))
        self.group_list.pack(fill="both", expand=True)
        self.group_list.bind("<<ListboxSelect>>", self.on_group_selected)
        self.btn(left, "+ NEW GROUP", self.new_group, bg="#4D639A").pack(fill="x", pady=(9, 0))
        right = tk.Frame(main, bg=self.PANEL, padx=17, pady=13)
        right.pack(side="left", fill="both", expand=True, padx=(10, 4))
        tk.Label(right, text="NAME", bg=self.PANEL, fg="#8CAEFF",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Entry(right, textvariable=self.group_name_var, bg="#122037", fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", font=("Segoe UI", 12)).pack(fill="x", pady=(5, 12), ipady=5)
        tk.Label(right, text="DEVICES IN THIS GROUP", bg=self.PANEL, fg="#8CAEFF",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.group_checks_frame = tk.Frame(right, bg=self.PANEL)
        self.group_checks_frame.pack(fill="x")
        for i in range(len(DEVICES)):
            self.add_group_checkbox(i)
        editrow = tk.Frame(right, bg=self.PANEL)
        editrow.pack(fill="x", pady=(13, 10))
        save = self.btn(editrow, "SAVE GROUP", self.save_group, bg="#267457")
        delete = self.btn(editrow, "DELETE", self.delete_group, bg="#854557")
        save.pack(side="left", fill="x", expand=True, padx=(0, 4))
        delete.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.controls.extend([save, delete])
        tk.Label(right, text="GROUP CONTROLS", bg=self.PANEL, fg="#8CAEFF",
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 7))
        actions = tk.Frame(right, bg=self.PANEL)
        actions.pack(fill="x")
        on = self.btn(actions, "GROUP ON", lambda: self.send_group_power(True), bg="#267457")
        off = self.btn(actions, "GROUP OFF", lambda: self.send_group_power(False), bg="#854557")
        on.pack(side="left", fill="x", expand=True, padx=(0, 4))
        off.pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.controls.extend([on, off])
        colorrow = tk.Frame(right, bg=self.PANEL)
        colorrow.pack(fill="x", pady=(8, 0))
        tk.Entry(colorrow, textvariable=self.group_hex_var, bg="#122037", fg=self.TEXT,
                 insertbackground=self.TEXT, relief="flat", width=12,
                 font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=6)
        pick = self.btn(colorrow, "PICK", self.pick_group_color)
        pick.pack(side="left", padx=(6, 0))
        send = self.btn(right, "SEND COLOR TO GROUP", self.send_group_color, bg=self.ACCENT)
        send.pack(fill="x", pady=(8, 0))
        self.controls.extend([pick, send])
        tk.Label(window, textvariable=self.group_last_var, bg=self.BG, fg="#ADD0FF",
                 justify="left", anchor="w", wraplength=790,
                 font=("Segoe UI", 9)).pack(fill="x", padx=22, pady=(6, 0))
        tk.Label(window, text="Group members selected together and verified before ONE shared command.\n"
                 "Save membership before sending. The hub keeps the selected group afterward.\n"
                 "Nothing runs on startup.",
                 bg=self.BG, fg=self.MUTED, justify="left", anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x", padx=22, pady=(4, 12))
        self.reload_group_list()
        if self.groups:
            self.group_list.selection_set(0)
            self.on_group_selected()
        else:
            self.new_group()

    def add_group_checkbox(self, i):
        if self.group_checks_frame is None or not self.group_checks_frame.winfo_exists():
            return
        line = tk.Frame(self.group_checks_frame, bg=self.PANEL)
        line.pack(fill="x", pady=(4, 0))
        tk.Checkbutton(line, variable=self.group_members[i], bg=self.PANEL, fg=self.TEXT,
                       activebackground=self.PANEL, activeforeground=self.TEXT,
                       selectcolor="#122037", highlightthickness=0,
                       textvariable=self.name_vars[i], font=("Segoe UI", 10)).pack(side="left")

    def reload_group_list(self, select_name=None):
        if not self.group_list or not self.group_list.winfo_exists():
            return
        names = list(self.groups)
        self.group_list.delete(0, "end")
        for name in names:
            self.group_list.insert("end", name)
        if select_name in names:
            idx = names.index(select_name)
            self.group_list.selection_set(idx)
            self.group_list.see(idx)

    def on_group_selected(self, _event=None):
        if not self.group_list:
            return
        indexes = self.group_list.curselection()
        if not indexes:
            return
        name = self.group_list.get(indexes[0])
        members = set(self.groups.get(name, []))
        self.active_group_name = name
        self.group_name_var.set(name)
        for i, (_, ident, _) in enumerate(DEVICES):
            self.group_members[i].set(ident.upper() in members)

    def new_group(self):
        if self.group_list is not None:
            self.group_list.selection_clear(0, "end")
        self.active_group_name = None
        self.group_name_var.set("")
        for flag in self.group_members:
            flag.set(False)
        self.group_last_var.set("New group: name it, choose devices, then SAVE GROUP.")

    def save_group(self):
        if self.busy:
            self.status.set("Finish the current command before changing groups.")
            return
        name = self.group_name_var.get().strip()
        members = [ident for i, (_, ident, _) in enumerate(DEVICES) if self.group_members[i].get()]
        if not name or len(name) > 60:
            self.group_last_var.set("Group name must be 1–60 characters.")
            return
        if not members:
            self.group_last_var.set("Select at least one device.")
            return
        previous = self.active_group_name
        if name in self.groups and name != previous:
            self.group_last_var.set(f"'{name}' already exists. Select it or use another name.")
            return
        original = self.groups.copy()
        if previous is not None and previous != name:
            self.groups.pop(previous, None)
        self.groups[name] = members
        try:
            self.persist_groups()
        except OSError as error:
            self.groups = original
            self.group_last_var.set("Save failed: " + str(error))
            return
        self.active_group_name = name
        self.reload_group_list(name)
        self.group_last_var.set(f"Saved '{name}' with {len(members)} device(s).")
        self.log(f"Saved group {name} ({len(members)} devices)")

    def delete_group(self):
        if self.busy:
            self.status.set("Finish the current command before changing groups.")
            return
        name = self.active_group_name
        if not name or name not in self.groups:
            self.group_last_var.set("Select a saved group before deleting.")
            return
        if not messagebox.askyesno("Delete group", f"Delete saved group '{name}'?\nLights will not be changed.",
                                   parent=self.groups_window):
            return
        old = self.groups.copy()
        self.groups.pop(name)
        try:
            self.persist_groups()
        except OSError as error:
            self.groups = old
            self.group_last_var.set("Delete failed: " + str(error))
            return
        self.new_group()
        self.reload_group_list()
        self.group_last_var.set("Deleted group '" + name + "'. No network commands sent.")

    def pick_group_color(self):
        picked = colorchooser.askcolor(color=self.group_hex_var.get(), parent=self.groups_window)
        if picked and picked[1]:
            self.group_hex_var.set(picked[1].upper())

    def group_members_to_send(self):
        """Actions target SAVED group membership, not uncommitted checkbox changes."""
        name = self.active_group_name
        if not name or name not in self.groups:
            self.group_last_var.set("Choose a saved group first (and SAVE any edits).")
            return None
        if (name != self.group_name_var.get().strip()
            or set(ident for i, (_, ident, _) in enumerate(DEVICES) if self.group_members[i].get())
               != set(self.groups[name])):
            self.group_last_var.set("Unsaved group changes — click SAVE GROUP first.")
            return None
        active = [ident for ident in self.groups[name] if ident not in self.disabled_ids]
        skipped = len(self.groups[name]) - len(active)
        if not active:
            self.group_last_var.set("Every member is disabled; nothing sent.")
            return None
        if skipped:
            self.group_last_var.set(f"{skipped} disabled member(s) excluded from this command.")
        return name, tuple(bytes.fromhex(ident) for ident in active)

    def send_group_power(self, on):
        target = self.group_members_to_send()
        if target:
            name, members = target
            self.launch("group_power", members, on, name)

    def send_group_color(self):
        target = self.group_members_to_send()
        if not target:
            return
        try:
            color = parse_color(self.group_hex_var.get())
        except ValueError as err:
            self.group_last_var.set(str(err))
            return
        name, members = target
        self.launch("group_color", members, color, name)

    def power_device(self, index, on):
        if DEVICES[index][1] in self.disabled_ids:
            self.status.set("That device is disabled. Re-enable it to send commands.")
            return
        self.launch("power", bytes.fromhex(DEVICES[index][1]), on, self.name_vars[index].get())

    def lbl(self, parent, text, fg=None):
        return tk.Label(parent, text=text, bg=self.BG, fg=fg or self.TEXT,
                        font=("Segoe UI", 10))

    def btn(self, parent, text, command, bg=None):
        return tk.Button(parent, text=text, command=command, bg=bg or "#2A3B56",
                         fg="white", activebackground="#3B5878",
                         activeforeground="white", font=("Segoe UI", 9, "bold"),
                         relief="flat", bd=0, padx=11, pady=8, cursor="hand2")

    def load_aliases(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except (OSError, ValueError):
            pass
        return {}

    def save_aliases(self):
        data = {id_text: var.get().strip()[:60] for var, (_, id_text, _) in zip(self.name_vars, DEVICES)}
        try:
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.status.set("Saved labels to " + CONFIG_FILE.name)
        except OSError as exc:
            self.status.set("Couldn't save labels: " + str(exc))

    def log(self, message):
        self.output.configure(state="normal")
        self.output.insert("end", time.strftime("%H:%M:%S") + "  " + message + "\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def poll(self):
        try:
            while True:
                kind, text = self.messages.get_nowait()
                if kind == "log":
                    self.log(text)
                elif kind == "scan_error":
                    self.scan_status.set(text)
                elif kind == "scan_done":
                    host, records = text
                    self.scan_records = records
                    self.scan_host = host
                    self.scan_at = time.strftime("%Y-%m-%d %H:%M:%S")
                    self.render_scan_table()
                    unknown = len(set(records) - {bytes.fromhex(dev[1]) for dev in DEVICES})
                    self.scan_status.set(
                        f"Found {len(records)} paired device(s) on {host}; "
                        f"{len(selection_ids(records))} selected, {unknown} unknown to Studio. "
                        f"Scanned {self.scan_at}."
                    )
                elif kind == "power_done":
                    idx, on = text
                    self.device_power_vars[idx].set("Last sent: " + ("ON" if on else "OFF") + " (not read back)")
                elif kind == "group_done":
                    self.group_last_var.set(text)
                elif kind == "bluetooth_done":
                    self.last_bluetooth_command = text
                    self.bluetooth_last.set("Last sent: " + ("ON" if text else "OFF") + " (not read back)")
                elif kind == "volume_done":
                    self.volume_last.set(f"Last sent: {text}% (not read back)")
                elif kind == "done":
                    self.status.set(text)
                    self.busy = False
                    self.host_entry.configure(state="normal")
                    self.controls = [ctrl for ctrl in self.controls if ctrl.winfo_exists()]
                    for ctrl in self.controls:
                        ctrl.configure(state="normal")
                    self.apply_disabled_buttons()
        except queue.Empty:
            pass
        self.after(75, self.poll)

    def sync_swatch(self, i):
        try:
            color = self.device_vars[i].get().strip().upper()
            parse_color(color)
            self.device_vars[i].set("#" + color.lstrip("#"))
            self.swatches[i].configure(bg=self.device_vars[i].get())
            self.status.set("Preview updated — not yet sent.")
        except (ValueError, tk.TclError) as exc:
            self.status.set("Invalid color: " + str(exc))

    def choose(self, i):
        picked = colorchooser.askcolor(color=self.device_vars[i].get(), parent=self,
                                       title="Choose color for " + self.name_vars[i].get())
        if picked and picked[1]:
            self.device_vars[i].set(picked[1].upper())
            self.sync_swatch(i)

    def launch(self, work, *args):
        if work in ("bluetooth", "volume") and hex_data(PROJECTOR_ID) in self.disabled_ids:
            self.status.set("Projector is disabled; audio command not sent.")
            return
        if self.busy:
            return
        left = MIN_ACTION_INTERVAL - (time.monotonic() - self.last_action)
        if left > 0:
            self.status.set(f"Cooldown: {left:.1f}s before another action.")
            return
        try:
            host = str(ipaddress.IPv4Address(self.host.get().strip()))
        except ipaddress.AddressValueError:
            self.status.set("Enter a valid IPv4 address")
            return
        self.busy = True
        self.stop_event.clear()
        self.last_action = time.monotonic()
        self.host_entry.configure(state="disabled")
        self.controls = [ctrl for ctrl in self.controls if ctrl.winfo_exists()]
        for ctrl in self.controls:
            ctrl.configure(state="disabled")
        self.status.set("Working on one controlled operation...")
        names_snapshot = {bytes.fromhex(dev[1]): self.name_vars[i].get()
                          for i, dev in enumerate(DEVICES)}
        disabled_snapshot = frozenset(bytes.fromhex(x) for x in self.disabled_ids)
        threading.Thread(target=self.worker,
                         args=(host, work, args, names_snapshot, disabled_snapshot), daemon=True).start()

    def refresh(self):
        self.launch("refresh")

    def apply_volume(self):
        self.launch("volume", self.volume_var.get())

    def toggle_bluetooth(self):
        # If we have not sent a Bluetooth command yet, toggle begins with ON.
        # The hub's actual Bluetooth state is not independently verified.
        desired = True if self.last_bluetooth_command is None else not self.last_bluetooth_command
        self.launch("bluetooth", desired)

    def send_color(self, i):
        if DEVICES[i][1] in self.disabled_ids:
            self.status.set("That device is disabled; no color sent.")
            return
        self.sync_swatch(i)
        try:
            rgb = parse_color(self.device_vars[i].get())
        except ValueError:
            return
        self.launch("send", bytes.fromhex(DEVICES[i][1]), rgb, self.name_vars[i].get())


    def stop_effect(self):
        if not self.busy:
            self.status.set("Nothing running — effects are stopped by default.")
            return
        self.stop_event.set()
        self.status.set("STOP requested. Current network operation may finish before stopping.")

    def start_effect(self, i, kind):
        if DEVICES[i][1] in self.disabled_ids:
            self.status.set("That device is disabled; no animation started.")
            return
        if self.busy:
            return
        try:
            steps = int(self.fx_steps.get())
            interval = float(self.fx_interval.get())
            if not 2 <= steps <= MAX_EFFECT_FRAMES:
                raise ValueError("Frames must be between 2 and 20")
            if not MIN_EFFECT_INTERVAL <= interval <= 10:
                raise ValueError("Seconds/frame must be 1.5 to 10")
            if kind == "fade":
                start = parse_color(self.fx_start.get())
                end = parse_color(self.device_vars[i].get())
                frames = make_fade(start, end, steps)
            elif kind == "rainbow":
                frames = make_rainbow(steps)
            else:
                raise ValueError("Unknown effect")
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.launch("effect", bytes.fromhex(DEVICES[i][1]), frames, interval,
                    self.name_vars[i].get(), kind)

    def worker(self, host, kind, arguments, names_by_id, disabled_ids):
        try:
            with HubSession(host) as hub:
                self.messages.put(("log", f"Connected {host}:{HUB_PORT}"))
                if kind == "power":
                    target, on, name = arguments
                    isolate_target(hub, target, lambda msg: self.messages.put(("log", msg)), disabled_ids)
                    if self.stop_event.is_set():
                        result = "Stopped before power command."
                    else:
                        packet = device_power_packet(on)
                        hub.send(packet)
                        idx = next(i for i, d in enumerate(DEVICES) if bytes.fromhex(d[1]) == target)
                        self.messages.put(("power_done", (idx, on)))
                        self.messages.put(("log", f"POWER {name} {'ON' if on else 'OFF'}: {hex_data(packet)}"))
                        result = f"{name}: {'ON' if on else 'OFF'} sent (not confirmed by live readback)."
                elif kind == "group_power" or kind == "group_color":
                    targets, payload, group_name = arguments
                    self.messages.put(("log", "Group '" + group_name + "' targets: " +
                                      ", ".join(names_by_id[t] for t in targets)))
                    # The hub's A6 09 and AA commands apply to all selected devices.
                    # Select all group members, with per-mutation verification,
                    # then send exactly ONE shared command for synchronized effects.
                    select_members(hub, targets, lambda msg: self.messages.put(("log", msg)), disabled_ids)
                    if self.stop_event.is_set():
                        result = f"Group '{group_name}' cancelled before power/color command."
                    else:
                        packet = device_power_packet(payload) if kind == "group_power" else rgb_packet(payload)
                        hub.send(packet)
                        if kind == "group_power":
                            for target in targets:
                                idx = next(i for i, d in enumerate(DEVICES) if bytes.fromhex(d[1]) == target)
                                self.messages.put(("power_done", (idx, payload)))
                        self.messages.put(("log", f"  GROUP SENT ONCE: {hex_data(packet)}"))
                        action = ("ON" if payload else "OFF") if kind == "group_power" else "color"
                        result = (f"Group '{group_name}' {action}: sent one command to "
                                  f"{len(targets)} verified selected device(s).")
                    self.messages.put(("group_done", result))
                elif kind == "bluetooth":
                    (on,) = arguments
                    isolate_target(hub, PROJECTOR_ID,
                                   lambda msg: self.messages.put(("log", msg)), disabled_ids)
                    if self.stop_event.is_set():
                        result = "Stopped before Bluetooth command."
                    else:
                        packet = projector_bluetooth_packet(on)
                        hub.send(packet)
                        self.messages.put(("log", "PROJECTOR BLUETOOTH " + ("ON" if on else "OFF") + ": " + hex_data(packet)))
                        self.messages.put(("bluetooth_done", on))
                        result = "Bluetooth " + ("ON" if on else "OFF") + " sent (not confirmed by readback)."
                elif kind == "volume":
                    (value,) = arguments
                    # Audio uses an AA command like RGB; never risk applying it
                    # to the other light sets unless the projector is isolated.
                    isolate_target(hub, PROJECTOR_ID,
                                   lambda msg: self.messages.put(("log", msg)), disabled_ids)
                    if self.stop_event.is_set():
                        result = "Stopped before volume could be sent."
                    else:
                        packet = projector_volume_packet(value)
                        hub.send(packet)
                        self.messages.put(("log", f"PROJECTOR VOLUME {value}%: " + hex_data(packet)))
                        self.messages.put(("volume_done", value))
                        result = f"Projector volume {value}% command sent (not confirmed by readback)."
                elif kind == "scan":
                    records, selected_count = hub.query_selection()
                    self.messages.put(("scan_done", (host, records)))
                    self.messages.put(("log", f"READ-ONLY SCAN: {len(records)} paired IDs; "
                                       f"{selected_count} selected; no selection/light commands sent."))
                    for ident, flag in records.items():
                        known_ids = {bytes.fromhex(dev[1]) for dev in DEVICES}
                        mapping = "known" if ident in known_ids else "UNKNOWN"
                        selected = "not selected" if flag == b"\xff\x00" else f"selected slot {flag[0]}"
                        self.messages.put(("log", f"  {hex_data(ident)}  {selected}  {mapping}"))
                    result = f"Device scan complete: {len(records)} paired device IDs found."
                elif kind == "refresh":
                    records, n = hub.query_selection()
                    self.messages.put(("log", f"Hub says {n} of {len(records)} devices selected:"))
                    for ident, flag in records.items():
                        self.messages.put(("log", "  " + hex_data(ident) + " / " + (
                            "unselected" if flag == b"\xff\x00" else f"selected slot {flag[0]}")
                        ))
                    result = "Selection read; no colors or selection changes sent."
                elif kind == "send":
                    target, rgb, name = arguments
                    isolate_target(hub, target, lambda msg: self.messages.put(("log", msg)), disabled_ids)
                    if self.stop_event.is_set():
                        result = "Stopped before RGB could be sent."
                    else:
                        hub.send(rgb_packet(rgb))
                        self.messages.put(("log", f"SENT {name}: #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"))
                        result = f"Color sent to {name}. Check which physical device changed."
                elif kind == "effect":
                    target, frames, interval, name, effect_name = arguments
                    isolate_target(hub, target, lambda msg: self.messages.put(("log", msg)), disabled_ids)
                    sent = 0
                    for j, rgb in enumerate(frames):
                        if self.stop_event.is_set():
                            break
                        hub.send(rgb_packet(rgb))
                        sent += 1
                        self.messages.put(("log",
                            f"{effect_name.upper()} {name} frame {sent}/{len(frames)}: "
                            f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"))
                        if j < len(frames) - 1 and self.stop_event.wait(interval):
                            break
                    result = ("Stopped" if self.stop_event.is_set() else "Completed")
                    result += f" {effect_name} on {name}: {sent}/{len(frames)} frames."
                else:
                    raise ValueError("Unknown action type")
        except (OSError, ValueError, TimeoutError, ConnectionError) as err:
            result = "Stopped: " + str(err)
            if kind == "scan":
                self.messages.put(("scan_error", result))
            if kind in ("group_power", "group_color"):
                result += " — group command not sent; hub selection may have changed."
                self.messages.put(("group_done", result))
            self.messages.put(("log", result))
        self.messages.put(("done", result))


if __name__ == "__main__":
    Studio().mainloop()
