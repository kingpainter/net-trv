"""Netatmo API authentication bound to a Home Assistant OAuth2 config entry."""
# VERSION = "0.8.1"

from __future__ import annotations

from collections import deque
from time import monotonic
from typing import Any, cast, override

from aiohttp import ClientResponse, ClientSession
from homeassistant.helpers import config_entry_oauth2_flow
import pyatmo

from .const import RATE_LIMIT_WINDOW_SECONDS


class NetatmoEnergyAuth(pyatmo.AbstractAsyncAuth):
    """pyatmo auth that takes its token from the config entry's OAuth2 session.

    It also counts outgoing API calls, because Netatmo enforces a per-user
    budget of 500 calls per hour and we want to see how close we get.
    """

    def __init__(
        self,
        websession: ClientSession,
        oauth_session: config_entry_oauth2_flow.OAuth2Session,
    ) -> None:
        """Initialize the auth."""
        super().__init__(websession)
        self._oauth_session = oauth_session
        self._call_times: deque[float] = deque()

    @override
    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing it when needed."""
        await self._oauth_session.async_ensure_token_valid()
        return cast(str, self._oauth_session.token["access_token"])

    # Counting at the request level (not *_api_request) so pyatmo's own
    # 429 retries are counted too: Netatmo counts every HTTP call.
    @override
    async def async_post_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> ClientResponse:
        """Count the call, then delegate to pyatmo."""
        self._record_call()
        return await super().async_post_request(url=url, params=params)

    @override
    async def async_get_request(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> ClientResponse:
        """Count the call, then delegate to pyatmo."""
        self._record_call()
        return await super().async_get_request(url=url, params=params)

    def _record_call(self) -> None:
        now = monotonic()
        self._call_times.append(now)
        self._prune(now)

    def _prune(self, now: float) -> None:
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        while self._call_times and self._call_times[0] < cutoff:
            self._call_times.popleft()

    @property
    def calls_last_hour(self) -> int:
        """Return the number of API calls made in the last hour."""
        self._prune(monotonic())
        return len(self._call_times)
