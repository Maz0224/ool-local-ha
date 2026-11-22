"""Orchestra of Lights local custom integration, experimental."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .protocol import Hub
from .settings import parse_json

_LOGGER = logging.getLogger(__name__)


def load_settings():
    """Legacy settings compatibility. New installations import JSON in the setup UI."""
    from .settings import parse_json

    paths = [Path(__file__).parents[2] / "ool_ha_devices.json", Path(__file__).with_name("ool_ha_devices.json")]
    for config in paths:
        if config.is_file():
            return parse_json(config.read_text(encoding="utf-8"))
    raise FileNotFoundError("Import your OOL Studio export during integration setup")


class OOLController:
    """Serializes all commands because A5 selection is shared hub state."""
    def __init__(self, hass: HomeAssistant, host: str, devices, groups, projector_id):
        self.hass = hass
        self.host = host
        self.devices = devices
        self.groups = groups
        self.projector_id = projector_id
        self.known_ids = {bytes.fromhex(d["id"]) for d in devices}
        self.disabled_ids = {bytes.fromhex(d["id"]) for d in devices if not d["enabled"]}
        self.lock = asyncio.Lock()
        self.last_action = 0.0

    async def send(self, ids: list[str] | tuple[str, ...], packet: bytes) -> None:
        targets = [bytes.fromhex(ident) for ident in ids]
        async with self.lock:
            pause = 1.5 - (time.monotonic() - self.last_action)
            if pause > 0:
                await asyncio.sleep(pause)
            try:
                await self.hass.async_add_executor_job(self._send_blocking, targets, packet)
            finally:
                self.last_action = time.monotonic()

    def _send_blocking(self, targets, packet):
        try:
            with Hub(self.host) as hub:
                hub.act(targets, packet, self.known_ids, self.disabled_ids)
        except Exception:
            _LOGGER.exception("OOL command failed without confirming an exact selection")
            raise


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        if entry.data.get("settings_json"):
            devices, groups, projector_id = await hass.async_add_executor_job(
                parse_json, entry.data["settings_json"]
            )
        else:
            devices, groups, projector_id = await hass.async_add_executor_job(load_settings)
    except (OSError, ValueError, KeyError, TypeError):
        _LOGGER.exception("Couldn't load OOL Studio device export")
        return False
    controller = OOLController(hass, entry.data["host"], devices, groups, projector_id)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if result:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return result
