"""Config flow for NET-TRV (OAuth2 against the user's own Netatmo app)."""
# VERSION = "0.4.0"

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any, override

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlowResult,
)
from homeassistant.helpers import config_entry_oauth2_flow

from .const import API_SCOPES, DOMAIN


class NetatmoEnergyFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the OAuth2 flow.

    Single instance (see manifest): one Netatmo account per Home Assistant.
    """

    DOMAIN = DOMAIN
    VERSION = 1

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        """Request only the thermostat scopes."""
        return {"scope": " ".join(API_SCOPES)}

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
        """
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data_updates=data
            )
        return self.async_create_entry(title="NET-TRV", data=data)
