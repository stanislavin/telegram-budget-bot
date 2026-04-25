from asyncio import CancelledError

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
)
from telegram.ext import ContextTypes
import asyncio

from util.telegram import (
    start,
    help_command,
    handle_message,
    _process_expense,
    _schedule_auto_confirm,
    _cleanup_processed_expense,
    button_callback,
    auto_confirm_expense,
    pending_expenses,
    recently_processed_expenses,
    expense_locks,
    load_categories,
    CATEGORIES,
    summary_command,
    undo_command,
    create_application,
    start_telegram_polling,
    app_command,
    _category_picker_keyboard,
    _send_filtered_expenses,
    PROCESSED_EXPENSE_TTL_SECONDS,
    _get_recent_commits_info,
    _get_bot_info_text,
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
    with patch(
        "util.telegram.CATEGORIES", ["Food", "Transport", "Utilities", "Rent", "Salary"]
    ):
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


@pytest.fixture
def mock_message_to_edit():
    """Fixture to mock a message for editing."""
    msg = MagicMock()
    msg.edit_text = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_start_command(mock_update, mock_context):
    """Test the /start command."""
    await start(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once()
    args, kwargs = mock_update.message.reply_text.call_args
    assert "Hi! I'm your budget tracking bot." in args[0]
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)


@pytest.mark.asyncio
async def test_help_command(mock_update, mock_context):
    """Test the /help command."""
    await help_command(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once()
    args, kwargs = mock_update.message.reply_text.call_args
    assert "Send me messages in the format:" in args[0]
    assert isinstance(kwargs["reply_markup"], ReplyKeyboardMarkup)


@pytest.mark.asyncio
@patch("util.telegram._schedule_auto_confirm")
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
@patch("util.telegram.save_to_postgres", new_callable=AsyncMock)
async def test_handle_message_expense(
    mock_save_to_postgres,
    mock_process_with_openrouter,
    mock_schedule,
    mock_update,
    mock_context,
):
    """Test handling of a regular expense message."""
    mock_update.message.text = "50 USD food lunch"
    mock_process_with_openrouter.return_value = (
        ((50.0, "USD", "Food", None, "lunch"), "anthropic/claude-3-opus-20240229"),
        None,
    )

    # Mock the status message returned by reply_text
    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    await _drain_chat_queue(mock_update.message.chat_id)

    mock_process_with_openrouter.assert_called_once_with("50 USD food lunch")

    expense_id = f"{mock_update.message.chat_id}-{mock_update.message.message_id}"
    assert expense_id in pending_expenses
    assert pending_expenses[expense_id]["amount"] == 50.0

    # Check that edit_text was called with AI analysis message
    edit_calls = mock_status_message.edit_text.call_args_list
    assert any(
        "🤖 Analyzing your expense with AI..." in str(call) for call in edit_calls
    )

    # Check that the final confirmation message was sent
    final_call = edit_calls[-1]
    final_text = final_call[0][0]
    assert "📊 Please confirm the expense (auto-confirms in 10s):" in final_text
    assert "Amount: 50.0 USD" in final_text
    assert "Category: Food" in final_text
    assert "Description: lunch" in final_text
    # Timing footer should be present
    assert "<pre>" in final_text
    assert "s AI" in final_text
    assert "s total</pre>" in final_text
    assert "parse_mode='HTML'" in str(final_call)

    mock_schedule.assert_called_once()


@pytest.mark.asyncio
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
async def test_handle_message_openrouter_error(
    mock_process_with_openrouter, mock_update, mock_context
):
    """Test handling of an OpenRouter error during message processing."""
    mock_update.message.text = "invalid message"
    # The new function returns a more specific error message
    mock_process_with_openrouter.return_value = (
        None,
        "Error processing with OpenRouter after retry: OpenRouter API error",
    )

    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    await _drain_chat_queue(mock_update.message.chat_id)

    # Check that the error message is in the calls
    expected_error = (
        "❌ Error: Error processing with OpenRouter after retry: OpenRouter API error"
    )
    # Extract the actual arguments passed to edit_text
    actual_calls = [
        call_args[0]
        for call_args in mock_status_message.edit_text.call_args_list
        if call_args and len(call_args) > 0
    ]
    # Check if the expected error text is in any of the actual calls
    assert any(expected_error in actual_call for actual_call in actual_calls), (
        f"Expected '{expected_error}' in actual calls: {actual_calls}"
    )
    assert not pending_expenses  # No expense should be pending on error


@pytest.mark.asyncio
@patch("util.telegram.save_to_postgres", new_callable=AsyncMock)
@patch("util.telegram.get_daily_stats_pg", new_callable=AsyncMock)
async def test_button_callback_confirm_success(
    mock_get_daily_stats_pg, mock_save_to_postgres, mock_update, mock_context
):
    """Test confirming an expense via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        "amount": 75.0,
        "currency": "EUR",
        "category": "Transport",
        "description": "Taxi",
        "status_message": AsyncMock(),
    }
    mock_update.callback_query.data = f"action:confirm|id:{expense_id}"
    mock_save_to_postgres.return_value = (True, None)

    mock_get_daily_stats_pg.return_value = ({"EUR": 100.0}, {})

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_save_to_postgres.assert_called_once_with(75.0, "EUR", "Transport", "Taxi", spending_type=None)
    calls = mock_update.callback_query.edit_message_text.call_args_list
    assert calls[0][0][0] == "💾 Saving to database..."
    final_text = calls[1][0][0]
    assert "✅ Saved: 75.0 EUR - Transport - Taxi" in final_text
    assert "💸 Total spent today:" in final_text
    assert expense_id not in pending_expenses


@pytest.mark.asyncio
@patch("util.telegram.save_to_postgres", new_callable=AsyncMock)
async def test_button_callback_confirm_failure(
    mock_save_to_postgres, mock_update, mock_context
):
    """Test confirming an expense with save failure via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        "amount": 75.0,
        "currency": "EUR",
        "category": "Transport",
        "description": "Taxi",
        "status_message": AsyncMock(),
    }
    mock_update.callback_query.data = f"action:confirm|id:{expense_id}"
    mock_save_to_postgres.return_value = (False, "Database error")

    # Mock the edit_message_reply_markup method as well since it's called when there's an error
    mock_update.callback_query.edit_message_reply_markup = AsyncMock()

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_save_to_postgres.assert_called_once_with(75.0, "EUR", "Transport", "Taxi", spending_type=None)
    calls = mock_update.callback_query.edit_message_text.call_args_list
    assert calls[0][0][0] == "💾 Saving to database..."
    assert calls[1][0][0] == "❌ Error saving to database: Database error"
    assert expense_id not in pending_expenses


@pytest.mark.asyncio
async def test_button_callback_cancel(mock_update, mock_context):
    """Test cancelling an expense via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        "amount": 10.0,
        "currency": "USD",
        "category": "Food",
        "description": "Coffee",
        "status_message": AsyncMock(),
    }
    mock_update.callback_query.data = f"action:cancel|id:{expense_id}"

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once_with(
        "Expense cancelled."
    )
    assert expense_id not in pending_expenses


@pytest.mark.asyncio
async def test_button_callback_change_category(mock_update, mock_context):
    """Test changing category via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        "amount": 20.0,
        "currency": "USD",
        "category": "Food",
        "description": "Dinner",
        "status_message": AsyncMock(),
    }
    mock_update.callback_query.data = f"action:change_category|id:{expense_id}"

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    args, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert "Select a new category for:" in args[0]
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)


