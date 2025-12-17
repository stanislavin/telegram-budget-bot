import asyncio
import logging
from functools import wraps

logger = logging.getLogger(__name__)

def retry_api_call(retry_delay=10):
    """
    Decorator to retry API calls when they fail.

    Args:
        retry_delay (int): Delay in seconds before retrying the API call
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Try the function once
            result = await func(*args, **kwargs)

            # If the function succeeded (result indicates success), return it
            # Different functions might have different success indicators
            # For functions returning (success, error), check if success is True
            # For functions returning objects, check if result is not None/error-indicating
            if (isinstance(result, tuple) and len(result) == 2):
                success, error = result
                if success is True or error is None:
                    return result
            elif result is not None and not (isinstance(result, bool) and not result):
                return result

            # If we reach here, the first attempt failed
            logger.warning(f"API request failed for {func.__name__}, retrying in {retry_delay}s")

            # Wait before retry
            await asyncio.sleep(retry_delay)

            # Retry once more
            retry_result = await func(*args, **kwargs)

            return retry_result

        return wrapper
    return decorator


async def retry_with_feedback(func, update, context, success_msg="Operation successful",
                             retry_msg="⚠️ Operation failed, retrying in 10 seconds...",
                             final_fail_msg="❌ Operation failed"):
    """
    Helper function to execute an API call with retry and user feedback.

    Args:
        func: The async function to call
        update: Telegram update object
        context: Telegram context object
        success_msg: Message to show on success
        retry_msg: Message to show when retrying
        final_fail_msg: Message to show if both attempts fail

    Returns:
        The result of the function call
    """
    # Try the function once
    result = await func()

    # Check if the function succeeded
    success = True
    if isinstance(result, tuple) and len(result) == 2:
        success, error = result
        if success is False and error is not None:
            success = False

    if success:
        if update and update.effective_message:
            await update.effective_message.edit_text(success_msg)
        return result

    # First attempt failed
    if update and update.effective_message:
        await update.effective_message.edit_text(retry_msg)

    # Wait before retrying
    await asyncio.sleep(10)

    if update and update.effective_message:
        await update.effective_message.edit_text("🔄 Retrying API request...")

    # Retry the function
    retry_result = await func()

    if update and update.effective_message:
        # Check if the retry succeeded
        retry_success = True
        if isinstance(retry_result, tuple) and len(retry_result) == 2:
            retry_success, error = retry_result
            if retry_success is False and error is not None:
                retry_success = False

        if not retry_success:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            # Both attempts failed, show error with retry button
            error, error_msg = retry_result if isinstance(retry_result, tuple) else (None, str(retry_result))
            await update.effective_message.edit_text(f"{final_fail_msg}: {error_msg if error_msg else 'Unknown error'}")

            # Add manual retry button
            keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data="manual_retry")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.effective_message.edit_reply_markup(reply_markup=reply_markup)
        else:
            await update.effective_message.edit_text(success_msg)

    return retry_result