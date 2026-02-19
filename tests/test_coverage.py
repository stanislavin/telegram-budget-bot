import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import asyncio

from util.telegram import (
    button_callback,
    pending_expenses,
    recently_processed_expenses,
    expense_locks,
)
from util.sheets import get_daily_stats

@pytest.fixture(autouse=True)
def clear_state():
    pending_expenses.clear()
    recently_processed_expenses.clear()
    expense_locks.clear()
    yield

@pytest.mark.asyncio
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
@patch('util.telegram.get_daily_stats', new_callable=AsyncMock)
async def test_button_callback_manual_sheet_retry_success(mock_get_daily_stats, mock_save_to_sheets):
    """Test successful manual retry for saving to sheets."""
    expense_id = "retry-123"
    pending_expenses[expense_id] = {
        'amount': 100.0,
        'currency': 'RUB',
        'category': 'Food',
        'description': 'Lunch',
        'status_message': AsyncMock()
    }
    
    update = MagicMock(spec=Update)
    update.callback_query = AsyncMock()
    update.callback_query.data = f"action:manual_sheet_retry|id:{expense_id}"
    
    mock_save_to_sheets.return_value = (True, None)
    mock_get_daily_stats.return_value = ({'RUB': 100.0}, {})
    
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    
    await button_callback(update, context)
    
    update.callback_query.answer.assert_called_once()
    mock_save_to_sheets.assert_called_once_with(100.0, 'RUB', 'Food', 'Lunch')
    
    calls = update.callback_query.edit_message_text.call_args_list
    assert any("Retrying to save to spreadsheet" in str(c) for c in calls)
    assert any("Saved: 100.0 RUB" in str(c) for c in calls)
    assert expense_id not in pending_expenses

@pytest.mark.asyncio
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_button_callback_manual_sheet_retry_failure(mock_save_to_sheets):
    """Test failed manual retry for saving to sheets."""
    expense_id = "retry-456"
    pending_expenses[expense_id] = {
        'amount': 50.0,
        'currency': 'EUR',
        'category': 'Transport',
        'description': 'Bus',
        'status_message': AsyncMock()
    }
    
    update = MagicMock(spec=Update)
    update.callback_query = AsyncMock()
    update.callback_query.data = f"action:manual_sheet_retry|id:{expense_id}"
    
    mock_save_to_sheets.return_value = (False, "Network error")
    
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    
    await button_callback(update, context)
    
    assert any("Error saving to spreadsheet: Network error" in str(c) for c in update.callback_query.edit_message_text.call_args_list)
    # Should show retry button again
    update.callback_query.edit_message_reply_markup.assert_called_once()

@pytest.mark.asyncio
async def test_button_callback_manual_sheet_retry_no_data():
    """Test manual retry when expense data is missing."""
    expense_id = "missing-123"
    update = MagicMock(spec=Update)
    update.callback_query = AsyncMock()
    update.callback_query.data = f"action:manual_sheet_retry|id:{expense_id}"
    
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    
    await button_callback(update, context)
    
    # Check that it edit_message_text was called with the error message
    calls = update.callback_query.edit_message_text.call_args_list
    assert any("expense data no longer available" in str(c) for c in calls)

@pytest.mark.asyncio
@patch('util.sheets.get_google_sheets_service')
async def test_get_daily_stats_error(mock_get_service):
    """Test error handling in get_daily_stats."""
    mock_get_service.side_effect = Exception("Auth error")
    
    with pytest.raises(Exception, match="Auth error"):
        await get_daily_stats()
