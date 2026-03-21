"""Tests for LLM settings management (util/llm_settings.py)."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from util.llm_settings import (
    apply_env_overrides,
    build_provider_chain_from_settings,
    _ensure_table,
    _seed_defaults,
    get_all_settings,
    get_enabled_settings,
    upsert_setting,
    delete_setting,
)


def _make_setting(**kwargs):
    defaults = {
        "id": 1,
        "provider": "local",
        "name": "primary",
        "model": "test-model",
        "url": "http://localhost:1234/v1/chat/completions",
        "api_key": None,
        "timeout": 30,
        "priority": 0,
        "enabled": True,
    }
    defaults.update(kwargs)
    return defaults


class TestApplyEnvOverrides:
    def test_local_url_override(self):
        settings = [_make_setting(provider="local", name="primary", url="http://old")]
        with patch.dict(os.environ, {"LOCAL_LLM_URL": "http://new"}, clear=False):
            result = apply_env_overrides(settings)
        assert result[0]["url"] == "http://new"

    def test_local_model_override(self):
        settings = [_make_setting(provider="local", name="primary", model="old-model")]
        with patch.dict(os.environ, {"LOCAL_LLM_MODEL": "new-model"}, clear=False):
            result = apply_env_overrides(settings)
        assert result[0]["model"] == "new-model"

    def test_local_timeout_override(self):
        settings = [_make_setting(provider="local", name="primary", timeout=15)]
        with patch.dict(os.environ, {"LOCAL_LLM_TIMEOUT": "60"}, clear=False):
            result = apply_env_overrides(settings)
        assert result[0]["timeout"] == 60

    def test_openrouter_model_override(self):
        settings = [_make_setting(provider="openrouter", name="primary", model="old")]
        with patch.dict(os.environ, {"OPENROUTER_LLM_VERSION": "new-or-model"}, clear=False):
            result = apply_env_overrides(settings)
        assert result[0]["model"] == "new-or-model"

    def test_openrouter_api_key_override(self):
        settings = [_make_setting(provider="openrouter", name="primary", api_key=None)]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}, clear=False):
            result = apply_env_overrides(settings)
        assert result[0]["api_key"] == "secret"

    def test_openrouter_url_override(self):
        settings = [_make_setting(provider="openrouter", name="primary", url="http://old")]
        with patch.dict(os.environ, {"OPENROUTER_URL": "http://new-or"}, clear=False):
            result = apply_env_overrides(settings)
        assert result[0]["url"] == "http://new-or"

    def test_fallback_models_override(self):
        settings = [
            _make_setting(provider="openrouter", name="primary", priority=10),
            _make_setting(provider="openrouter", name="fallback_1", model="old-fb", priority=20),
        ]
        with patch.dict(os.environ, {"OPENROUTER_FALLBACK_MODELS": "fb-a,fb-b"}, clear=False):
            result = apply_env_overrides(settings)
        # Primary should remain, old fallback replaced
        or_settings = [s for s in result if s["provider"] == "openrouter"]
        assert len(or_settings) == 3  # primary + 2 new fallbacks
        assert or_settings[0]["name"] == "primary"
        assert or_settings[1]["model"] == "fb-a"
        assert or_settings[2]["model"] == "fb-b"

    def test_no_env_vars_no_change(self):
        settings = [_make_setting(provider="local", name="primary", url="http://orig")]
        env_clear = {
            k: "" for k in [
                "LOCAL_LLM_URL", "LOCAL_LLM_MODEL", "LOCAL_LLM_TIMEOUT",
                "OPENROUTER_API_KEY", "OPENROUTER_LLM_VERSION",
                "OPENROUTER_URL", "OPENROUTER_FALLBACK_MODELS",
            ]
        }
        with patch.dict(os.environ, env_clear, clear=False):
            # Remove the keys entirely
            for k in env_clear:
                os.environ.pop(k, None)
            result = apply_env_overrides(settings)
        assert result[0]["url"] == "http://orig"


class TestBuildProviderChainFromSettings:
    def test_local_provider_no_auth(self):
        settings = [_make_setting(provider="local")]
        chain = build_provider_chain_from_settings(settings)
        assert len(chain) == 1
        url, headers, model, timeout = chain[0]
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

    def test_openrouter_provider_with_api_key(self):
        settings = [_make_setting(provider="openrouter", api_key="sk-test")]
        chain = build_provider_chain_from_settings(settings)
        url, headers, model, timeout = chain[0]
        assert headers["Authorization"] == "Bearer sk-test"

    def test_openrouter_provider_falls_back_to_env_key(self):
        settings = [_make_setting(provider="openrouter", api_key=None)]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env-key"}, clear=False):
            chain = build_provider_chain_from_settings(settings)
        url, headers, model, timeout = chain[0]
        assert headers["Authorization"] == "Bearer env-key"

    def test_disabled_settings_excluded(self):
        settings = [
            _make_setting(provider="local", enabled=True),
            _make_setting(provider="openrouter", enabled=False),
        ]
        chain = build_provider_chain_from_settings(settings)
        assert len(chain) == 1

    def test_generic_provider_with_api_key(self):
        settings = [_make_setting(provider="custom", api_key="custom-key")]
        chain = build_provider_chain_from_settings(settings)
        url, headers, model, timeout = chain[0]
        assert headers["Authorization"] == "Bearer custom-key"

    def test_generic_provider_without_api_key(self):
        settings = [_make_setting(provider="custom", api_key=None)]
        chain = build_provider_chain_from_settings(settings)
        url, headers, model, timeout = chain[0]
        assert "Authorization" not in headers

    def test_chain_preserves_order(self):
        settings = [
            _make_setting(provider="local", model="local-m", priority=0),
            _make_setting(provider="openrouter", name="primary", model="or-m", priority=10),
        ]
        chain = build_provider_chain_from_settings(settings)
        assert chain[0][2] == "local-m"
        assert chain[1][2] == "or-m"

    def test_empty_settings(self):
        chain = build_provider_chain_from_settings([])
        assert chain == []


class TestEnsureTable:
    @pytest.mark.asyncio
    async def test_creates_table_if_not_exists(self):
        mock_pool = AsyncMock()
        with patch("util.llm_settings._TABLE_CREATED", False):
            await _ensure_table(mock_pool)
        mock_pool.execute.assert_called_once()
        call_args = mock_pool.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS llm_settings" in call_args

    @pytest.mark.asyncio
    async def test_skips_creation_if_already_created(self):
        mock_pool = AsyncMock()
        with patch("util.llm_settings._TABLE_CREATED", True):
            await _ensure_table(mock_pool)
        mock_pool.execute.assert_not_called()


class TestSeedDefaults:
    @pytest.mark.asyncio
    async def test_seeds_defaults_when_table_empty(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        await _seed_defaults(mock_pool)
        assert mock_pool.execute.call_count == 4

    @pytest.mark.asyncio
    async def test_skips_seeding_when_table_not_empty(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=5)
        await _seed_defaults(mock_pool)
        mock_pool.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_seeds_correct_default_models(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        await _seed_defaults(mock_pool)
        calls = mock_pool.execute.call_args_list
        providers = [call[0][1] for call in calls]
        assert "local" in providers
        assert "openrouter" in providers


class TestGetAllSettings:
    @pytest.mark.asyncio
    async def test_returns_all_settings_ordered_by_priority(self):
        mock_pool = AsyncMock()
        mock_row1 = MagicMock()
        mock_row1.__getitem__ = lambda self, key: 1 if key == "id" else ("local" if key == "provider" else None)
        mock_row1.__iter__ = lambda self: iter(["id", 1, "provider", "local"])
        mock_row1.keys = lambda: ["id", "provider", "name", "model", "url", "api_key", "timeout", "priority", "enabled"]
        mock_row2 = MagicMock()
        mock_row2.__getitem__ = lambda self, key: 2 if key == "id" else ("openrouter" if key == "provider" else None)
        mock_row2.__iter__ = lambda self: iter(["id", 2, "provider", "openrouter"])
        mock_row2.keys = lambda: ["id", "provider", "name", "model", "url", "api_key", "timeout", "priority", "enabled"]
        mock_pool.fetch = AsyncMock(return_value=[mock_row1, mock_row2])
        
        with patch("util.llm_settings._TABLE_CREATED", True), \
             patch("util.llm_settings._seed_defaults", new_callable=AsyncMock):
            result = await get_all_settings(mock_pool)
        
        assert len(result) == 2
        mock_pool.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_ensure_table_before_fetch(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        
        with patch("util.llm_settings._TABLE_CREATED", False):
            await get_all_settings(mock_pool)
        
        assert mock_pool.execute.called


class TestGetEnabledSettings:
    @pytest.mark.asyncio
    async def test_returns_only_enabled_settings(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: True if key == "enabled" else 1
        mock_row.__iter__ = lambda self: iter(["id", 1, "enabled", True])
        mock_row.keys = lambda: ["id", "provider", "name", "model", "url", "api_key", "timeout", "priority", "enabled"]
        mock_pool.fetch = AsyncMock(return_value=[mock_row])
        
        with patch("util.llm_settings._TABLE_CREATED", True):
            result = await get_enabled_settings(mock_pool)
        
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_orders_by_priority(self):
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        
        with patch("util.llm_settings._TABLE_CREATED", True):
            await get_enabled_settings(mock_pool)
        
        call_args = mock_pool.fetch.call_args[0][0]
        assert "WHERE enabled = true ORDER BY priority" in call_args


class TestUpsertSetting:
    @pytest.mark.asyncio
    async def test_inserts_new_setting(self):
        mock_pool = AsyncMock()
        with patch("util.llm_settings._TABLE_CREATED", True):
            await upsert_setting(mock_pool, "local", "primary", "model-a", "http://test", timeout=60)
        assert mock_pool.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_updates_existing_setting_on_conflict(self):
        mock_pool = AsyncMock()
        with patch("util.llm_settings._TABLE_CREATED", True):
            await upsert_setting(mock_pool, "openrouter", "primary", "model-b", "http://new")
        call_args = mock_pool.execute.call_args[0]
        assert "openrouter" in call_args  # provider in args
        assert "model-b" in call_args     # model in args

    @pytest.mark.asyncio
    async def test_sets_default_values(self):
        mock_pool = AsyncMock()
        with patch("util.llm_settings._TABLE_CREATED", True):
            await upsert_setting(mock_pool, "local", "test", "m", "u")
        call_args = mock_pool.execute.call_args[0]
        # First arg is SQL, remaining args are values in order: provider, name, model, url, api_key, timeout, priority, enabled
        values = call_args[1:]  # Skip SQL statement
        assert values[0] == "local"       # provider
        assert values[1] == "test"        # name
        assert values[2] == "m"           # model
        assert values[3] == "u"           # url
        assert values[5] == 30            # default timeout
        assert values[6] == 0             # default priority
        assert values[7] is True          # default enabled


class TestDeleteSetting:
    @pytest.mark.asyncio
    async def test_deletes_setting_and_returns_true(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        result = await delete_setting(mock_pool, 42)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_nothing_deleted(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 0")
        result = await delete_setting(mock_pool, 999)
        assert result is False

    @pytest.mark.asyncio
    async def test_calls_ensure_table_before_delete(self):
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="DELETE 1")
        
        with patch("util.llm_settings._TABLE_CREATED", False):
            await delete_setting(mock_pool, 1)
        
        assert mock_pool.execute.called
