"""Tests for the KinderpediaAPI client."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp import ClientError, ClientSession

from custom_components.kinderpedia.api import (
    KinderpediaAPI,
    KinderpediaAuthError,
    KinderpediaConnectionError,
)
from tests.conftest import MOCK_LOGIN_RESPONSE, MOCK_CORE_RESPONSE, MOCK_TIMELINE_RAW, MOCK_NEWSFEED_RAW


def _make_response(status, json_data):
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    return resp


def _make_api(hass_mock):
    """Create a KinderpediaAPI with mocked hass."""
    with patch(
        "custom_components.kinderpedia.api.async_get_clientsession"
    ) as mock_get_session:
        session = AsyncMock(spec=ClientSession)
        mock_get_session.return_value = session
        api = KinderpediaAPI(hass_mock, "test@example.com", "pass123")
    return api, session


class TestLogin:
    """Tests for the login method."""

    @pytest.mark.asyncio
    async def test_login_success(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        session.post = MagicMock(return_value=_async_ctx(resp))

        await api.login()

        assert api.token == "fake-jwt-token"

    @pytest.mark.asyncio
    async def test_login_bad_credentials(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        resp = _make_response(401, {})
        session.post = MagicMock(return_value=_async_ctx(resp))

        with pytest.raises(KinderpediaAuthError):
            await api.login()

    @pytest.mark.asyncio
    async def test_login_null_body(self):
        """A 200 with a `null` JSON body must not raise AttributeError."""
        hass = MagicMock()
        api, session = _make_api(hass)

        resp = _make_response(200, None)
        session.post = MagicMock(return_value=_async_ctx(resp))

        with pytest.raises(KinderpediaAuthError):
            await api.login()

    @pytest.mark.asyncio
    async def test_login_null_expire_at(self):
        """A null expire_at must not blow up datetime.fromtimestamp."""
        hass = MagicMock()
        api, session = _make_api(hass)

        resp = _make_response(200, {"token": "fake-jwt-token", "expire_at": None})
        session.post = MagicMock(return_value=_async_ctx(resp))

        await api.login()

        assert api.token == "fake-jwt-token"

    @pytest.mark.asyncio
    async def test_login_connection_failure(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        session.post = MagicMock(
            return_value=_async_ctx_raise(ClientError("DNS fail"))
        )

        with pytest.raises(KinderpediaConnectionError):
            await api.login()

    @pytest.mark.asyncio
    async def test_login_reuses_valid_token(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        session.post = MagicMock(return_value=_async_ctx(resp))

        await api.login()
        # Second call should reuse token, not call post again
        session.post.reset_mock()
        await api.login()
        session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_login_missing_token(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        resp = _make_response(200, {"expire_at": 9999999999})
        session.post = MagicMock(return_value=_async_ctx(resp))

        with pytest.raises(KinderpediaAuthError, match="missing token"):
            await api.login()


class TestFetchChildren:
    """Tests for the fetch_children method."""

    @pytest.mark.asyncio
    async def test_fetch_children_success(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        login_resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        core_resp = _make_response(200, MOCK_CORE_RESPONSE)

        def side_effect_post(*args, **kwargs):
            return _async_ctx(login_resp)

        def side_effect_get(*args, **kwargs):
            return _async_ctx(core_resp)

        session.post = MagicMock(side_effect=side_effect_post)
        session.get = MagicMock(side_effect=side_effect_get)

        children = await api.fetch_children()

        assert len(children) == 1
        assert children[0]["first_name"] == "Alice"
        assert children[0]["child_id"] == 111

    @pytest.mark.asyncio
    async def test_fetch_children_skips_inactive(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        inactive_response = {
            "result": {
                "available_accounts": [
                    {"child_id": 111, "kindergarten_id": 222, "status": "inactive"}
                ],
                "children": [{"id": 111, "first_name": "Bob"}],
            }
        }

        login_resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        core_resp = _make_response(200, inactive_response)
        session.post = MagicMock(return_value=_async_ctx(login_resp))
        session.get = MagicMock(return_value=_async_ctx(core_resp))

        children = await api.fetch_children()
        assert children == []

    @pytest.mark.asyncio
    async def test_fetch_children_tolerates_malformed_entries(self):
        """Null/!dict accounts and children, and missing ids, must be skipped."""
        hass = MagicMock()
        api, session = _make_api(hass)

        malformed_response = {
            "result": {
                "available_accounts": [
                    None,
                    "garbage",
                    {"status": "active"},  # no child_id / kindergarten_id
                    {"child_id": 999, "kindergarten_id": 222, "status": "active"},  # no matching child
                    {"child_id": 111, "status": "active"},  # no kindergarten_id
                    {"child_id": 111, "kindergarten_id": 222, "status": "active"},
                ],
                "children": [None, "garbage", {"first_name": "NoId"}, {"id": 111, "first_name": "Alice"}],
            }
        }

        login_resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        core_resp = _make_response(200, malformed_response)
        session.post = MagicMock(return_value=_async_ctx(login_resp))
        session.get = MagicMock(return_value=_async_ctx(core_resp))

        children = await api.fetch_children()

        assert len(children) == 1
        assert children[0]["child_id"] == 111
        assert children[0]["kindergarten_id"] == 222
        assert children[0]["first_name"] == "Alice"


class TestFetchTimeline:
    """Tests for the fetch_timeline method."""

    @pytest.mark.asyncio
    async def test_fetch_timeline_success(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        login_resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        timeline_resp = _make_response(200, MOCK_TIMELINE_RAW)

        session.post = MagicMock(return_value=_async_ctx(login_resp))
        session.get = MagicMock(return_value=_async_ctx(timeline_resp))

        result = await api.fetch_timeline(111, 222)
        assert "result" in result

    @pytest.mark.asyncio
    async def test_fetch_timeline_http_error(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        login_resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        timeline_resp = _make_response(500, {})

        session.post = MagicMock(return_value=_async_ctx(login_resp))
        session.get = MagicMock(return_value=_async_ctx(timeline_resp))

        with pytest.raises(KinderpediaConnectionError):
            await api.fetch_timeline(111, 222)


class TestFetchNewsfeed:
    """Tests for the fetch_newsfeed method."""

    @pytest.mark.asyncio
    async def test_fetch_newsfeed_success(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        login_resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        feed_resp = _make_response(200, MOCK_NEWSFEED_RAW)

        session.post = MagicMock(return_value=_async_ctx(login_resp))
        session.get = MagicMock(return_value=_async_ctx(feed_resp))

        result = await api.fetch_newsfeed(111, 222)
        assert "result" in result
        assert "feed" in result["result"]

    @pytest.mark.asyncio
    async def test_fetch_newsfeed_http_error(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        login_resp = _make_response(200, MOCK_LOGIN_RESPONSE)
        feed_resp = _make_response(500, {})

        session.post = MagicMock(return_value=_async_ctx(login_resp))
        session.get = MagicMock(return_value=_async_ctx(feed_resp))

        with pytest.raises(KinderpediaConnectionError):
            await api.fetch_newsfeed(111, 222)


class TestTokenRejection:
    """The server may drop a token before its advertised expiry."""

    @pytest.mark.asyncio
    async def test_retries_once_after_401(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        session.post = MagicMock(return_value=_async_ctx(_make_response(200, MOCK_LOGIN_RESPONSE)))
        responses = [_make_response(401, {}), _make_response(200, MOCK_TIMELINE_RAW)]
        session.get = MagicMock(side_effect=lambda *a, **kw: _async_ctx(responses.pop(0)))

        result = await api.fetch_timeline(111, 222)

        assert "result" in result
        assert session.post.call_count == 2  # initial login + refresh

    @pytest.mark.asyncio
    async def test_gives_up_after_second_401(self):
        hass = MagicMock()
        api, session = _make_api(hass)

        session.post = MagicMock(return_value=_async_ctx(_make_response(200, MOCK_LOGIN_RESPONSE)))
        session.get = MagicMock(return_value=_async_ctx(_make_response(403, {})))

        with pytest.raises(KinderpediaAuthError):
            await api.fetch_timeline(111, 222)


# --- Helpers for async context managers ---

class _async_ctx:
    """Simulate an async context manager that returns a response."""

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        pass


class _async_ctx_raise:
    """Simulate an async context manager that raises."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *args):
        pass
