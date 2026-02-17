import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
import asyncio

from util.telegram import (
    start,
    help_command,
    handle_message,
    _process_expense,
    _schedule_auto_confirm,
    button_callback,
    auto_confirm_expense,
    pending_expenses,
    recently_processed_expenses,
    expense_locks,
    load_categories,
    CATEGORIES,
    summary_command,
    create_application,
    start_telegram_polling,
)
from util.message_queue import _chat_queues, _chat_workers


async def _drain_chat_queue(chat_id: str):
    """Helper to wait for all queued items for a given chat to be processed."""
    queue = _chat_queues.get(str(chat_id))
    if queue:
        await queue.join()

# Mock the CATEGORIES to ensure consistent test results
@pytest.fixture(autouse=True)
def mock_categories():
    with patch('util.telegram.CATEGORIES', ['Food', 'Transport', 'Utilities', 'Rent', 'Salary']):
        yield


@pytest.fixture(autouse=True)
def clear_expense_state():
    pending_expenses.clear()
    recently_processed_expenses.clear()
    expense_locks.clear()
    # Clean up per-chat queues between tests
    for task in list(_chat_workers.values()):
        try:
            task.cancel()
        except RuntimeError:
            pass  # event loop already closed
    _chat_queues.clear()
    _chat_workers.clear()
    yield
    pending_expenses.clear()
    recently_processed_expenses.clear()
    expense_locks.clear()
    for task in list(_chat_workers.values()):
        try:
            task.cancel()
        except RuntimeError:
            pass
    _chat_queues.clear()
    _chat_workers.clear()

@pytest.fixture
def mock_update():
    """Fixture to mock a Telegram Update object."""
    update = MagicMock(spec=Update)
    update.message = MagicMock()
    update.message.text = ""
    update.message.reply_text = AsyncMock()
    update.message.edit_text = AsyncMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message.chat_id = 12345
    update.message.message_id = 67890
    return update

@pytest.fixture
def mock_context():
    """Fixture to mock a Telegram ContextTypes.DEFAULT_TYPE object."""
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.bot = MagicMock()
    return context