@pytest.mark.asyncio
async def test_button_callback_select_category(mock_update, mock_context):
    """Test selecting a new category via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        "amount": 20.0,
        "currency": "USD",
        "category": "Food",
        "description": "Dinner",
        "status_message": AsyncMock(),
    }
    mock_update.callback_query.data = (
        f"action:select_category|id:{expense_id}|category:Transport"
    )

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    args, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert "Category: Transport" in args[0]
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)
    assert pending_expenses[expense_id]["category"] == "Transport"


@pytest.mark.asyncio
async def test_button_callback_back(mock_update, mock_context):
    """Test back button via button callback."""
    expense_id = "12345-67890"
    pending_expenses[expense_id] = {
        "amount": 20.0,
        "currency": "USD",
        "category": "Food",
        "description": "Dinner",
        "status_message": AsyncMock(),
    }
    mock_update.callback_query.data = f"action:back|id:{expense_id}"

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    args, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert "Please confirm the expense:" in args[0]
    assert isinstance(kwargs["reply_markup"], InlineKeyboardMarkup)


@pytest.mark.asyncio
@patch("util.telegram.save_to_postgres", new_callable=AsyncMock)
@patch("util.telegram.get_daily_stats_pg", new_callable=AsyncMock)
async def test_auto_confirm_expense_success(
    mock_get_daily_stats_pg, mock_save_to_postgres, mock_update, mock_context
):
    """Test auto-confirmation of an expense."""
    expense_id = "auto-123"
    mock_status_message = AsyncMock()
    pending_expenses[expense_id] = {
        "amount": 30.0,
        "currency": "GBP",
        "category": "Utilities",
        "description": "Electricity bill",
        "status_message": mock_status_message,
    }
    mock_save_to_postgres.return_value = (True, None)

    mock_get_daily_stats_pg.return_value = ({"GBP": 50.0}, {})

    # We need to run this in a separate task as auto_confirm_expense has a sleep
    task = asyncio.create_task(auto_confirm_expense(expense_id, mock_context))
    await asyncio.sleep(10.1)  # Wait for the sleep to finish
    await task  # Ensure the task completes

    mock_save_to_postgres.assert_called_once_with(
        30.0, "GBP", "Utilities", "Electricity bill", spending_type=None
    )
    mock_status_message.edit_text.assert_called_once()
    call_args = mock_status_message.edit_text.call_args
    assert (
        "⏱️ Auto-confirmed: 30.0 GBP - Utilities - Electricity bill" in call_args[0][0]
    )
    assert "💸 Total spent today:" in call_args[0][0]
    assert expense_id not in pending_expenses


@pytest.mark.asyncio
@patch("util.telegram.save_to_postgres", new_callable=AsyncMock)
async def test_auto_confirm_expense_failure(
    mock_save_to_postgres, mock_update, mock_context
):
    """Test auto-confirmation failure."""
    expense_id = "auto-456"
    mock_status_message = AsyncMock()
    pending_expenses[expense_id] = {
        "amount": 40.0,
        "currency": "JPY",
        "category": "Rent",
        "description": "Monthly rent",
        "status_message": mock_status_message,
    }
    mock_save_to_postgres.return_value = (False, "Auto-save error")

    task = asyncio.create_task(auto_confirm_expense(expense_id, mock_context))
    await asyncio.sleep(10.1)
    await task

    mock_save_to_postgres.assert_called_once_with(40.0, "JPY", "Rent", "Monthly rent", spending_type=None)
    mock_status_message.edit_text.assert_called_once_with(
        f"❌ Error auto-saving to database: Auto-save error"
    )
    assert expense_id not in pending_expenses


@pytest.mark.asyncio
@patch("util.telegram.asyncio.sleep", new_callable=AsyncMock)
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
        call
        for call in mock_update.callback_query.edit_message_text.call_args_list
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
@patch("util.telegram.get_recent_expenses_pg", new_callable=AsyncMock)
async def test_handle_message_recent_expenses_button(
    mock_get_recent_expenses_pg, mock_update, mock_context
):
    """Test '📅 Recent Expenses' button handling."""
    mock_update.message.text = "📅 Recent Expenses"
    mock_get_recent_expenses_pg.return_value = "Recent expenses data"
    # reply_text returns a message object that will be edited with results
    status_msg = AsyncMock()
    mock_update.message.reply_text.return_value = status_msg
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_any_call("🔄 Fetching recent expenses...")
    mock_get_recent_expenses_pg.assert_called_once()
    status_msg.edit_text.assert_called_once()


@pytest.mark.asyncio
@patch("util.telegram._get_bot_info_text")
async def test_handle_message_bot_info_button(
    mock_get_bot_info, mock_update, mock_context
):
    """Test 'ℹ️ Bot Info' button handling."""
    mock_update.message.text = "ℹ️ Bot Info"
    mock_get_bot_info.return_value = "<b>Bot Info</b>"

    await handle_message(mock_update, mock_context)

    mock_get_bot_info.assert_called_once()
    mock_update.message.reply_text.assert_called_once_with(
        "<b>Bot Info</b>", parse_mode="HTML"
    )


@pytest.mark.asyncio
async def test_handle_message_dashboard_button(mock_update, mock_context):
    """Test '🖥️ Dashboard' button handling - should not trigger expense analysis."""
    mock_update.message.text = "🖥️ Dashboard"
    mock_update.message.reply_text = AsyncMock()

    await handle_message(mock_update, mock_context)

    # Should call dashboard_command, not process_with_openrouter
    mock_update.message.reply_text.assert_called_once()
    args, kwargs = mock_update.message.reply_text.call_args
    assert "Service URL" in args[0]
    assert isinstance(kwargs.get("parse_mode"), str)


@pytest.mark.asyncio
@patch("util.telegram.get_daily_summary_pg", new_callable=AsyncMock)
async def test_summary_command(mock_get_daily_summary_pg, mock_update, mock_context):
    """Test the /summary command."""
    mock_get_daily_summary_pg.return_value = ("Daily summary data", None)
    await summary_command(mock_update, mock_context)
    mock_update.message.reply_text.assert_any_call("🔄 Calculating today's expenses...")
    mock_get_daily_summary_pg.assert_called_once()
    mock_update.message.reply_text.assert_any_call("Daily summary data")


@pytest.mark.asyncio
@pytest.mark.asyncio
@patch("util.telegram.get_daily_summary_pg", new_callable=AsyncMock)
async def test_handle_message_todays_summary_button(
    mock_get_daily_summary_pg, mock_update, mock_context
):
    """Test '💸 Today's Summary' button handling."""
    mock_update.message.text = "💸 Today's Summary"
    mock_get_daily_summary_pg.return_value = ("Today's summary data", None)
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_any_call("🔄 Calculating today's expenses...")
    mock_get_daily_summary_pg.assert_called_once()
    mock_update.message.reply_text.assert_any_call("Today's summary data")


