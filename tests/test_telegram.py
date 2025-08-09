import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
import asyncio

from util.telegram import start, help_command, handle_message, button_callback, auto_confirm_expense, pending_expenses, load_categories, CATEGORIES, summary_command

# Mock the CATEGORIES to ensure consistent test results
@pytest.fixture(autouse=True)
def mock_categories():
    with patch('util.telegram.CATEGORIES', ['Food', 'Transport', 'Utilities', 'Rent', 'Salary']):
        yield

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
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
@patch('asyncio.create_task')
async def test_handle_message_expense(mock_create_task, mock_save_to_sheets, mock_process_with_openrouter, mock_update, mock_context):
    """Test handling of a regular expense message."""
    mock_update.message.text = "50 USD food lunch"
    mock_process_with_openrouter.return_value = ((50.0, "USD", "Food", "lunch"), None)
    
    # Mock the status message returned by reply_text
    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    
    mock_update.message.reply_text.assert_called_once_with("🔄 Processing your expense...")
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
    
    mock_create_task.assert_called_once()

@pytest.mark.asyncio
@patch('util.telegram.process_with_openrouter', new_callable=AsyncMock)
async def test_handle_message_openrouter_error(mock_process_with_openrouter, mock_update, mock_context):
    """Test handling of an OpenRouter error during message processing."""
    mock_update.message.text = "invalid message"
    mock_process_with_openrouter.return_value = (None, "OpenRouter API error")
    
    mock_status_message = AsyncMock()
    mock_update.message.reply_text.return_value = mock_status_message

    await handle_message(mock_update, mock_context)
    
    mock_status_message.edit_text.assert_any_call("❌ Error: OpenRouter API error")
    assert not pending_expenses # No expense should be pending on error

@pytest.mark.asyncio
@patch('util.telegram.save_to_sheets', new_callable=AsyncMock)
async def test_button_callback_confirm_success(mock_save_to_sheets, mock_update, mock_context):
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
    
    await button_callback(mock_update, mock_context)
    
    mock_update.callback_query.answer.assert_called_once()
    mock_save_to_sheets.assert_called_once_with(75.0, 'EUR', 'Transport', 'Taxi')
    calls = mock_update.callback_query.edit_message_text.call_args_list
    assert calls[0][0][0] == "💾 Saving to spreadsheet..."
    assert calls[1][0][0] == "✅ Saved: 75.0 EUR - Transport - Taxi"
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
async def test_auto_confirm_expense_success(mock_save_to_sheets, mock_update, mock_context):
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
    
    # We need to run this in a separate task as auto_confirm_expense has a sleep
    task = asyncio.create_task(auto_confirm_expense(expense_id, mock_context))
    await asyncio.sleep(10.1) # Wait for the sleep to finish
    await task # Ensure the task completes
    
    mock_save_to_sheets.assert_called_once_with(30.0, 'GBP', 'Utilities', 'Electricity bill')
    mock_status_message.edit_text.assert_called_once_with(
        f"⏱️ Auto-confirmed: 30.0 GBP - Utilities - Electricity bill"
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
@patch('util.telegram.start_daily_summary_scheduler')
async def test_start_command_scheduler(mock_scheduler, mock_update, mock_context):
    """Test that /start command initializes the daily summary scheduler."""
    await start(mock_update, mock_context)
    mock_scheduler.assert_called_once_with('12345', mock_context, 'UTC')