@pytest.mark.asyncio
async def test_start_command(mock_update, mock_context):
    """Test the /start command."""
    await start(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once()
    args, kwargs = mock_update.message.reply_text.call_args
    assert 'Hi! I\'m your budget tracking bot.' in args[0]
    assert isinstance(kwargs['reply_markup'], ReplyKeyboardMarkup)

@pytest.mark.asyncio
async def test_help_command(mock_update, mock_context):
    """Test the /help command."""
    await help_command(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once()
    args, kwargs = mock_update.message.reply_text.call_args
    assert 'Send me messages in the format:' in args[0]
    assert isinstance(kwargs['reply_markup'], ReplyKeyboardMarkup)

@pytest.mark.asyncio
@patch('util.telegram._schedule_auto_confirm')
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_handle_message_expense(mock_save_to_sheets, mock_process_with_openrouter, mock_schedule, mock_update, mock_context):
    """Test handling of a regular expense message."""
    mock_update.message.text = "50 USD food lunch"
    mock_process_with_openrouter.return_value = (((50.0, "USD", "Food", "lunch"), "anthropic/claude-3-opus-20240229"), None)

    # Mock the status message returned by reply_text
    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    await _drain_chat_queue(mock_update.message.chat_id)

    mock_process_with_openrouter.assert_called_once_with("50 USD food lunch")

    expense_id = f"{mock_update.message.chat_id}-{mock_update.message.message_id}"
    assert expense_id in pending_expenses
    assert pending_expenses[expense_id]['amount'] == 50.0

    # Check that edit_text was called with AI analysis message
    edit_calls = mock_status_message.edit_text.call_args_list
    assert any("🤖 Analyzing your expense with AI..." in str(call) for call in edit_calls)

    # Check that the final confirmation message was sent
    final_call = edit_calls[-1]
    final_text = final_call[0][0]
    assert "📊 Please confirm the expense (auto-confirms in 10s):" in final_text
    assert "Amount: 50.0 USD" in final_text
    assert "Category: Food" in final_text
    assert "Description: lunch" in final_text

    mock_schedule.assert_called_once()

@pytest.mark.asyncio
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
async def test_handle_message_openrouter_error(mock_process_with_openrouter, mock_update, mock_context):
    """Test handling of an OpenRouter error during message processing."""
    mock_update.message.text = "invalid message"
    # The new function returns a more specific error message
    mock_process_with_openrouter.return_value = (None, "Error processing with OpenRouter after retry: OpenRouter API error")

    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    await _drain_chat_queue(mock_update.message.chat_id)

    # Check that the error message is in the calls
    expected_error = "❌ Error: Error processing with OpenRouter after retry: OpenRouter API error"
    # Extract the actual arguments passed to edit_text
    actual_calls = [call_args[0] for call_args in mock_status_message.edit_text.call_args_list if call_args and len(call_args) > 0]
    # Check if the expected error text is in any of the actual calls
    assert any(expected_error in actual_call for actual_call in actual_calls), f"Expected '{expected_error}' in actual calls: {actual_calls}"
    assert not pending_expenses  # No expense should be pending on error

@pytest.mark.asyncio
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
@patch('util.telegram.get_daily_stats', new_callable=AsyncMock)
async def test_button_callback_confirm_success(mock_get_daily_stats, mock_save_to_sheets, mock_update, mock_context):
    """Test confirming an expense via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        'amount': 75.0,
        'currency': 'EUR',
        'category': 'Transport',
        'description': 'Taxi',
        'status_message': AsyncMock()
    }
    mock_update.callback_query.data = f"action:confirm|id:{expense_id}"
    mock_save_to_sheets.return_value = (True, None)

    mock_get_daily_stats.return_value = ({'EUR': 100.0}, {})

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_save_to_sheets.assert_called_once_with(75.0, 'EUR', 'Transport', 'Taxi')
    calls = mock_update.callback_query.edit_message_text.call_args_list
    assert calls[0][0][0] == "💾 Saving to spreadsheet..."
    assert calls[1][0][0] == "✅ Saved: 75.0 EUR - Transport - Taxi\n\n💸 Total spent today: 100.00 EUR"
    assert expense_id not in pending_expenses

@pytest.mark.asyncio
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_button_callback_confirm_failure(mock_save_to_sheets, mock_update, mock_context):
    """Test confirming an expense with save failure via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        'amount': 75.0,
        'currency': 'EUR',
        'category': 'Transport',
        'description': 'Taxi',
        'status_message': AsyncMock()
    }
    mock_update.callback_query.data = f"action:confirm|id:{expense_id}"
    mock_save_to_sheets.return_value = (False, "Sheets error")

    # Mock the edit_message_reply_markup method as well since it's called when there's an error
    mock_update.callback_query.edit_message_reply_markup = AsyncMock()

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_save_to_sheets.assert_called_once_with(75.0, 'EUR', 'Transport', 'Taxi')
    calls = mock_update.callback_query.edit_message_text.call_args_list
    assert calls[0][0][0] == "💾 Saving to spreadsheet..."
    assert calls[1][0][0] == "❌ Error saving to spreadsheet: Sheets error"
    assert expense_id not in pending_expenses

@pytest.mark.asyncio
async def test_button_callback_cancel(mock_update, mock_context):
    """Test cancelling an expense via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        'amount': 10.0,
        'currency': 'USD',
        'category': 'Food',
        'description': 'Coffee',
        'status_message': AsyncMock()
    }
    mock_update.callback_query.data = f"action:cancel|id:{expense_id}"
    
    await button_callback(mock_update, mock_context)
    
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once_with("Expense cancelled.")
    assert expense_id not in pending_expenses

@pytest.mark.asyncio
async def test_button_callback_change_category(mock_update, mock_context):
    """Test changing category via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        'amount': 20.0,
        'currency': 'USD',
        'category': 'Food',
        'description': 'Dinner',
        'status_message': AsyncMock()
    }
    mock_update.callback_query.data = f"action:change_category|id:{expense_id}"
    
    await button_callback(mock_update, mock_context)
    
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    args, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert 'Select a new category for:' in args[0]
    assert isinstance(kwargs['reply_markup'], InlineKeyboardMarkup)

