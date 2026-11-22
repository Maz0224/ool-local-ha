"""Conservative local TCP protocol for Gemmy OOL. No Home Assistant dependencies.

All state operations are optimistic: A5 reports which decorations are SELECTED,
not whether their light or Bluetooth power is on.
"""
from __future__ import annotations

import socket
import time
from contextlib import closing

from .const import PORT

QUERY = bytes.fromhex("03 00 A5 00 01")
SELECT = bytes.fromhex("0D 00 A5 01 01 06 84 01 20")
DESELECT = bytes.fromhex("0B 00 A5 02 01 06 84")


class OOLProtocolError(Exception):
    """Do not send a lighting command when exact targets are not confirmed."""


def canonical_id(value: str) -> str:
    raw = bytes.fromhex(value)
    if len(raw) != 6:
        raise ValueError("Device IDs must contain 6 bytes")
    return raw.hex(" ").upper()


def power_packet(on: bool) -> bytes:
    return bytes.fromhex("03 00 A6 09") + bytes([int(on)])


def bluetooth_packet(on: bool) -> bytes:
    return bytes.fromhex("03 00 A6 0D") + bytes([int(on)])


def volume_packet(value: int) -> bytes:
    value = int(value)
    if not 0 <= value <= 100:
        raise ValueError("Volume must be 0..100")
    return bytes.fromhex("0B 00 AA 00 00 01 04") + bytes([value, value]) + bytes(4)


def rgb_packet(rgb) -> bytes:
    rgb = tuple(int(x) for x in rgb)
    if len(rgb) != 3 or any(not 0 <= v <= 255 for v in rgb):
        raise ValueError("RGB channels must be 0..255")
    return bytes.fromhex("0B 00 AA 00 00 03") + bytes(rgb) + bytes.fromhex("00 00 00 64")


class Hub:
    """Short-lived TCP session; never probes arbitrary ports or sends at startup."""

    def __init__(self, host):
        self.host = host
        self.socket = None
        self.buffer = bytearray()

    def __enter__(self):
        self.socket = socket.create_connection((self.host, PORT), timeout=4)
        self.socket.settimeout(3.5)
        return self

    def __exit__(self, *_args):
        if self.socket is not None:
            self.socket.close()

    def send(self, packet):
        self.socket.sendall(packet)

    def frame(self):
        deadline = time.monotonic() + 3.5
        while True:
            if len(self.buffer) >= 2:
                length = int.from_bytes(self.buffer[:2], "little")
                if not 0 < length <= 4096:
                    raise OOLProtocolError("Bad frame length")
                if len(self.buffer) >= length + 2:
                    packet = bytes(self.buffer[2:length + 2])
                    del self.buffer[:length + 2]
                    return packet
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("No A5 response from hub")
            self.socket.settimeout(remaining)
            chunk = self.socket.recv(4096)
            if not chunk:
                raise ConnectionError("Hub closed TCP connection")
            self.buffer.extend(chunk)

    def selection(self):
        self.send(QUERY)
        for _ in range(5):
            data = self.frame()
            if data[:2] != b"\xa5\x00":
                continue
            if len(data) < 8 or (len(data) - 8) % 8 or data[4:8] != b"\x06\x84\xff\xff":
                raise OOLProtocolError("Unrecognized A5 response layout")
            count = (len(data) - 8) // 8
            selected_count, unselected_count = data[2], data[3]
            if selected_count + unselected_count != count:
                raise OOLProtocolError("Hub count mismatch")
            records = {}
            for i in range(8, len(data), 8):
                ident, marker = data[i:i+6], data[i+6:i+8]
                if ident in records:
                    raise OOLProtocolError("Duplicate device in response")
                if marker != b"\xff\x00" and (marker[1] != 0 or not 1 <= marker[0] <= count):
                    raise OOLProtocolError("Unknown selection marker: " + marker.hex())
                records[ident] = marker
            selected = {ident for ident, marker in records.items() if marker != b"\xff\x00"}
            if len(selected) != selected_count:
                raise OOLProtocolError("Selected count mismatch")
            return records, selected
        raise OOLProtocolError("No A5 status returned")

    def wait_selected(self, expected, description):
        for i in range(6):
            time.sleep(0.35 if i == 0 else 0.45)
            _records, selected = self.selection()
            if selected == expected:
                return
        raise OOLProtocolError(description + " not confirmed by hub; no lighting command sent")

    def select_exact(self, targets, known_ids, disabled_ids):
        target_set = set(targets)
        if not target_set or target_set & set(disabled_ids) or not target_set <= set(known_ids):
            raise OOLProtocolError("Empty, disabled or unregistered target")
        records, current = self.selection()
        if not target_set <= set(records):
            raise OOLProtocolError("Target light not in paired hub devices")
        if current - set(known_ids):
            raise OOLProtocolError("Unregistered device selected. Import an updated Studio export")
        if current & set(disabled_ids):
            raise OOLProtocolError("Disabled OOL light still selected ON THE HUB. "
                                   "Refusing commands that could also affect it")
        for ident in records:
            if ident in current and ident not in target_set:
                self.send(DESELECT + ident)
                current.remove(ident)
                self.wait_selected(current, "Deselect " + ident.hex())
        for ident in records:
            if ident in target_set and ident not in current:
                self.send(SELECT + ident)
                current.add(ident)
                self.wait_selected(current, "Select " + ident.hex())
        _records, observed = self.selection()
        if observed != target_set:
            raise OOLProtocolError("Final selection mismatch")

    def act(self, targets, payload, known_ids, disabled_ids):
        self.select_exact(targets, known_ids, disabled_ids)
        self.send(payload)


def probe(host):
    """Read only: used while adding the integration in the HA UI."""
    with Hub(host) as hub:
        records, _selected = hub.selection()
        if not records:
            raise OOLProtocolError("No paired devices reported")
        return len(records)
