# Orchestra of Lights — Local Home Assistant (Experimental)

Community-developed, unofficial **Gemmy Orchestra of Lights** LAN integration, based on user-captured TCP packets. **Not affiliated with Gemmy.**

## Features

- Individual OOL RGB + power entities for enabled decorations
- One RGB + power light entity for each configured group
- Projector Bluetooth on/off switch and 0–100 volume slider (if projector identified)
- Conservative A5 device-selection verification; blocks uncertain commands
- No polling of power or volume; state reflects last successfully sent command

## Installation — GitHub / Marketplace / HACS

1. Use **Settings → Marketplace → Add from link** on Home Assistant 2026.11+; select this repository and install. On older Home Assistant versions, add the URL to HACS **Custom repositories** as type **Integration**.
2. Restart Home Assistant if prompted.
3. Open **Settings → Devices & services → Add integration** and search **Orchestra of Lights**.
4. Enter the OOL hub's local IPv4 address.
5. On your Windows PC, run **OOL Studio V6**, click **EXPORT HA SETTINGS**. Open the resulting `ool_ha_devices.json` in Notepad and copy its full contents.
6. Paste that JSON into Home Assistant's **Import your OOL Studio devices** step and submit.

Your private device identifiers, groups and labels are saved in the Home Assistant config entry; **don't upload your personal export JSON to this public GitHub repository**.

## Important safety and behavior

- This code is experimental, tested offline using simulated hub responses, **not tested against actual Home Assistant or all OOL decorations**. Back up Home Assistant first.
- If a disabled/broken device remains selected on the hub, other commands are **intentionally blocked**. The source project observed a stuck net light; do not bypass safety verification to work around it.
- Changing the selected devices in the official app or OOL Studio while HA sends a command can cause conflicts. Use one controller at a time.
- Bluetooth switch and volume slider are **command-only**; no known reliable device readback. Brightness adjustment, pairing controls, playlists, firmware update, and independent RGB pixel control are **not** implemented.
- The OOL hub's IP must be reachable from Home Assistant TCP port **8087**.
- Projector audio controls are created when exactly one imported device name includes the word `projector`, or if the JSON includes an explicit `projector_id` matching one of the devices.
- Groups and devices are imported at setup time. To change them, remove and re-add the integration with a newly exported JSON; entity IDs may change.

## Source layout

`custom_components/ool_local/` holds the integration. `hacs.json` and `README.md` belong in the GitHub repository root.

## Publishing for the first time

Before using this GitHub repository, edit `custom_components/ool_local/manifest.json` and replace `REPLACE_USERNAME` in `documentation` and `issue_tracker` with your own public GitHub username. If you pick a repository name other than `ool-local-ha`, update both URLs to match it.

Publish the **contents** of this package as files in the **root** of a new public GitHub repository; do not upload the whole ZIP as a single repository file. A GitHub release is recommended when sharing updates. See https://www.home-assistant.io/integrations/marketplace/.