@pytest.mark.asyncio
async def test_button_callback_select_category(mock_update, mock_context):
    """Test selecting a new category via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        'amount': 20.0,
        'currency': 'USD',
        'category': 'Food',
        'description': 'Dinner',
        'status_message': AsyncMock()
    }
    mock_update.callback_query.data = f"action:select_category|id:{expense_id}|category:Transport"
    
    await button_callback(mock_update, mock_context)
    
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    args, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert 'Category: Transport' in args[0]
    assert isinstance(kwargs['reply_markup'], InlineKeyboardMarkup)
    assert pending_expenses[expense_id]['category'] == 'Transport'

@pytest.mark.asyncio
async def test_button_callback_back(mock_update, mock_context):
    """Test back button via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        'amount': 20.0,
        'currency': 'USD',
        'category': 'Food',
        'description': 'Dinner',
        'status_message': AsyncMock()
    }
    mock_update.callback_query.data = f"action:back|id:{expense_id}"
    
    await button_callback(mock_update, mock_context)
    
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    args, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert 'Please confirm the expense:' in args[0]
    assert isinstance(kwargs['reply_markup'], InlineKeyboardMarkup)

@pytest.mark.asyncio
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
@patch('util.telegram.get_daily_stats', new_callable=AsyncMock)
async def test_auto_confirm_expense_success(mock_get_daily_stats, mock_save_to_sheets, mock_update, mock_context):
    """Test auto-confirmation of an expense."""
    expense_id = "auto-123"
    mock_status_message = AsyncMock()
    pending_expenses[expense_id] = {
        'amount': 30.0,
        'currency': 'GBP',
        'category': 'Utilities',
        'description': 'Electricity bill',
        'status_message': mock_status_message
    }
    mock_save_to_sheets.return_value = (True, None)

    mock_get_daily_stats.return_value = ({'GBP': 50.0}, {})

    # We need to run this in a separate task as auto_confirm_expense has a sleep
    task = asyncio.create_task(auto_confirm_expense(expense_id, mock_context))
    await asyncio.sleep(10.1) # Wait for the sleep to finish
    await task # Ensure the task completes

    mock_save_to_sheets.assert_called_once_with(30.0, 'GBP', 'Utilities', 'Electricity bill')
    mock_status_message.edit_text.assert_called_once_with(
        f"⏱️ Auto-confirmed: 30.0 GBP - Utilities - Electricity bill\n\n💸 Total spent today: 50.00 GBP"
    )
    assert expense_id not in pending_expenses

@pytest.mark.asyncio
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_auto_confirm_expense_failure(mock_save_to_sheets, mock_update, mock_context):
    """Test auto-confirmation failure."""
    expense_id = "auto-456"
    mock_status_message = AsyncMock()
    pending_expenses[expense_id] = {
        'amount': 40.0,
        'currency': 'JPY',
        'category': 'Rent',
        'description': 'Monthly rent',
        'status_message': mock_status_message
    }
    mock_save_to_sheets.return_value = (False, "Auto-save error")

    task = asyncio.create_task(auto_confirm_expense(expense_id, mock_context))
    await asyncio.sleep(10.1)
    await task

    mock_save_to_sheets.assert_called_once_with(40.0, 'JPY', 'Rent', 'Monthly rent')
    mock_status_message.edit_text.assert_called_once_with(
        f"❌ Error auto-saving to spreadsheet: Auto-save error"
    )
    assert expense_id not in pending_expenses


@pytest.mark.asyncio
@patch('util.telegram.asyncio.sleep', new_callable=AsyncMock)
async def test_auto_confirm_no_pending_expense(mock_sleep, mock_context):
    """Auto-confirm should exit early if expense is missing."""
    mock_sleep.return_value = None
    await auto_confirm_expense("missing-id", mock_context)
    mock_sleep.assert_awaited()


