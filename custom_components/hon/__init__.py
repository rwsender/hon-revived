import logging
from pathlib import Path
from typing import Any

import voluptuous as vol  # type: ignore[import-untyped]
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import config_validation as cv, aiohttp_client
from homeassistant.helpers import device_registry as dr
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from pyhon import Hon

from .const import DOMAIN, PLATFORMS, MOBILE_ID, CONF_REFRESH_TOKEN

_LOGGER = logging.getLogger(__name__)

HON_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
    }
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema(vol.All(cv.ensure_list, [HON_SCHEMA]))},
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = aiohttp_client.async_get_clientsession(hass)
    if (config_dir := hass.config.config_dir) is None:
        raise ValueError("Missing Config Dir")
    hon = await Hon(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        mobile_id=MOBILE_ID,
        session=session,
        test_data_path=Path(config_dir),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN, ""),
    ).create()

    # Save the new refresh token
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_REFRESH_TOKEN: hon.api.auth.refresh_token}
    )

    coordinator: DataUpdateCoordinator[dict[str, Any]] = DataUpdateCoordinator(
        hass, _LOGGER, name=DOMAIN
    )
    hon.subscribe_updates(coordinator.async_set_updated_data)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.unique_id] = {"hon": hon, "coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_start_program_stock(call: ServiceCall) -> None:
        """Start startProgram sending only the mandatory parameters.

        This mirrors what turning the appliance's own dial does (a bare
        program selection with no customization overrides), as opposed to
        the switch entity's normal turn_on which always sends the full,
        customized parameter set. Some programs (e.g. this washer/dryer's
        Drain & Spin) report a much longer estimated duration when started
        with a full custom parameter set than when started "stock".
        """
        device_ids = cv.ensure_list(call.data.get("device_id", []))
        registry = dr.async_get(hass)
        for device_id in device_ids:
            device_entry = registry.async_get(device_id)
            if device_entry is None:
                _LOGGER.error("start_program_stock: device %s not found", device_id)
                continue
            appliance_unique_id = next(
                (
                    identifier[1]
                    for identifier in device_entry.identifiers
                    if identifier[0] == DOMAIN
                ),
                None,
            )
            if appliance_unique_id is None:
                _LOGGER.error(
                    "start_program_stock: device %s has no hon identifier", device_id
                )
                continue
            for entry_data in hass.data.get(DOMAIN, {}).values():
                target_hon = entry_data.get("hon")
                if target_hon is None:
                    continue
                for appliance in target_hon.appliances:
                    if appliance.unique_id == appliance_unique_id:
                        await appliance.commands["startProgram"].send(
                            only_mandatory=True
                        )
                        break

    if not hass.services.has_service(DOMAIN, "start_program_stock"):
        hass.services.async_register(
            DOMAIN,
            "start_program_stock",
            handle_start_program_stock,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    refresh_token = hass.data[DOMAIN][entry.unique_id]["hon"].api.auth.refresh_token

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_REFRESH_TOKEN: refresh_token}
    )
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload:
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    return unload
