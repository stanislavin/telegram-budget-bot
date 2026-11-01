"""Tests for the Telegram Mini App (charts page) endpoints and validation."""

import hashlib
import hmac
import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qsl, urlencode

import pytest

from util.telegram_auth import validate_init_data, parse_init_user


def _sign_init_data(params: dict, token: str) -> str:
    """Build an initData query string with a valid Telegram hash."""
    secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
    check = '\n'.join(f'{k}={params[k]}' for k in sorted(params))
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, 'hash': h})


class TestValidateInitData:
    def test_valid_signature(self):
        token = "123:ABC"
        params = {
            'auth_date': str(int(time.time())),
            'query_id': 'AAHdqTcvCH1vGWJ',
            'user': '{"id": 42, "first_name": "Stas"}',
        }
        data = _sign_init_data(params, token)
        assert validate_init_data(data, token=token) is True

    def test_tampered_user(self):
        token = "123:ABC"
        params = {
            'auth_date': str(int(time.time())),
            'user': '{"id": 42}',
        }
        data = _sign_init_data(params, token)
        # attacker swaps the user payload but can't re-sign
        forged = data.replace('id%22%3A+42', 'id%22%3A+7')
        if forged == data:  # urlencode differs; fall back to rebuilding
            forged = urlencode({**params, 'user': '{"id": 7}', 'hash': parse_qsl(data)[1][1]})
        assert validate_init_data(forged, token=token) is False

    def test_wrong_token_fails(self):
        params = {'auth_date': str(int(time.time())), 'user': '{"id": 42}'}
        data = _sign_init_data(params, "123:RIGHT")
        assert validate_init_data(data, token="123:WRONG") is False

    def test_expired_auth_date(self):
        token = "123:ABC"
        params = {
            'auth_date': str(int(time.time()) - 48 * 3600),
            'user': '{"id": 42}',
        }
        data = _sign_init_data(params, token)
        assert validate_init_data(data, token=token) is False

    def test_missing_hash(self):
        assert validate_init_data('auth_date=1&user=%7B%7D', token='t') is False

    def test_empty(self):
        assert validate_init_data('', token='t') is False
        assert validate_init_data(None, token='t') is False


class TestParseInitUser:
    def test_extracts_id(self):
        data = urlencode({'user': '{"id": 42, "first_name": "Stas"}'})
        assert parse_init_user(data) == 42

    def test_no_user(self):
        assert parse_init_user('hash=abc') is None
        assert parse_init_user(None) is None


def _mini_app_client():
    """Flask test client + mock pool, same pattern as the `client` fixture
    in test_web_api.py (patch stays active while the client is used)."""
    from util.health import build_app
    mock_pool = MagicMock()
    mock_pool.fetch = MagicMock(return_value=[])
    mock_pool.execute = MagicMock(return_value="UPDATE 1")

    import asyncio

    def fake_run(coro):
        coro_name = getattr(coro, '__qualname__', '') or getattr(coro, '__name__', '')
        if asyncio.iscoroutine(coro):
            coro.close()
        if '_get_web_pool' in coro_name or '_ensure' in coro_name:
            return mock_pool
        return mock_pool.fetch()

    patcher = patch("web.api._run", side_effect=fake_run)
    patcher.start()
    import web.api
    web.api._spending_type_col_ensured = True
    web.api._planned_col_ensured = True
    app = build_app()
    app.config['TESTING'] = True
    return app.test_client(), mock_pool


TOKEN = "***"
UID = 111222333


def _init_data(user_id: int = UID, age: int = 0) -> str:
    return _sign_init_data(
        {'auth_date': str(int(time.time()) - age), 'user': f'{{"id": {user_id}}}'},
        TOKEN,
    )


class TestChartsPage:
    def test_denied_without_init_data(self):
        client, _ = _mini_app_client()
        with patch("util.telegram_auth.TELEGRAM_BOT_TOKEN", TOKEN), \
             patch("web.api._MINI_APP_OWNER_CACHE", set()):
            r = client.get("/charts")
            assert r.status_code == 401

    def test_serves_page_with_valid_init_data(self):
        client, _ = _mini_app_client()
        with patch("util.telegram_auth.TELEGRAM_BOT_TOKEN", TOKEN), \
             patch("web.api._MINI_APP_OWNER_CACHE", set()), \
             patch.dict("os.environ", {"TELEGRAM_USER_IDS": ""}):
            import urllib.parse
            r = client.get("/charts?init_data=" + urllib.parse.quote(_init_data(), safe=""))
            assert r.status_code == 200
            assert b"telegram-web-app.js" in r.data

    def test_denied_for_foreign_user_when_owner_locked(self):
        client, _ = _mini_app_client()
        with patch("util.telegram_auth.TELEGRAM_BOT_TOKEN", TOKEN), \
             patch("web.api._MINI_APP_OWNER_CACHE", {999}):
            r = client.get(f"/charts?init_data={_init_data(user_id=UID)}")
            assert r.status_code == 401


class TestMiniAppApiGate:
    def test_allows_trends_for_owner(self):
        client, mock_pool = _mini_app_client()
        with patch("util.telegram_auth.TELEGRAM_BOT_TOKEN", TOKEN), \
             patch("web.api._MINI_APP_OWNER_CACHE", {UID}), \
             patch.dict("os.environ", {"TELEGRAM_USER_IDS": str(UID)}):
            r = client.get(
                "/api/trends?from=2026-01-01&to=2026-01-31&group_by=month",
                headers={"X-Telegram-Init-Data": _init_data()},
            )
            assert r.status_code == 200

    def test_blocks_mutating_endpoints(self):
        client, _ = _mini_app_client()
        with patch("util.telegram_auth.TELEGRAM_BOT_TOKEN", TOKEN), \
             patch("web.api._MINI_APP_OWNER_CACHE", {UID}):
            r = client.post(
                "/api/analyze",
                json={"period": "2026-01"},
                headers={"X-Telegram-Init-Data": _init_data()},
            )
            assert r.status_code == 403

    def test_blocks_with_bad_signature(self):
        client, _ = _mini_app_client()
        with patch("util.telegram_auth.TELEGRAM_BOT_TOKEN", TOKEN):
            r = client.get(
                "/api/trends?from=2026-01-01&to=2026-01-31&group_by=day",
                headers={"X-Telegram-Init-Data": "user=%7B%22id%22%3A1%7D&hash=deadbeef"},
            )
            assert r.status_code == 401

    def test_no_header_keeps_legacy_behavior(self):
        """Desktop dashboard (no initData) is not affected by the gate."""
        client, _ = _mini_app_client()
        r = client.get(
            "/api/trends?from=2026-01-01&to=2026-01-31&group_by=month"
        )
        assert r.status_code == 200
