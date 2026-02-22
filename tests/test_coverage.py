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
    _dual_save,
    _dual_delete,
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


# ---------- Dual-write helpers ----------

@pytest.mark.asyncio
@patch('util.telegram.DATABASE_URL', 'postgresql://fake')
@patch('util.telegram.save_to_postgres', new_callable=AsyncMock, create=True)
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_dual_save_both_succeed(mock_sheets, mock_pg):
    """Test _dual_save calls both stores when DATABASE_URL is set."""
    mock_sheets.return_value = (True, None)
    mock_pg.return_value = (True, None)

    result = await _dual_save(10, 'USD', 'food', 'lunch')

    assert result == (True, None)
    mock_sheets.assert_awaited_once_with(10, 'USD', 'food', 'lunch')
    mock_pg.assert_awaited_once_with(10, 'USD', 'food', 'lunch')


@pytest.mark.asyncio
@patch('util.telegram.DATABASE_URL', 'postgresql://fake')
@patch('util.telegram.save_to_postgres', new_callable=AsyncMock, create=True)
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_dual_save_pg_fails_nonblocking(mock_sheets, mock_pg):
    """Test that Postgres failure doesn't block Sheets result."""
    mock_sheets.return_value = (True, None)
    mock_pg.side_effect = Exception("PG down")

    result = await _dual_save(10, 'USD', 'food', 'lunch')

    assert result == (True, None)  # Sheets result returned


@pytest.mark.asyncio
@patch('util.telegram.DATABASE_URL', 'postgresql://fake')
@patch('util.telegram.save_to_postgres', new_callable=AsyncMock, create=True)
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_dual_save_pg_returns_error_tuple(mock_sheets, mock_pg):
    """Test non-blocking log when Postgres returns an error tuple."""
    mock_sheets.return_value = (True, None)
    mock_pg.return_value = (None, "insert failed")

    result = await _dual_save(10, 'USD', 'food', 'lunch')

    assert result == (True, None)


@pytest.mark.asyncio
@patch('util.telegram.DATABASE_URL', 'postgresql://fake')
@patch('util.telegram.save_to_postgres', new_callable=AsyncMock, create=True)
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_dual_save_sheets_exception_propagates(mock_sheets, mock_pg):
    """Test that Sheets exception is re-raised."""
    mock_sheets.side_effect = Exception("Sheets down")
    mock_pg.return_value = (True, None)

    with pytest.raises(Exception, match="Sheets down"):
        await _dual_save(10, 'USD', 'food', 'lunch')


@pytest.mark.asyncio
@patch('util.telegram.DATABASE_URL', 'postgresql://fake')
@patch('util.telegram.delete_last_expense_pg', new_callable=AsyncMock, create=True)
@patch('util.telegram.delete_last_expense', new_callable=AsyncMock)
async def test_dual_delete_both_succeed(mock_sheets_del, mock_pg_del):
    """Test _dual_delete calls both stores when DATABASE_URL is set."""
    expense_info = {'amount': '10', 'currency': 'USD', 'category': 'food', 'description': 'lunch'}
    mock_sheets_del.return_value = (expense_info, None)
    mock_pg_del.return_value = (expense_info, None)

    result = await _dual_delete()

    assert result == (expense_info, None)
    mock_sheets_del.assert_awaited_once()
    mock_pg_del.assert_awaited_once()


@pytest.mark.asyncio
@patch('util.telegram.DATABASE_URL', 'postgresql://fake')
@patch('util.telegram.delete_last_expense_pg', new_callable=AsyncMock, create=True)
@patch('util.telegram.delete_last_expense', new_callable=AsyncMock)
async def test_dual_delete_pg_fails_nonblocking(mock_sheets_del, mock_pg_del):
    """Test that Postgres delete failure doesn't block Sheets result."""
    expense_info = {'amount': '10', 'currency': 'USD', 'category': 'food', 'description': 'lunch'}
    mock_sheets_del.return_value = (expense_info, None)
    mock_pg_del.side_effect = Exception("PG down")

    result = await _dual_delete()

    assert result == (expense_info, None)


@pytest.mark.asyncio
@patch('util.telegram.DATABASE_URL', 'postgresql://fake')
@patch('util.telegram.delete_last_expense_pg', new_callable=AsyncMock, create=True)
@patch('util.telegram.delete_last_expense', new_callable=AsyncMock)
async def test_dual_delete_sheets_exception_propagates(mock_sheets_del, mock_pg_del):
    """Test that Sheets delete exception is re-raised."""
    mock_sheets_del.side_effect = Exception("Sheets down")
    mock_pg_del.return_value = ({'amount': '10'}, None)

    with pytest.raises(Exception, match="Sheets down"):
        await _dual_delete()