@pytest.mark.asyncio
def test_create_application_registers_handlers():
    """Ensure create_application wires handlers and returns application."""
    mock_builder = MagicMock()
    mock_application = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.post_init.return_value = mock_builder
    mock_builder.build.return_value = mock_application

    with patch("util.telegram.Application.builder", return_value=mock_builder):
        app = create_application()

    mock_builder.token.assert_called_once()
    assert mock_application.add_handler.call_count >= 4
    assert app is mock_application


def test_start_telegram_polling_runs():
    """Ensure polling starts via run_polling."""
    mock_application = MagicMock()
    with patch("util.telegram.create_application", return_value=mock_application):
        app = start_telegram_polling()

    mock_application.run_polling.assert_called_once_with(
        allowed_updates=Update.ALL_TYPES
    )
    assert app is mock_application


@pytest.mark.asyncio
@patch("util.telegram._schedule_auto_confirm")
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
async def test_handle_message_fallback_display(
    mock_process_with_openrouter, mock_schedule, mock_update, mock_context
):
    """Test that fallback model information is displayed to the user."""
    mock_update.message.text = "50 USD food lunch"
    fallback_model = "google/gemini-pro-1.5"
    mock_process_with_openrouter.return_value = (
        ((50.0, "USD", "Food", "want", "lunch"), fallback_model),
        None,
    )

    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    await _drain_chat_queue(mock_update.message.chat_id)

    # Check that the final confirmation message contains fallback info (HTML)
    edit_calls = mock_status_message.edit_text.call_args_list
    final_call = edit_calls[-1]
    final_text = final_call[0][0]
    assert f"⚠️ <b>Fallback used:</b> <code>{fallback_model}</code>" in final_text
    assert "parse_mode='HTML'" in str(final_call)
    # Timing footer should also be present
    assert "<pre>" in final_text
    assert "s total</pre>" in final_text


