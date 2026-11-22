"""Projector volume slider; does not automatically poll or send on startup."""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .protocol import volume_packet


async def async_setup_entry(hass, entry, async_add_entities):
    controller = hass.data[DOMAIN][entry.entry_id]
    if not controller.projector_id or not any(d["id"] == controller.projector_id and d["enabled"] for d in controller.devices):
        return
    async_add_entities([ProjectorVolume(controller)])


class ProjectorVolume(NumberEntity):
    _attr_name = "OOL Projector Volume"
    _attr_unique_id = "ool_projector_volume"
    _attr_should_poll = False
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_native_value = None

    def __init__(self, controller):
        self.controller = controller
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller.projector_id)},
            name="OOL Projector", manufacturer="Gemmy", model="Orchestra of Lights Projector"
        )

    async def async_set_native_value(self, value: float):
        amount = round(value)
        await self.controller.send([self.controller.projector_id], volume_packet(amount))
        self._attr_native_value = amount
        self.async_write_ha_state()
