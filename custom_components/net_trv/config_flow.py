"""Config flow for NET-TRV (OAuth2 against the user's own Netatmo app)."""
# VERSION = "0.11.2"

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, override

from aiohttp import ClientError
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_RADIUS
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import probatio as vol
import pyatmo
from pyatmo.exceptions import ApiError, NoDeviceError

from .api import NetatmoTokenAuth
from .const import (
    API_SCOPES,
    CONF_HISTORY_ENABLED,
    CONF_WEATHER_ENABLED,
    CONF_WEATHER_LOCATION,
    DEFAULT_WEATHER_RADIUS,
    DOMAIN,
)
from .coordinator import is_energy_home


class NetatmoEnergyFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the OAuth2 flow.

    Single instance (see manifest): one Netatmo account per Home Assistant.
    """

    DOMAIN = DOMAIN
    VERSION = 1

    # Netatmo home ids with a relay, filled by _async_check_account. The
    # entry's unique id is the first one: stable, and unlike the account
    # e-mail not personal data.
    _home_ids: list[str]

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> NetatmoEnergyOptionsFlow:
        """Return the options flow (public weather area)."""
        return NetatmoEnergyOptionsFlow()

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        """Request only the thermostat scopes."""
        return {"scope": " ".join(API_SCOPES)}

    async def async_step_homekit(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle the relay found over HomeKit.

        The relay only tells us that a Netatmo relay is on the network; the
        account behind it is chosen at sign-in. ``single_config_entry``
        aborts this flow when NET-TRV is already set up. No unique id is set
        here: the entry gets its id from the Netatmo account, not the relay.
        """
        return await self.async_step_homekit_confirm()

    async def async_step_homekit_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask before sending the user to Netatmo."""
        if user_input is None:
            self._set_confirm_only()
            return self.async_show_form(step_id="homekit_confirm")
        return await self.async_step_user()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth when the refresh token is rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm before sending them to Netatmo again."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in to Netatmo again on demand.

        Needed after changing the webhook URL on the Netatmo app: Netatmo
        only binds an app-level webhook to a user at authorization time.
        """
        if user_input is None:
            return self.async_show_form(step_id="reconfigure")
        return await self.async_step_user()

    @override
    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry, or update the token on reauth/reconfigure.

        ``data_updates`` merges the new token into the entry, so the webhook
        id and Nabu Casa cloudhook URL survive and the webhook URL stays the
        same.

        Every source first reads /homesdata with the new token, so a broken
        account or one without a relay never ends up as an entry.
        """
        if error := await self._async_check_account(data):
            return self.async_abort(reason=error)
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            entry = (
                self._get_reauth_entry()
                if self.source == SOURCE_REAUTH
                else self._get_reconfigure_entry()
            )
            # Same account = the entry's home is still on it. An entry from
            # before 0.11.0 has no unique id yet and takes the new one.
            if entry.unique_id and entry.unique_id not in self._home_ids:
                return self.async_abort(reason="wrong_account")
            return self.async_update_reload_and_abort(
                entry,
                unique_id=entry.unique_id or self._home_ids[0],
                data_updates=data,
            )
        await self.async_set_unique_id(self._home_ids[0])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="NET-TRV", data=data)

    async def _async_check_account(self, data: dict[str, Any]) -> str | None:
        """Return an abort reason, or None when the account has a relay."""
        auth = NetatmoTokenAuth(
            async_get_clientsession(self.hass), data["token"]["access_token"]
        )
        account = pyatmo.AsyncAccount(auth)
        try:
            await account.async_update_topology()
        except NoDeviceError:
            # pyatmo raises this when /homesdata holds no homes at all.
            return "no_energy_home"
        except (ApiError, ClientError, TimeoutError) as err:
            self.logger.warning("Could not read Netatmo homes: %s", err)
            return "cannot_connect"
        self._home_ids = sorted(
            home_id for home_id, home in account.homes.items() if is_energy_home(home)
        )
        if not self._home_ids:
            return "no_energy_home"
        return None


class NetatmoEnergyOptionsFlow(OptionsFlowWithReload):
    """Room history import and the public weather area."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the weather options; saving reloads the entry."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        location = options.get(CONF_WEATHER_LOCATION) or {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            CONF_RADIUS: DEFAULT_WEATHER_RADIUS,
        }
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HISTORY_ENABLED,
                    default=options.get(CONF_HISTORY_ENABLED, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_WEATHER_ENABLED,
                    default=options.get(CONF_WEATHER_ENABLED, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_WEATHER_LOCATION, default=location
                ): selector.LocationSelector(
                    selector.LocationSelectorConfig(radius=True)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
