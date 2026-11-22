OOL STUDIO V6 — DISABLE STUCK DEVICES
====================================

FILES
-----
ool_studio_groups_v6_disabled.py   Main Windows app (standard library only)

INSTALL
-------
1. Extract the Python file into your CURRENT oolProject folder, alongside your
   ool_multidevice_labels.json, ool_multidevice_groups.json and
   ool_registered_devices.json files. Do NOT delete those saved files.
2. Run:
   py "C:\Users\camaz\Desktop\oolProject\ool_studio_groups_v6_disabled.py"
3. Device 'A1 00 C7 16 31 24' starts DISABLED by default because logs show
   the official iPhone app and the Python app cannot deselect it reliably.
4. Every device card has a 'Disable device (keep saved)' checkbox.
   Changes persist in ool_disabled_devices.json and do NOT alter pairing.
5. Disabled devices remain visible and in saved group definitions, but are
   filtered out when sending group controls. You can re-enable any time.
6. Click EXPORT HA SETTINGS to create ool_ha_devices.json. You can copy that
   into the companion HA integration folder on your Raspberry Pi.

CRITICAL SAFETY DETAIL
----------------------
Disabling a device does NOT remove it from the OOL hub's selected devices. If
it is still selected, a hub-wide RGB or A6 power command would affect it too.
To avoid that, this app verifies that no disabled device is selected before
sending group, single-device or audio commands. If stuck selection persists,
operations STOP and display a clear error. No hidden bypass.

No network messages are sent just by opening the program or checking the
Disable box. Existing scan, labels, group controls, RGB and audio preserved.

Offline smoke tests and simulated-selection tests passed; real OOL hub still
needs to be tested.
