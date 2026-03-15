"""Tests for LLM settings management (util/llm_settings.py)."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from util.llm_settings import (
    apply_env_overrides,
    build_provider_chain_from_settings,
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
