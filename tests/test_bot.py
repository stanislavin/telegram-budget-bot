"""
Tests for the bot entrypoint wiring.
"""

from unittest.mock import patch, MagicMock

import bot


def test_main_runs_polling_when_enabled():
    """Bot should start all services when polling is enabled."""
    with patch('bot.start_health_check') as mock_health, \
         patch('bot.start_nudge') as mock_nudge, \
         patch('bot.start_telegram_polling') as mock_poll:
        mock_health.return_value = MagicMock()

        with patch('bot.RUN_TELEGRAM_BOT', True):
            bot.main()

    mock_health.assert_called_once()
    mock_nudge.assert_called_once()
    mock_poll.assert_called_once()


def test_main_health_only_when_disabled():
    """Bot should keep HTTP server alive when polling is disabled."""
    with patch('bot.start_health_check') as mock_health, \
         patch('bot.start_nudge') as mock_nudge, \
         patch('bot.start_telegram_polling') as mock_poll:
        health_thread = MagicMock()
        mock_health.return_value = health_thread

        with patch('bot.RUN_TELEGRAM_BOT', False):
            bot.main()

    mock_health.assert_called_once()
    mock_nudge.assert_called_once()
    mock_poll.assert_not_called()
    health_thread.join.assert_called_once()
