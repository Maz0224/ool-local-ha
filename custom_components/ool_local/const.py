"""Constants for the locally researched OOL protocol."""
DOMAIN = "ool_local"
PORT = 8087
from homeassistant.const import Platform
PLATFORMS = (Platform.LIGHT, Platform.SWITCH, Platform.NUMBER)
