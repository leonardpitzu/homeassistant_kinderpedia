"""Kinderpedia HTTP API client."""

from __future__ import annotations

import logging
from asyncio import Lock
from datetime import UTC, datetime
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_KEY, CORE_URL, DATA_URL, LOGIN_URL, NEWSFEED_URL, REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_AUTH_STATUSES = (401, 403)


class KinderpediaAuthError(HomeAssistantError):
    """Raised on authentication failure."""


class KinderpediaConnectionError(HomeAssistantError):
    """Raised on connection failure."""


class KinderpediaAPI:
    """Minimal async client for the Kinderpedia parent API."""

    def __init__(self, hass: HomeAssistant, email: str, password: str) -> None:
        self.hass = hass
        self.email = email
        self.password = password
        self.session = async_get_clientsession(hass)
        self.token: str | None = None
        self.token_expiry = datetime.min.replace(tzinfo=UTC)
        self._login_lock = Lock()
        self._timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    async def login(self) -> None:
        """Log in, reusing a cached token while it is still valid."""
        if self._token_valid():
            _LOGGER.debug("Reusing cached token")
            return

        async with self._login_lock:
            # Re-check after acquiring the lock: another coroutine may have
            # refreshed the token while we were waiting.
            if self._token_valid():
                _LOGGER.debug("Reusing cached token")
                return

            await self._do_login()

    def _token_valid(self) -> bool:
        return bool(self.token) and datetime.now(tz=UTC) < self.token_expiry

    async def _do_login(self) -> None:
        payload = {
            "email": self.email,
            "password": self.password,
        }

        _LOGGER.debug("Sending login request to %s", LOGIN_URL)

        try:
            async with self.session.post(LOGIN_URL, json=payload, timeout=self._timeout) as resp:
                _LOGGER.debug("Login response status: %s", resp.status)
                if resp.status != 200:
                    raise KinderpediaAuthError(f"Login failed with HTTP {resp.status}")
                data = await resp.json()
        except KinderpediaAuthError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise KinderpediaConnectionError(f"Connection failed: {err}") from err

        _LOGGER.debug("Login response: %s", data)

        if not isinstance(data, dict):
            raise KinderpediaAuthError("Login failed: malformed response")

        self.token = data.get("token")
        self.token_expiry = datetime.fromtimestamp(data.get("expire_at") or 0, tz=UTC)

        if not self.token:
            raise KinderpediaAuthError("Login failed: missing token")

        _LOGGER.debug("Login token: %s", self.token)

    def _headers(self, child_id: int | None = None, kindergarten_id: int | None = None) -> dict[str, str]:
        """Build request headers for an authenticated call."""
        headers = {
            "cookie": f"JWToken={self.token}",
            "x-requested-with": "XMLHttpRequest",
            "x-api-key": API_KEY,
        }
        if child_id is not None:
            headers["x-child-id"] = str(child_id)
        if kindergarten_id is not None:
            headers["x-kindergarten-id"] = str(kindergarten_id)
        return headers

    async def _get_json(
        self,
        url: str,
        what: str,
        child_id: int | None = None,
        kindergarten_id: int | None = None,
    ) -> Any:
        """GET *url* authenticated, re-logging in once if the token is rejected.

        The server can invalidate a token before its advertised expiry, so a
        401/403 means "log in again", not "give up".
        """
        await self.login()

        for attempt in (1, 2):
            try:
                async with self.session.get(
                    url,
                    headers=self._headers(child_id, kindergarten_id),
                    timeout=self._timeout,
                ) as resp:
                    if resp.status in _AUTH_STATUSES:
                        if attempt == 1:
                            _LOGGER.debug("%s rejected with HTTP %s, refreshing token", what, resp.status)
                            self.token = None
                            await self.login()
                            continue
                        raise KinderpediaAuthError(f"{what} rejected with HTTP {resp.status}")
                    if resp.status != 200:
                        raise KinderpediaConnectionError(f"{what} failed: HTTP {resp.status}")
                    return await resp.json()
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise KinderpediaConnectionError(f"{what} failed: {err}") from err

        raise KinderpediaAuthError(f"{what} rejected after re-authentication")

    async def fetch_children(self) -> list[dict[str, Any]]:
        """Return the active children on this account."""
        _LOGGER.debug("Fetching core data from %s", CORE_URL)
        data = await self._get_json(CORE_URL, "Core data fetch")
        _LOGGER.debug("Core response: %s", data)

        result_data = data.get("result", {}) if isinstance(data, dict) else {}
        accounts = result_data.get("available_accounts", []) or []
        children = result_data.get("children", []) or []

        child_lookup = {c["id"]: c for c in children if isinstance(c, dict) and "id" in c}
        enriched: list[dict[str, Any]] = []

        for acc in accounts:
            if not isinstance(acc, dict) or acc.get("status") != "active":
                continue

            child_id = acc.get("child_id")
            kg_id = acc.get("kindergarten_id")
            child = child_lookup.get(child_id)
            if not child or kg_id is None:
                continue

            enriched.append({
                "child_id": child_id,
                "kindergarten_id": kg_id,
                "kindergarten_name": acc.get("kindergarten_name", "Unknown"),
                "avatar": acc.get("avatar"),
                "first_name": child.get("first_name", "Unknown"),
                "last_name": child.get("last_name", ""),
                "birth_date": child.get("birth_date"),
                "gender": child.get("gender"),
            })

        return enriched

    async def fetch_timeline(self, child_id: int, kindergarten_id: int, week_offset: int = 0) -> Any:
        """Fetch the daily timeline for a child.

        *week_offset* is relative to the current week: 0 = this week,
        -1 = last week, -2 = two weeks ago, etc.
        """
        url = DATA_URL.format(week=week_offset)
        _LOGGER.debug("Fetching timeline from %s", url)
        return await self._get_json(url, "Timeline fetch", child_id, kindergarten_id)

    async def fetch_newsfeed(self, child_id: int, kindergarten_id: int) -> Any:
        """Fetch the newsfeed for a child."""
        _LOGGER.debug("Fetching newsfeed from %s", NEWSFEED_URL)
        return await self._get_json(NEWSFEED_URL, "Newsfeed fetch", child_id, kindergarten_id)
