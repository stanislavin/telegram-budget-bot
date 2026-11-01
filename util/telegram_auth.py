"""Validation of Telegram Mini App launch data (WebApp.initData).

Implements the official algorithm from
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

The bot's own token is used as the secret key, so only a Mini App launched
from *this* bot inside a real Telegram client produces a valid signature.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Optional
from urllib.parse import parse_qsl

from util.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

# Reject launches older than this (Telegram passes auth_date as unix time).
MAX_AUTH_AGE_SECONDS = 24 * 3600

# When True, signature checks are skipped (local development only).
INSECURE_SKIP_VALIDATION = os.getenv('MINI_APP_INSECURE', '').strip().lower() in (
    '1', 'true', 'yes', 'on',
)


def _webapp_secret(token: str) -> bytes:
    """Derive the HMAC key for a bot token: HMAC_SHA256('WebAppData', token)."""
    return hmac.new(b'WebAppData', token.encode('utf-8'), hashlib.sha256).digest()


def _constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


def validate_init_data(init_data: Optional[str], token: Optional[str] = None) -> bool:
    """Return True if ``init_data`` carries a valid Telegram hash.

    Args:
        init_data: Raw query string taken from ``Telegram.WebApp.initData``.
        token: Bot token override; defaults to ``TELEGRAM_BOT_TOKEN``.

    Returns:
        False when the data is missing, malformed, expired, or unsigned.
        When ``MINI_APP_INSECURE`` is set, always returns True (dev only).
    """
    if INSECURE_SKIP_VALIDATION:
        logger.warning("MINI_APP_INSECURE is set — skipping Mini App initData validation")
        return True
    if not init_data:
        return False
    token = token or TELEGRAM_BOT_TOKEN
    if not token:
        logger.error("Cannot validate initData: TELEGRAM_BOT_TOKEN is not configured")
        return False

    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return False

    received_hash = params.pop('hash', None)
    if not received_hash:
        return False

    auth_date = params.get('auth_date')
    if auth_date:
        try:
            age = time.time() - int(auth_date)
        except ValueError:
            return False
        if age > MAX_AUTH_AGE_SECONDS:
            logger.info("Rejected Mini App launch: initData is %ss old", int(age))
            return False

    data_check_string = '\n'.join(
        f'{key}={params[key]}' for key in sorted(params)
    )
    secret = _webapp_secret(token)
    computed = hmac.new(secret, data_check_string.encode('utf-8'), hashlib.sha256).hexdigest()
    return _constant_time_equal(computed, received_hash)


def parse_init_user(init_data: Optional[str]) -> Optional[int]:
    """Extract ``WebAppUser.id`` from initData (unverified — validate first).

    Returns the user id as int, or None when absent/unparseable.
    """
    if not init_data:
        return None
    import json
    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True))
        user = json.loads(params.get('user', '{}'))
        uid = user.get('id')
        return int(uid) if uid is not None else None
    except Exception:
        return None