# ----------  Sequential queue tests  ----------


@pytest.mark.asyncio
@patch("util.telegram._schedule_auto_confirm")
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
async def test_sequential_processing_order(
    mock_process_with_openrouter, mock_schedule, mock_context
):
    """Expenses for the same chat are processed in FIFO order."""
    order = []

    async def fake_openrouter(msg):
        order.append(msg)
        await asyncio.sleep(0.05)  # simulate work
        return (100.0, "RUB", "food", None, msg), "test-model"

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

    await _drain_chat_queue(str(99))

    assert order == ["expense_0", "expense_1", "expense_2"]


@pytest.mark.asyncio
@patch("util.telegram._schedule_auto_confirm")
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
async def test_different_chats_process_concurrently(
    mock_process_with_openrouter, mock_schedule, mock_context
):
    """Expenses from different chats are not blocked by each other."""
    started = []

    async def fake_openrouter(msg):
        started.append(msg)
        await asyncio.sleep(0.05)
        return (50.0, "EUR", "transport", None, msg), "test-model"

    mock_process_with_openrouter.side_effect = fake_openrouter

    def make_update(chat_id, msg_id, text):
        u = MagicMock(spec=Update)
        u.message = MagicMock()
        u.message.text = text
        u.message.reply_text = AsyncMock(return_value=AsyncMock())
        u.message.chat_id = chat_id
        u.message.message_id = msg_id
        return u

    u1 = make_update(100, 1, "chat_a_expense")
    u2 = make_update(200, 2, "chat_b_expense")

    await handle_message(u1, mock_context)
    await handle_message(u2, mock_context)

    for q in _chat_queues.values():
        await q.join()

    # Both should have been processed
    assert "chat_a_expense" in started
    assert "chat_b_expense" in started


