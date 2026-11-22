"""OOL physical and group light entities. Control states are optimistic."""
from __future__ import annotations

import hashlib

from homeassistant.components.light import ATTR_RGB_COLOR, ColorMode, LightEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .protocol import power_packet, rgb_packet


async def async_setup_entry(hass, entry, async_add_entities):
    controller = hass.data[DOMAIN][entry.entry_id]
    entities = []
    enabled = {d["id"] for d in controller.devices if d["enabled"]}
    for dev in controller.devices:
        if dev["enabled"]:
            entities.append(OOLLight(controller, dev["name"], [dev["id"]], "device_" + dev["id"].replace(" ", "")))
    for group in controller.groups:
        members = [ident for ident in group["members"] if ident in enabled]
        if not members:
            continue
        digest = hashlib.sha256(group["name"].encode()).hexdigest()[:12]
        entities.append(OOLLight(controller, group["name"], members, "group_" + digest, group=True))
    async_add_entities(entities)


class OOLLight(LightEntity):
    """No true power readback; state becomes known after a successful command."""
    _attr_should_poll = False
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, controller, name, member_ids, key, group=False):
        self.controller = controller
        self.members = member_ids
        self._attr_unique_id = "ool_" + key
        self._attr_name = name
        self._attr_rgb_color = (255, 255, 255)
        self._attr_is_on = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "group_" + key if group else member_ids[0])},
            name=name, manufacturer="Gemmy", model="OOL group" if group else "Orchestra of Lights"
        )
        self._attr_extra_state_attributes = {"state_source": "last_command_only"}

    async def async_turn_on(self, **kwargs):
        rgb = kwargs.get(ATTR_RGB_COLOR)
        if rgb is None:
            packet = power_packet(True)
        else:
            packet = rgb_packet(rgb)
        await self.controller.send(self.members, packet)
        self._attr_is_on = True
        if rgb is not None:
            self._attr_rgb_color = tuple(rgb)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self.controller.send(self.members, power_packet(False))
        self._attr_is_on = False
        self.async_write_ha_state()