@pytest.mark.asyncio
async def test_button_callback_after_auto_processing(mock_update, mock_context):
    """Late button presses should reuse the final text instead of erroring."""
    expense_id = "processed-123"
    processed_text = "⏱️ Auto-confirmed: 10 USD - Food - Snack"
    recently_processed_expenses[expense_id] = processed_text
    mock_update.callback_query.data = f"action:confirm|id:{expense_id}"
    mock_update.callback_query.message = MagicMock()
    mock_update.callback_query.message.text = processed_text

    await button_callback(mock_update, mock_context)

    expired_calls = [
        call for call in mock_update.callback_query.edit_message_text.call_args_list
        if "expired" in call[0][0]
    ]
    assert not expired_calls

@pytest.mark.asyncio
async def test_handle_message_add_expense_button(mock_update, mock_context):
    """Test '💰 Add Expense' button handling."""
    mock_update.message.text = "💰 Add Expense"
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once_with(
        "Please send your expense in the format:\n"
        "amount currency category description\n"
        "Example: 25.50 USD food groceries"
    )

@pytest.mark.asyncio
async def test_handle_message_view_categories_button(mock_update, mock_context):
    """Test '📊 View Categories' button handling."""
    mock_update.message.text = "📊 View Categories"
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once_with(
        "Available categories:\n- Food\n- Transport\n- Utilities\n- Rent\n- Salary"
    )

@pytest.mark.asyncio
@patch('util.telegram.get_recent_expenses', new_callable=AsyncMock)
async def test_handle_message_recent_expenses_button(mock_get_recent_expenses, mock_update, mock_context):
    """Test '📅 Recent Expenses' button handling."""
    mock_update.message.text = "📅 Recent Expenses"
    mock_get_recent_expenses.return_value = "Recent expenses data"
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_any_call("🔄 Fetching recent expenses...")
    mock_get_recent_expenses.assert_called_once()
    mock_update.message.reply_text.assert_any_call("Recent expenses data")

@pytest.mark.asyncio
async def test_handle_message_ping_button(mock_update, mock_context):
    """Test '🏓 Ping' button handling."""
    mock_update.message.text = "🏓 Ping"
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once_with("pong 🏓")

@pytest.mark.asyncio
@patch('util.telegram.get_daily_summary', new_callable=AsyncMock)
async def test_summary_command(mock_get_daily_summary, mock_update, mock_context):
    """Test the /summary command."""
    mock_get_daily_summary.return_value = ("Daily summary data", None)
    await summary_command(mock_update, mock_context)
    mock_update.message.reply_text.assert_any_call("🔄 Calculating today's expenses...")
    mock_get_daily_summary.assert_called_once()
    mock_update.message.reply_text.assert_any_call("Daily summary data")


@pytest.mark.asyncio


@pytest.mark.asyncio
@patch('util.telegram.get_daily_summary', new_callable=AsyncMock)
async def test_handle_message_todays_summary_button(mock_get_daily_summary, mock_update, mock_context):
    """Test '💸 Today's Summary' button handling."""
    mock_update.message.text = "💸 Today's Summary"
    mock_get_daily_summary.return_value = ("Today's summary data", None)
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_any_call("🔄 Calculating today's expenses...")
    mock_get_daily_summary.assert_called_once()
    mock_update.message.reply_text.assert_any_call("Today's summary data")


@pytest.mark.asyncio


@pytest.mark.asyncio
@patch('util.telegram.start_daily_summary_scheduler')
async def test_start_command_scheduler(mock_scheduler, mock_update, mock_context):
    """Test that /start command initializes the daily summary scheduler."""
    await start(mock_update, mock_context)
    mock_scheduler.assert_called_once_with('12345', mock_context, 'UTC')


def test_create_application_registers_handlers():
    """Ensure create_application wires handlers and returns application."""
    mock_builder = MagicMock()
    mock_application = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.build.return_value = mock_application

    with patch('util.telegram.Application.builder', return_value=mock_builder):
        app = create_application()

    mock_builder.token.assert_called_once()
    assert mock_application.add_handler.call_count >= 4
    assert app is mock_application


def test_start_telegram_polling_runs():
    """Ensure polling starts via run_polling."""
    mock_application = MagicMock()
    with patch('util.telegram.create_application', return_value=mock_application):
        app = start_telegram_polling()

    mock_application.run_polling.assert_called_once_with(allowed_updates=Update.ALL_TYPES)
    assert app is mock_application
