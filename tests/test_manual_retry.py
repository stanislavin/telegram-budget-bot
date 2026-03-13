import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from util.telegram import button_callback, pending_expenses, recently_processed_expenses

@pytest.mark.asyncio
async def test_manual_openrouter_retry_bug_reproduction():
    """
    Reproduce the bug where manual_openrouter_retry fails because:
    1. callback_data doesn't have 'id' or 'action' in expected format.
    2. It falls into the 'expense has expired' branch.
    """
    # Mock update and context
    update = MagicMock(spec=Update)
    update.callback_query = MagicMock()
    update.callback_query.data = "action:manual_openrouter_retry|id:123-456"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    
    # Mock the message the button is on
    status_message = MagicMock()
    status_message.text = "❌ Error: API Error"
    status_message.chat_id = 123
    status_message.message_id = 456
    
    # Mock the original user message (the one being replied to)
    user_message = MagicMock()
    user_message.text = "100 rsd food"
    status_message.reply_to_message = user_message
    
    update.callback_query.message = status_message
    update.effective_message = status_message
    
    context = MagicMock()

    # Clear state
    pending_expenses.clear()
    recently_processed_expenses.clear()

    # Mock process_with_openrouter to avoid actual API call
    with patch('util.telegram.process_with_openrouter', new_callable=AsyncMock) as mock_process:
        mock_process.return_value = (((100.0, "RSD", "Food", None, "food"), "anthropic/claude-3-opus-20240229"), None)
        
        # Call the callback
        await button_callback(update, context)

    # Verify that it started retrying
    update.callback_query.edit_message_text.assert_any_call("🔄 Retrying OpenRouter API call...")

if __name__ == "__main__":
    pytest.main([__file__])
