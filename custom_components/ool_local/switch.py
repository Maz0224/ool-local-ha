"""Projector Bluetooth toggle; state known only after a successful send."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .protocol import bluetooth_packet


async def async_setup_entry(hass, entry, async_add_entities):
    controller = hass.data[DOMAIN][entry.entry_id]
    if not controller.projector_id or not any(d["id"] == controller.projector_id and d["enabled"] for d in controller.devices):
        return
    async_add_entities([ProjectorBluetooth(controller)])


class ProjectorBluetooth(SwitchEntity):
    _attr_name = "OOL Projector Bluetooth"
    _attr_unique_id = "ool_projector_bluetooth"
    _attr_should_poll = False
    _attr_is_on = None

    def __init__(self, controller):
        self.controller = controller
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, controller.projector_id)},
            name="OOL Projector", manufacturer="Gemmy", model="Orchestra of Lights Projector"
        )

    async def async_turn_on(self, **kwargs):
        await self.controller.send([self.controller.projector_id], bluetooth_packet(True))
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        await self.controller.send([self.controller.projector_id], bluetooth_packet(False))
        self._attr_is_on = False
        self.async_write_ha_state()
