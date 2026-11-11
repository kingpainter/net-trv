"""Tests for the auth classes: token source and the API call counter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientSession
import pyatmo
import pytest

from custom_components.net_trv import api
from custom_components.net_trv.api import NetatmoEnergyAuth, NetatmoTokenAuth


@pytest.fixture
def oauth_session() -> MagicMock:
    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "from-entry"}
    return session


async def test_token_comes_from_oauth_session(oauth_session: MagicMock) -> None:
    auth = NetatmoEnergyAuth(MagicMock(spec=ClientSession), oauth_session)
    assert await auth.async_get_access_token() == "from-entry"
    oauth_session.async_ensure_token_valid.assert_awaited_once()


async def test_token_auth_returns_bare_token() -> None:
    auth = NetatmoTokenAuth(MagicMock(spec=ClientSession), "fresh")
    assert await auth.async_get_access_token() == "fresh"
    assert auth.calls_last_hour == 0


async def test_requests_are_counted_and_pruned(oauth_session: MagicMock) -> None:
    auth = NetatmoEnergyAuth(MagicMock(spec=ClientSession), oauth_session)
    with (
        patch.object(
            pyatmo.AbstractAsyncAuth, "async_post_request", AsyncMock()
        ) as post,
        patch.object(pyatmo.AbstractAsyncAuth, "async_get_request", AsyncMock()) as get,
        patch.object(api, "monotonic", return_value=1000.0),
    ):
        await auth.async_post_request(url="u", params={"a": 1})
        await auth.async_get_request(url="u")
        assert auth.calls_last_hour == 2
    post.assert_awaited_once_with(url="u", params={"a": 1})
    get.assert_awaited_once_with(url="u", params=None)

    # An hour later both calls have dropped out of the window.
    with patch.object(api, "monotonic", return_value=1000.0 + 3601):
        assert auth.calls_last_hour == 0
