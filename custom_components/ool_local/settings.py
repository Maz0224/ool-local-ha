"""User-supplied OOL Studio export. Kept in HA config-entry storage, never GitHub."""
from __future__ import annotations

import json

from .protocol import canonical_id


def parse_settings(raw):
    if not isinstance(raw, dict):
        raise ValueError("Expected OOL Studio JSON object")
    devices, seen = [], set()
    for item in raw.get("devices", []):
        ident = canonical_id(item["id"])
        if ident in seen:
            continue
        seen.add(ident)
        devices.append({
            "id": ident,
            "name": str(item.get("name") or "OOL Light")[:80],
            "enabled": item.get("enabled") is True,
        })
    if not devices:
        raise ValueError("No devices in OOL Studio export")
    groups = []
    for item in raw.get("groups", []):
        name = str(item.get("name", "")).strip()[:80]
        members = []
        for member in item.get("members", []):
            try:
                ident = canonical_id(member)
            except (TypeError, ValueError):
                continue
            if ident in seen and ident not in members:
                members.append(ident)
        if name and members:
            groups.append({"name": name, "members": members})
    explicit = raw.get("projector_id")
    if explicit:
        projector = canonical_id(explicit)
        if projector not in seen:
            raise ValueError("projector_id is not in devices")
    else:
        candidates = [d["id"] for d in devices if "projector" in d["name"].lower()]
        projector = candidates[0] if len(candidates) == 1 else None
    return devices, groups, projector


def parse_json(text):
    return parse_settings(json.loads(text))
