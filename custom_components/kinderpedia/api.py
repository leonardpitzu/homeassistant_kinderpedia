import logging
from asyncio import Lock
from datetime import UTC, datetime
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import HomeAssistantError
from .const import LOGIN_URL, CORE_URL, DATA_URL, NEWSFEED_URL, API_KEY

_LOGGER = logging.getLogger(__name__)


class KinderpediaAuthError(HomeAssistantError):
    """Raised on authentication failure."""


class KinderpediaConnectionError(HomeAssistantError):
    """Raised on connection failure."""


class KinderpediaAPI:
    def __init__(self, hass, email, password):
        self.hass = hass
        self.email = email
        self.password = password
        self.session = async_get_clientsession(hass)
        self.token = None
        self.token_expiry = datetime.min.replace(tzinfo=UTC)
        self._login_lock = Lock()

    async def login(self):
        if self.token and datetime.now(tz=UTC) < self.token_expiry:
            _LOGGER.debug("Reusing cached token")
            return

        async with self._login_lock:
            # Re-check after acquiring the lock: another coroutine may have
            # refreshed the token while we were waiting.
            if self.token and datetime.now(tz=UTC) < self.token_expiry:
                _LOGGER.debug("Reusing cached token")
                return

            await self._do_login()

    async def _do_login(self):
        payload = {
            "email": self.email,
            "password": self.password,
        }

        _LOGGER.debug("Sending login request to %s", LOGIN_URL)

        try:
            async with self.session.post(LOGIN_URL, json=payload) as resp:
                _LOGGER.debug("Login response status: %s", resp.status)
                if resp.status != 200:
                    raise KinderpediaAuthError(f"Login failed with HTTP {resp.status}")
                data = await resp.json()
        except KinderpediaAuthError:
            raise
        except Exception as e:
            raise KinderpediaConnectionError(f"Connection failed: {e}") from e

        _LOGGER.debug("Login response: %s", data)

        self.token = data.get("token")
        self.token_expiry = datetime.fromtimestamp(data.get("expire_at", 0), tz=UTC)

        if not self.token:
            raise KinderpediaAuthError("Login failed: missing token")

        _LOGGER.debug("Login token: %s", self.token)

    async def fetch_children(self):
        await self.login()
        headers = {
            "cookie": f"JWToken={self.token}",
            "x-requested-with": "XMLHttpRequest",
            "x-api-key": API_KEY
        }

        _LOGGER.debug("Fetching core data from %s", CORE_URL)

        try:
            async with self.session.get(CORE_URL, headers=headers) as resp:
                _LOGGER.debug("Core status: %s", resp.status)
                if resp.status != 200:
                    raise KinderpediaConnectionError(f"Core data fetch failed: HTTP {resp.status}")
                data = await resp.json()
                _LOGGER.debug("Core response: %s", data)
        except KinderpediaConnectionError:
            raise
        except Exception as e:
            raise KinderpediaConnectionError(f"Failed to fetch core data: {e}") from e

        result_data = data.get("result", {})
        _LOGGER.debug("Result keys: %s", list(result_data.keys()))

        accounts = result_data.get("available_accounts", [])
        children = result_data.get("children", [])

        child_lookup = {c["id"]: c for c in children}
        enriched = []

        for acc in accounts:
            if acc.get("status") != "active":
                continue

            child_id = acc["child_id"]
            kg_id = acc["kindergarten_id"]
            child = child_lookup.get(child_id)

            if not child:
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

    async def fetch_timeline(self, child_id, kindergarten_id, week_offset=0):
        """Fetch the daily timeline for a child.

        *week_offset* is relative to the current week: 0 = this week,
        -1 = last week, -2 = two weeks ago, etc.
        """
        await self.login()
        url = DATA_URL.format(week=week_offset)
        _LOGGER.debug("Fetching timeline from %s", url)
        headers = {
                "x-child-id": str(child_id),
                "x-kindergarten-id": str(kindergarten_id),
                "x-requested-with": "XMLHttpRequest",
                "x-api-key": API_KEY,
                "cookie": f"JWToken={self.token}",
            }
        try:
            async with self.session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    raise KinderpediaConnectionError(f"Timeline fetch failed: HTTP {resp.status}")
                return await resp.json()
        except KinderpediaConnectionError:
            raise
        except Exception as e:
            raise KinderpediaConnectionError(f"Failed to fetch timeline: {e}") from e

    async def fetch_newsfeed(self, child_id, kindergarten_id):
        await self.login()
        headers = {
            "x-child-id": str(child_id),
            "x-kindergarten-id": str(kindergarten_id),
            "x-requested-with": "XMLHttpRequest",
            "x-api-key": API_KEY,
            "cookie": f"JWToken={self.token}",
        }

        _LOGGER.debug("Fetching newsfeed from %s", NEWSFEED_URL)

        try:
            async with self.session.get(NEWSFEED_URL, headers=headers) as resp:
                if resp.status != 200:
                    raise KinderpediaConnectionError(f"Newsfeed fetch failed: HTTP {resp.status}")
                return await resp.json()
        except KinderpediaConnectionError:
            raise
        except Exception as e:
            raise KinderpediaConnectionError(f"Failed to fetch newsfeed: {e}") from e