@pytest.mark.asyncio
@patch('util.telegram._schedule_auto_confirm')
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
async def test_handle_message_fallback_display(mock_process_with_openrouter, mock_schedule, mock_update, mock_context):
    """Test that fallback model information is displayed to the user."""
    mock_update.message.text = "50 USD food lunch"
    fallback_model = "google/gemini-pro-1.5"
    mock_process_with_openrouter.return_value = (((50.0, "USD", "Food", "lunch"), fallback_model), None)

    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    await _drain_chat_queue(mock_update.message.chat_id)

    # Check that the final confirmation message contains fallback info
    edit_calls = mock_status_message.edit_text.call_args_list
    final_call = edit_calls[-1]
    final_text = final_call[0][0]
    assert f"⚠️ *Fallback used:* `{fallback_model}`" in final_text
    assert "parse_mode='Markdown'" in str(final_call)


# ----------  Sequential queue tests  ----------


@pytest.mark.asyncio
@patch('util.telegram._schedule_auto_confirm')
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
async def test_sequential_processing_order(mock_process_with_openrouter, mock_schedule, mock_context):
    """Expenses for the same chat are processed in FIFO order."""
    order = []

    async def fake_openrouter(msg):
        order.append(msg)
        await asyncio.sleep(0.05)  # simulate work
        return (100.0, 'RUB', 'food', msg), 'test-model'

    mock_process_with_openrouter.side_effect = fake_openrouter

    updates = []
    for i in range(3):
        u = MagicMock(spec=Update)
        u.message = MagicMock()
        u.message.text = f"expense_{i}"
        u.message.reply_text = AsyncMock(return_value=AsyncMock())  # status msg
        u.message.chat_id = 99
        u.message.message_id = 1000 + i
        updates.append(u)

    for u in updates:
        await handle_message(u, mock_context)

    await _drain_chat_queue(99)

    assert order == ['expense_0', 'expense_1', 'expense_2']


@pytest.mark.asyncio
@patch('util.telegram._schedule_auto_confirm')
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
async def test_different_chats_process_concurrently(mock_process_with_openrouter, mock_schedule, mock_context):
    """Expenses from different chats are not blocked by each other."""
    started = []

    async def fake_openrouter(msg):
        started.append(msg)
        await asyncio.sleep(0.05)
        return (50.0, 'EUR', 'transport', msg), 'test-model'

    mock_process_with_openrouter.side_effect = fake_openrouter

    def make_update(chat_id, msg_id, text):
        u = MagicMock(spec=Update)
        u.message = MagicMock()
        u.message.text = text
        u.message.reply_text = AsyncMock(return_value=AsyncMock())
        u.message.chat_id = chat_id
        u.message.message_id = msg_id
        return u

    u1 = make_update(100, 1, 'chat_a_expense')
    u2 = make_update(200, 2, 'chat_b_expense')

    await handle_message(u1, mock_context)
    await handle_message(u2, mock_context)

    for q in _chat_queues.values():
        await q.join()

    # Both should have been processed
    assert 'chat_a_expense' in started
    assert 'chat_b_expense' in started


@pytest.mark.asyncio
@patch('util.telegram._schedule_auto_confirm')
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
async def test_queue_error_isolation(mock_process_with_openrouter, mock_schedule, mock_context):
    """An error in one expense does not prevent the next one from processing."""
    call_count = 0

    async def fake_openrouter(msg):
        nonlocal call_count
        call_count += 1
        if msg == 'bad':
            raise RuntimeError('boom')
        return (10.0, 'RUB', 'essentials', msg), 'test-model'

    mock_process_with_openrouter.side_effect = fake_openrouter

    def make_update(msg_id, text):
        u = MagicMock(spec=Update)
        u.message = MagicMock()
        u.message.text = text
        u.message.reply_text = AsyncMock(return_value=AsyncMock())
        u.message.chat_id = 300
        u.message.message_id = msg_id
        return u

    u_bad = make_update(1, 'bad')
    u_good = make_update(2, 'good')

    await handle_message(u_bad, mock_context)
    await handle_message(u_good, mock_context)

    await _drain_chat_queue(300)

    # Both were attempted — the worker didn't crash
    assert call_count == 2
