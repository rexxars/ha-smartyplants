# SmartyPlants for Home Assistant (deprecated)

> [!CAUTION]
> **This custom integration no longer works.** Use the [official SmartyPlants integration](https://www.home-assistant.io/integrations/smartyplants/) included in Home Assistant 2026.11 and later.

For a new installation, go to **Settings → Devices & services → Add integration** and select **SmartyPlants**.

## Migrating from the custom integration

Both integrations use the `smartyplants` domain. While the custom component is installed, Home Assistant loads it in preference to the built-in integration. The built-in integration requires a new configuration entry with an API key.

1. Upgrade to Home Assistant **2026.11 or later**. Create an API key under **Settings** in the SmartyPlants app.
2. Back up Home Assistant and note the SmartyPlants entities and devices used in your automations, dashboards, and Plant configuration. Removing the old entry also removes its devices and entities.
3. In **Settings → Devices & services → SmartyPlants**, open the three-dot menu next to each custom integration entry and select **Delete**.
4. If you installed via HACS, find **SmartyPlants** in HACS, open its three-dot menu, and select **Remove**. For a manual installation, delete the `custom_components/smartyplants/` folder from your Home Assistant configuration directory.
5. Restart Home Assistant. Under **Settings → Devices & services → Add integration**, select **SmartyPlants** and enter your API key.
6. Check the new devices and entity IDs and update any references you recorded. The **Needs watering**, **Needs attention**, and optional status sensors were specific to the custom integration. For watering reminders, the [official documentation](https://www.home-assistant.io/integrations/smartyplants/#automation-remind-me-to-water-a-plant) has an example using a soil moisture threshold.

## License

[MIT](LICENSE)
