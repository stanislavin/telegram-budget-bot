import logging
import requests
import asyncio

from util.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_LLM_VERSION,
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_URL,
    LOCAL_LLM_URL,
    LOCAL_LLM_MODEL,
    LOCAL_LLM_TIMEOUT,
    DATABASE_URL,
    get_llm_prompt
)
from util.retry_handler import with_retry

logger = logging.getLogger(__name__)

# Define supported currencies
SUPPORTED_CURRENCIES = {'RSD', 'EUR', 'RUB'}

VALID_SPENDING_TYPES = {'need', 'want', 'invest', 'wellbeing'}


def _call_chat_completion(url, headers, model, messages, timeout=30):
    """Make a chat completion request and return (content, model)."""
    data = {"model": model, "messages": messages}
    response = requests.post(url, headers=headers, json=data, timeout=timeout)
    if 400 <= response.status_code < 500:
        raise ValueError(f"HTTP {response.status_code}: {response.text}")
    response.raise_for_status()
    content = response.json()['choices'][0]['message']['content'].strip()
    return content, model


def _build_provider_chain():
    """Build ordered list of (url, headers, model, timeout) from env vars (static fallback)."""
    chain = []

    # Preferred: local LLM on tailnet (no auth needed)
    if LOCAL_LLM_URL and LOCAL_LLM_MODEL:
        chain.append((
            LOCAL_LLM_URL,
            {"Content-Type": "application/json"},
            LOCAL_LLM_MODEL,
            LOCAL_LLM_TIMEOUT,
        ))

    # Fallback: OpenRouter models
    openrouter_headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/stanislavin/telegram-budget-bot",
    }
    for model in [OPENROUTER_LLM_VERSION] + OPENROUTER_FALLBACK_MODELS:
        chain.append((OPENROUTER_URL, openrouter_headers, model, 30))

    return chain


async def _build_provider_chain_dynamic(pool=None):
    """Build provider chain from DB settings with env var overrides.

    Args:
        pool: optional asyncpg pool to use (e.g. web API's own pool).
              If None, uses the bot's pool from util.postgres.

    Falls back to static _build_provider_chain() if DB is unavailable.
    """
    if not DATABASE_URL:
        return _build_provider_chain()

    try:
        from util.llm_settings import (
            get_enabled_settings,
            apply_env_overrides,
            build_provider_chain_from_settings,
        )
        if pool is None:
            from util.postgres import get_pool
            pool = await get_pool()
        settings = await get_enabled_settings(pool)
        settings = apply_env_overrides(settings)
        chain = build_provider_chain_from_settings(settings)
        if chain:
            return chain
    except Exception as e:
        logger.warning(f"Failed to load LLM settings from DB, using static config: {e}")

    return _build_provider_chain()


def _parse_openrouter_response(formatted_text: str):
    """Helper function to parse OpenRouter API response."""
    parts = formatted_text.split(',')
    if len(parts) == 5:
        amount = float(parts[0])
        currency = parts[1].upper()
        category = parts[2]
        spending_type = parts[3].strip().lower()
        description = parts[4]
    elif len(parts) == 4:
        # Backwards compatibility: no spending_type field
        amount = float(parts[0])
        currency = parts[1].upper()
        category = parts[2]
        spending_type = None
        description = parts[3]
    else:
        raise ValueError("Failed to parse OpenRouter response")

    # Ensure currency defaults to RUB if not specified or invalid
    if currency not in SUPPORTED_CURRENCIES:
        logger.warning(f"Invalid or ambiguous currency '{currency}' detected, defaulting to RUB")
        currency = 'RUB'

    # Validate spending_type
    if spending_type not in VALID_SPENDING_TYPES:
        logger.warning(f"Invalid spending_type '{spending_type}', defaulting to None")
        spending_type = None

    return amount, currency, category, spending_type, description


@with_retry(max_retries=1, error_message="Error processing with OpenRouter")
async def process_with_openrouter(message: str) -> tuple:
    """Process message using LLM and return formatted data and model used."""
    prompt = get_llm_prompt() + "\n\nDescription of expense is: " + message
    messages = [{"role": "user", "content": prompt}]

    chain = await _build_provider_chain_dynamic()

    last_error = None
    for url, headers, model, timeout in chain:
        try:
            logger.info(f"Attempting to process with model: {model}")
            content, used_model = await asyncio.to_thread(
                _call_chat_completion, url, headers, model, messages, timeout
            )
            parsed_data = _parse_openrouter_response(content)
            return parsed_data, used_model
        except Exception as e:
            logger.error(f"Error with model {model}: {str(e)}")
            last_error = str(e)
            continue

    raise RuntimeError(f"All models failed. Last error: {last_error}")
