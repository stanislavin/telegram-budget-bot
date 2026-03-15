"""
LLM provider settings management.

Settings are stored in PostgreSQL and can be overridden by environment variables.
Env vars always take precedence over DB values.
"""

import logging
import os

logger = logging.getLogger(__name__)

_TABLE_CREATED = False


async def _ensure_table(pool):
    """Create llm_settings table if it doesn't exist."""
    global _TABLE_CREATED
    if not _TABLE_CREATED:
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS llm_settings (
                id SERIAL PRIMARY KEY,
                provider VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                model VARCHAR NOT NULL,
                url VARCHAR NOT NULL,
                api_key VARCHAR,
                timeout INTEGER NOT NULL DEFAULT 30,
                priority INTEGER NOT NULL DEFAULT 0,
                enabled BOOLEAN NOT NULL DEFAULT true,
                UNIQUE(provider, name)
            )
        """)
        _TABLE_CREATED = True


async def _seed_defaults(pool):
    """Insert default settings if the table is empty."""
    count = await pool.fetchval("SELECT COUNT(*) FROM llm_settings")
    if count > 0:
        return

    defaults = [
        {
            "provider": "local",
            "name": "primary",
            "model": "zai-org/glm-4.7-flash",
            "url": "http://100.89.78.122:1234/v1/chat/completions",
            "api_key": None,
            "timeout": 60,
            "priority": 0,
            "enabled": True,
        },
        {
            "provider": "openrouter",
            "name": "primary",
            "model": "arcee-ai/trinity-mini:free",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "api_key": None,
            "timeout": 30,
            "priority": 10,
            "enabled": True,
        },
        {
            "provider": "openrouter",
            "name": "fallback_1",
            "model": "z-ai/glm-4.5-air:free",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "api_key": None,
            "timeout": 30,
            "priority": 20,
            "enabled": True,
        },
        {
            "provider": "openrouter",
            "name": "fallback_2",
            "model": "openrouter/aurora-alpha",
            "url": "https://openrouter.ai/api/v1/chat/completions",
            "api_key": None,
            "timeout": 30,
            "priority": 30,
            "enabled": True,
        },
    ]

    for d in defaults:
        await pool.execute(
            """INSERT INTO llm_settings (provider, name, model, url, api_key, timeout, priority, enabled)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            d["provider"], d["name"], d["model"], d["url"],
            d["api_key"], d["timeout"], d["priority"], d["enabled"],
        )
    logger.info("Seeded default LLM settings")


async def get_all_settings(pool):
    """Return all LLM settings ordered by priority."""
    await _ensure_table(pool)
    await _seed_defaults(pool)
    rows = await pool.fetch(
        "SELECT * FROM llm_settings ORDER BY priority, id"
    )
    return [dict(r) for r in rows]


async def get_enabled_settings(pool):
    """Return enabled LLM settings ordered by priority."""
    await _ensure_table(pool)
    await _seed_defaults(pool)
    rows = await pool.fetch(
        "SELECT * FROM llm_settings WHERE enabled = true ORDER BY priority, id"
    )
    return [dict(r) for r in rows]


async def upsert_setting(pool, provider, name, model, url, api_key=None,
                         timeout=30, priority=0, enabled=True):
    """Insert or update an LLM setting."""
    await _ensure_table(pool)
    await pool.execute(
        """INSERT INTO llm_settings (provider, name, model, url, api_key, timeout, priority, enabled)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
           ON CONFLICT (provider, name) DO UPDATE SET
               model = EXCLUDED.model,
               url = EXCLUDED.url,
               api_key = EXCLUDED.api_key,
               timeout = EXCLUDED.timeout,
               priority = EXCLUDED.priority,
               enabled = EXCLUDED.enabled""",
        provider, name, model, url, api_key, timeout, priority, enabled,
    )


async def delete_setting(pool, setting_id):
    """Delete an LLM setting by id."""
    await _ensure_table(pool)
    result = await pool.execute("DELETE FROM llm_settings WHERE id = $1", setting_id)
    return result != "DELETE 0"


def apply_env_overrides(settings):
    """Apply environment variable overrides to DB settings.

    Env vars take precedence over DB values:
    - LOCAL_LLM_URL / LOCAL_LLM_MODEL / LOCAL_LLM_TIMEOUT override local primary
    - OPENROUTER_LLM_VERSION overrides openrouter primary model
    - OPENROUTER_URL overrides openrouter URL
    - OPENROUTER_API_KEY overrides openrouter api_key
    - OPENROUTER_FALLBACK_MODELS overrides/adds openrouter fallback models
    """
    env_local_url = os.getenv("LOCAL_LLM_URL")
    env_local_model = os.getenv("LOCAL_LLM_MODEL")
    env_local_timeout = os.getenv("LOCAL_LLM_TIMEOUT")
    env_or_key = os.getenv("OPENROUTER_API_KEY")
    env_or_version = os.getenv("OPENROUTER_LLM_VERSION")
    env_or_url = os.getenv("OPENROUTER_URL")
    env_or_fallbacks = os.getenv("OPENROUTER_FALLBACK_MODELS")

    for s in settings:
        if s["provider"] == "local" and s["name"] == "primary":
            if env_local_url:
                s["url"] = env_local_url
            if env_local_model:
                s["model"] = env_local_model
            if env_local_timeout:
                s["timeout"] = int(env_local_timeout)

        if s["provider"] == "openrouter":
            if env_or_key:
                s["api_key"] = env_or_key
            if env_or_url:
                s["url"] = env_or_url
            if s["name"] == "primary" and env_or_version:
                s["model"] = env_or_version

    # Handle OPENROUTER_FALLBACK_MODELS env var override
    if env_or_fallbacks:
        fallback_models = [m.strip() for m in env_or_fallbacks.split(",") if m.strip()]
        # Find existing openrouter fallbacks
        existing_fallbacks = [s for s in settings if s["provider"] == "openrouter" and s["name"] != "primary"]
        # Find the primary openrouter to get base priority and url
        or_primary = next((s for s in settings if s["provider"] == "openrouter" and s["name"] == "primary"), None)
        base_priority = or_primary["priority"] if or_primary else 10
        base_url = or_primary["url"] if or_primary else "https://openrouter.ai/api/v1/chat/completions"

        # Remove existing fallbacks from settings
        settings[:] = [s for s in settings if not (s["provider"] == "openrouter" and s["name"] != "primary")]

        # Add env var fallbacks
        for i, model in enumerate(fallback_models):
            settings.append({
                "provider": "openrouter",
                "name": f"fallback_{i + 1}",
                "model": model,
                "url": env_or_url or base_url,
                "api_key": env_or_key,
                "timeout": 30,
                "priority": base_priority + (i + 1) * 10,
                "enabled": True,
            })

    return settings


def build_provider_chain_from_settings(settings):
    """Convert settings list into provider chain tuples.

    Returns list of (url, headers, model, timeout).
    """
    chain = []
    for s in settings:
        if not s.get("enabled", True):
            continue

        if s["provider"] == "local":
            headers = {"Content-Type": "application/json"}
        elif s["provider"] == "openrouter":
            api_key = s.get("api_key") or os.getenv("OPENROUTER_API_KEY", "")
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/stanislavin/telegram-budget-bot",
            }
        else:
            # Generic provider — use api_key if present
            headers = {"Content-Type": "application/json"}
            if s.get("api_key"):
                headers["Authorization"] = f"Bearer {s['api_key']}"

        chain.append((s["url"], headers, s["model"], s["timeout"]))

    return chain