@pytest.mark.asyncio
@patch("util.telegram._schedule_auto_confirm")
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
async def test_queue_error_isolation(
    mock_process_with_openrouter, mock_schedule, mock_context
):
    """An error in one expense does not prevent the next one from processing."""
    call_count = 0

    async def fake_openrouter(msg):
        nonlocal call_count
        call_count += 1
        if msg == "bad":
            raise RuntimeError("boom")
        return (10.0, "RUB", "essentials", None, msg), "test-model"

    mock_process_with_openrouter.side_effect = fake_openrouter

    def make_update(msg_id, text):
        u = MagicMock(spec=Update)
        u.message = MagicMock()
        u.message.text = text
        u.message.reply_text = AsyncMock(return_value=AsyncMock())
        u.message.chat_id = 300
        u.message.message_id = msg_id
        return u

    u_bad = make_update(1, "bad")
    u_good = make_update(2, "good")

    await handle_message(u_bad, mock_context)
    await handle_message(u_good, mock_context)

    await _drain_chat_queue(str(300))

    # Both were attempted — the worker didn't crash
    assert call_count == 2


# ---------- _cleanup_processed_expense ----------


@pytest.mark.asyncio
@patch("util.telegram.asyncio.sleep", new_callable=AsyncMock)
async def test_cleanup_processed_expense(mock_sleep):
    """Test that _cleanup_processed_expense removes state after sleeping (lines 37-38)."""
    expense_id = "cleanup-test-123"
    recently_processed_expenses[expense_id] = "some text"
    expense_locks[expense_id] = asyncio.Lock()

    await _cleanup_processed_expense(expense_id)

    mock_sleep.assert_awaited_once_with(PROCESSED_EXPENSE_TTL_SECONDS)
    assert expense_id not in recently_processed_expenses
    assert expense_id not in expense_locks


# ---------- undo_command ----------


@pytest.mark.asyncio
@patch("util.telegram.delete_last_expense_pg", new_callable=AsyncMock)
async def test_undo_command_success(mock_delete, mock_update, mock_context):
    """Test undo_command when deletion succeeds (lines 97-110)."""
    mock_delete.return_value = (
        {
            "amount": "25.50",
            "currency": "USD",
            "category": "Food",
            "description": "lunch",
        },
        None,
    )

    await undo_command(mock_update, mock_context)

    reply_calls = [str(c) for c in mock_update.message.reply_text.call_args_list]
    assert any("Deleting last expense" in c for c in reply_calls)
    assert any("Deleted last expense" in c for c in reply_calls)
    assert any("25.50" in c for c in reply_calls)


@pytest.mark.asyncio
@patch("util.telegram.delete_last_expense_pg", new_callable=AsyncMock)
async def test_undo_command_error(mock_delete, mock_update, mock_context):
    """Test undo_command when deletion fails (lines 97-103)."""
    mock_delete.return_value = (None, "No expenses to delete.")

    await undo_command(mock_update, mock_context)

    reply_calls = [str(c) for c in mock_update.message.reply_text.call_args_list]
    assert any("No expenses to delete." in c for c in reply_calls)


# ---------- handle_message keyboard buttons ----------


@pytest.mark.asyncio
async def test_handle_message_help_button(mock_update, mock_context):
    """Test '❓ Help' button routes to help_command (lines 443-444)."""
    mock_update.message.text = "❓ Help"
    await handle_message(mock_update, mock_context)
    mock_update.message.reply_text.assert_called_once()
    args, _ = mock_update.message.reply_text.call_args
    assert "Send me messages in the format:" in args[0]


@pytest.mark.asyncio
@patch("util.telegram.delete_last_expense_pg", new_callable=AsyncMock)
async def test_handle_message_undo_button(mock_delete, mock_update, mock_context):
    """Test '↩️ Undo last' button routes to undo_command (lines 456-457)."""
    mock_delete.return_value = (
        {
            "amount": "10.00",
            "currency": "USD",
            "category": "Food",
            "description": "coffee",
        },
        None,
    )
    mock_update.message.text = "↩️ Undo last"
    await handle_message(mock_update, mock_context)
    mock_delete.assert_called_once()


# ---------- _handle_openrouter_retry error paths ----------


@pytest.mark.asyncio
async def test_button_callback_openrouter_retry_no_reply_to_message(
    mock_update, mock_context
):
    """Test manual_openrouter_retry when reply_to_message is absent (lines 209-210)."""
    mock_update.callback_query.data = "action:manual_openrouter_retry|id:test-123"
    mock_update.callback_query.message = MagicMock()
    mock_update.callback_query.message.reply_to_message = None

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_with(
        "❌ Unable to retry: original message not found."
    )


@pytest.mark.asyncio
async def test_button_callback_openrouter_retry_empty_text(mock_update, mock_context):
    """Test manual_openrouter_retry when reply_to_message.text is empty (lines 214-215)."""
    mock_update.callback_query.data = "action:manual_openrouter_retry|id:test-123"
    mock_update.callback_query.message = MagicMock()
    mock_update.callback_query.message.reply_to_message = MagicMock()
    mock_update.callback_query.message.reply_to_message.text = None

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_with(
        "❌ Unable to retry: original message text is empty."
    )


@pytest.mark.asyncio
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
async def test_button_callback_openrouter_retry_api_error(
    mock_process, mock_update, mock_context
):
    """Test manual_openrouter_retry when OpenRouter returns an error (lines 222-226)."""
    mock_update.callback_query.data = "action:manual_openrouter_retry|id:test-123"
    mock_update.callback_query.message = MagicMock()
    mock_update.callback_query.message.reply_to_message = MagicMock()
    mock_update.callback_query.message.reply_to_message.text = "50 USD food lunch"
    mock_update.callback_query.edit_message_reply_markup = AsyncMock()
    mock_process.return_value = (None, "OpenRouter API Error")

    await button_callback(mock_update, mock_context)

    edit_calls = [
        str(c) for c in mock_update.callback_query.edit_message_text.call_args_list
    ]
    assert any("OpenRouter API Error" in c for c in edit_calls)
    mock_update.callback_query.edit_message_reply_markup.assert_called_once()


@pytest.mark.asyncio
@patch("util.telegram._schedule_auto_confirm")
@patch("util.telegram.process_with_openrouter", new_callable=AsyncMock)
async def test_button_callback_openrouter_retry_no_expense_id(
    mock_process, mock_schedule, mock_update, mock_context
):
    """Test manual_openrouter_retry generates expense_id when none provided (line 232)."""
    mock_update.callback_query.data = "action:manual_openrouter_retry"  # no id field
    mock_update.callback_query.message = MagicMock()
    mock_update.callback_query.message.reply_to_message = MagicMock()
    mock_update.callback_query.message.reply_to_message.text = "50 USD food lunch"
    mock_update.effective_message = MagicMock()
    mock_update.effective_message.chat_id = 99999
    mock_update.effective_message.message_id = 11111
    mock_process.return_value = (((50.0, "USD", "Food", None, "lunch"), "test-model"), None)

    await button_callback(mock_update, mock_context)

    generated_id = "99999-11111"
    assert generated_id in pending_expenses


# ---------- button_callback expired expense handling ----------


@pytest.mark.asyncio
async def test_button_callback_expired_processed_different_text(
    mock_update, mock_context
):
    """Late press where current text differs from processed text triggers edit (line 406)."""
    expense_id = "expired-diff-123"
    processed_text = "⏱️ Auto-confirmed: 10 USD - Food - Snack"
    recently_processed_expenses[expense_id] = processed_text
    mock_update.callback_query.data = f"action:confirm|id:{expense_id}"
    mock_update.callback_query.message = MagicMock()
    mock_update.callback_query.message.text = "old different text"

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_with(processed_text)


@pytest.mark.asyncio
async def test_button_callback_expired_no_processed_text(mock_update, mock_context):
    """Expired expense with no processed record shows expiry message (line 410)."""
    expense_id = "truly-expired-999"
    mock_update.callback_query.data = f"action:confirm|id:{expense_id}"

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_with(
        "❌ This expense has expired or was already processed."
    )


# ---------- _get_recent_commits_info ----------


def test_get_recent_commits_info_success():
    """Returns 3 commits ending at the deployed SHA via GitHub API."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = [
        {"sha": "abc1234aaaa", "commit": {"message": "Fix bug"}},
        {"sha": "def5678bbbb", "commit": {"message": "Add feature\nbody"}},
        {"sha": "789abcdcccc", "commit": {"message": "Refactor"}},
    ]
    fake_resp.raise_for_status = MagicMock()
    with patch("util.telegram._recent_commits_cache", {}), patch(
        "util.telegram.GIT_COMMIT_SHORT", "abc1234"
    ), patch("util.telegram.requests.get", return_value=fake_resp):
        result = _get_recent_commits_info()

    assert "abc1234 Fix bug" in result
    assert "def5678 Add feature" in result
    assert "789abcd Refactor" in result


def test_get_recent_commits_info_error():
    """Falls back to bare SHA when GitHub API fails."""
    with patch("util.telegram._recent_commits_cache", {}), patch(
        "util.telegram.GIT_COMMIT_SHORT", "abc1234"
    ), patch("util.telegram.requests.get", side_effect=Exception("network down")):
        result = _get_recent_commits_info()

    assert result == "abc1234"


def test_get_recent_commits_info_error_unknown():
    """No SHA available and no local git → unavailable message."""
    with patch("util.telegram._recent_commits_cache", {}), patch(
        "util.telegram.GIT_COMMIT_SHORT", "unknown"
    ), patch(
        "util.telegram.subprocess.check_output", side_effect=Exception("git not found")
    ):
        result = _get_recent_commits_info()

    assert result == "(git info unavailable)"


# ---------- _get_bot_info_text ----------


def test_get_bot_info_text():
    """Test _get_bot_info_text builds the info string (lines 549-557)."""
    with patch(
        "util.telegram._get_recent_commits_info",
        return_value="abc1234 Add feature\ndef5678 Fix bug\n789abcd Update docs",
    ):
        result = _get_bot_info_text()

    assert "Bot Information" in result
    assert "SERVICE_URL" in result
    assert "Local LLM" in result
    assert "Cloud fallbacks" in result
    assert "Recent commits" in result


# ---------- app_command APK not found ----------


async def test_app_command_apk_not_found(mock_update, mock_context):
    """Falls back to error message when no local APK and no release asset."""
    with patch("util.telegram.os.path.isfile", return_value=False), patch(
        "util.telegram._resolve_apk_release_url", return_value=None
    ):
        await app_command(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once_with(
        "APK not available yet. The latest build hasn't published a release."
    )


async def test_app_command_uses_release_url(mock_update, mock_context):
    """Downloads the APK server-side and sends the bytes when no local APK exists."""
    url = "https://github.com/owner/repo/releases/download/android-latest/expense-tracker.apk"
    fake_bytes = b"PK\x03\x04fake-apk-bytes"
    mock_update.message.reply_document = AsyncMock()
    with patch("util.telegram.os.path.isfile", return_value=False), patch(
        "util.telegram._resolve_apk_release_url", return_value=url
    ), patch(
        "util.telegram._download_apk_bytes", return_value=fake_bytes
    ):
        await app_command(mock_update, mock_context)

    mock_update.message.reply_document.assert_called_once_with(
        document=fake_bytes, filename="expense-tracker.apk"
    )


# ---------- _category_picker_keyboard ----------


def test_category_picker_keyboard():
    """Test _category_picker_keyboard builds category filter keyboard."""
    with patch("util.telegram.CATEGORIES", ["food", "transport", "housing"]):
        markup = _category_picker_keyboard()

    assert isinstance(markup, InlineKeyboardMarkup)
    assert len(markup.inline_keyboard) == 2  # 3 cats in first row + Show All


def test_category_picker_keyboard_single_category():
    """Test _category_picker_keyboard with single category."""
    with patch("util.telegram.CATEGORIES", ["food"]):
        markup = _category_picker_keyboard()

    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].text == "food"


# ---------- _send_filtered_expenses with filters ----------


async def test_send_filtered_expenses_by_category(mock_message_to_edit):
    """Test _send_filtered_expenses with category filter."""
    with patch("util.telegram.get_recent_expenses_pg", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = "Expense list for food"
        await _send_filtered_expenses(mock_message_to_edit, category="food")

    mock_get.assert_called_once_with(category="food")
    mock_message_to_edit.edit_text.assert_called_once()


async def test_send_filtered_expenses_by_spending_type(mock_message_to_edit):
    """Test _send_filtered_expenses with spending_type filter."""
    with patch("util.telegram.get_recent_expenses_pg", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = "Expense list for need"
        await _send_filtered_expenses(mock_message_to_edit, spending_type="need")

    mock_get.assert_called_once_with(spending_type="need")
    mock_message_to_edit.edit_text.assert_called_once()


async def test_send_filtered_expenses_both_filters(mock_message_to_edit):
    """Test _send_filtered_expenses with both category and spending_type."""
    with patch("util.telegram.get_recent_expenses_pg", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = "Expense list"
        await _send_filtered_expenses(
            mock_message_to_edit, category="food", spending_type="need"
        )

    mock_get.assert_called_once_with(category="food", spending_type="need")


async def test_send_filtered_expenses_truncation(mock_message_to_edit):
    """Test _send_filtered_expenses truncates long messages."""
    long_text = "x" * 5000

    with patch("util.telegram.get_recent_expenses_pg", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = long_text
        await _send_filtered_expenses(mock_message_to_edit, category="food")

    call_args = mock_message_to_edit.edit_text.call_args
    assert len(call_args[0][0]) < 4096
    call_args[0][0].endswith("\n... (truncated)")


# ---------- _schedule_auto_confirm RuntimeError handling ----------


def test_schedule_auto_confirm_runtime_error_on_cancel():
    """Test _schedule_auto_confirm handles RuntimeError on cancel."""
    mock_context = MagicMock()
    mock_task = MagicMock()
    mock_task.cancel.side_effect = RuntimeError("Task cancelled")

    with patch("util.telegram.auto_confirm_tasks", {"expense1": mock_task}), \
         patch("util.telegram.asyncio.create_task"):
        _schedule_auto_confirm("expense1", mock_context)


def test_schedule_auto_confirm_runtime_error_on_create():
    """Test _schedule_auto_confirm handles RuntimeError on create_task."""
    mock_context = MagicMock()

    with patch("util.telegram.asyncio.create_task", side_effect=RuntimeError("No loop")):
        _schedule_auto_confirm("expense1", mock_context)


# ---------- _cleanup_processed_expense CancelledError ----------


async def test_cleanup_processed_expense_cancelled():
    """Test _cleanup_processed_expense handles CancelledError."""
    with patch("util.telegram.asyncio.sleep", side_effect=CancelledError()):
        await _cleanup_processed_expense("expense1")

    assert "expense1" not in recently_processed_expenses


# ---------- callback_query_handler action handlers ----------


async def test_button_callback_expenses_filter_action(mock_update, mock_context):
    """Test button_callback handles expenses_filter action."""
    mock_msg = MagicMock()

    update = Update(
        1,
        callback_query=CallbackQuery(
            "test",
            from_user=mock_update.effective_user,
            message=mock_msg,
            data="action:expenses_filter|type:all",
            chat_instance="test",
        ),
    )

    with patch("util.telegram._send_filtered_expenses") as mock_send, \
         patch("telegram.CallbackQuery.answer", new_callable=AsyncMock):
        await button_callback(update, mock_context)

    mock_send.assert_called_once()


async def test_button_callback_expenses_pick_category_action(mock_update, mock_context):
    """Test button_callback handles expenses_pick_category action."""
    mock_msg = MagicMock()
    mock_msg.edit_text = AsyncMock()

    update = Update(
        1,
        callback_query=CallbackQuery(
            "test",
            from_user=mock_update.effective_user,
            message=mock_msg,
            data="action:expenses_pick_category",
            chat_instance="test",
        ),
    )

    with patch("util.telegram.CATEGORIES", ["food", "transport"]), \
         patch("telegram.CallbackQuery.answer", new_callable=AsyncMock):
        await button_callback(update, mock_context)

    assert mock_msg.edit_text.called
