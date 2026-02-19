import logging
import re
import time
import asyncio
import os
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

from util.config import (
    TELEGRAM_BOT_TOKEN, get_llm_prompt, OPENROUTER_LLM_VERSION,
    OPENROUTER_FALLBACK_MODELS, SERVICE_URL,
)
from util.sheets import save_to_sheets, get_recent_expenses, get_daily_summary, get_daily_stats, delete_last_expense
from util.openrouter import process_with_openrouter
from util.scheduler import start_daily_summary_scheduler
from util.message_queue import enqueue_expense, queue_size

logger = logging.getLogger(__name__)

# Store pending expenses
pending_expenses = {}
recently_processed_expenses = {}
expense_locks = {}
PROCESSED_EXPENSE_TTL_SECONDS = 30

# Track chats that have already received the startup notification
_startup_notified: set[str] = set()


def _schedule_auto_confirm(expense_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Schedule the auto-confirm timer as a background task."""
    asyncio.create_task(auto_confirm_expense(expense_id, context))


async def _cleanup_processed_expense(expense_id: str):
    """Drop processed state after a short retention window."""
    await asyncio.sleep(PROCESSED_EXPENSE_TTL_SECONDS)
    recently_processed_expenses.pop(expense_id, None)
    expense_locks.pop(expense_id, None)


def _remember_processed_expense(expense_id: str, final_text: str):
    """Remember processed expenses briefly to handle late button presses gracefully."""
    recently_processed_expenses[expense_id] = final_text
    asyncio.create_task(_cleanup_processed_expense(expense_id))

# Load categories from prompt
def load_categories():
    prompt = get_llm_prompt()
    # Extract categories from the prompt using regex
    category_pattern = r'- ([a-zA-Z\s]+) \(.*?\)'
    categories = [cat.strip() for cat in re.findall(category_pattern, prompt)]
    return categories

CATEGORIES = load_categories()

def get_command_keyboard():
    """Create a custom keyboard with command buttons."""
    keyboard = [
        [KeyboardButton("💰 Add Expense")],
        [KeyboardButton("📊 View Categories"), KeyboardButton("❓ Help")],
        [KeyboardButton("📅 Recent Expenses"), KeyboardButton("💸 Today's Summary")],
        [KeyboardButton("↩️ Undo last"), KeyboardButton("🏓 Ping")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    chat_id = str(update.message.chat_id)
    
    # Start daily summary scheduler for this chat
    start_daily_summary_scheduler(chat_id, context, "UTC")
    
    await update.message.reply_text(
        'Hi! I\'m your budget tracking bot. Send me messages in the format:\n'
        'amount currency category description\n'
        'Example: 25.50 USD food groceries\n\n'
        '🕐 I\'ll also send you a daily spending summary every day at 17:00 UTC.',
        reply_markup=get_command_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        'Send me messages in the format:\n'
        'amount currency category description\n'
        'Example: 25.50 USD food groceries\n\n'
        'Commands:\n'
        '/summary - Get today\'s spending summary\n'
        '/undo - Delete the last expense\n\n'
        'Or use the buttons below to interact with me!',
        reply_markup=get_command_keyboard()
    )

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send today's spending summary."""
    await update.message.reply_text("🔄 Calculating today's expenses...")
    summary_text, _ = await get_daily_summary()
    await update.message.reply_text(summary_text)


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete the last expense from the sheet."""
    await update.message.reply_text("🔄 Deleting last expense...")

    expense_info, error = await delete_last_expense()

    if error:
        await update.message.reply_text(f"❌ {error}")
        return

    await update.message.reply_text(
        f"🗑️ Deleted last expense:\n"
        f"Amount: {expense_info['amount']} {expense_info['currency']}\n"
        f"Category: {expense_info['category']}\n"
        f"Description: {expense_info['description']}"
    )

async def auto_confirm_expense(expense_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Automatically confirm an expense after 10 seconds if no user action."""
    await asyncio.sleep(10)  # Wait for 10 seconds

    lock = expense_locks.setdefault(expense_id, asyncio.Lock())
    async with lock:
        expense_data = pending_expenses.pop(expense_id, None)
        if not expense_data:
            return

        # Get the status message
        status_message = expense_data['status_message']

        # Save to sheets with retry
        success, error = await save_to_sheets(
            expense_data['amount'],
            expense_data['currency'],
            expense_data['category'],
            expense_data['description']
        )

        if success:
            # Get daily stats
            currency_totals, _ = await get_daily_stats()

            # Format totals
            totals_str = format_daily_totals(currency_totals)

            final_text = (
                f"⏱️ Auto-confirmed: {expense_data['amount']} {expense_data['currency']} - "
                f"{expense_data['category']} - {expense_data['description']}\n\n"
                f"💸 Total spent today: {totals_str}"
            )
            await status_message.edit_text(final_text)
        else:
            final_text = f"❌ Error auto-saving to spreadsheet: {error}"
            await status_message.edit_text(final_text)

            # Add manual retry button
            keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_sheet_retry|id:{expense_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await status_message.edit_reply_markup(reply_markup=reply_markup)

        _remember_processed_expense(expense_id, final_text)

async def show_category_buttons(expense_id: str, current_category: str):
    """Show category selection buttons."""
    # Create a row of buttons for each category
    keyboard = []
    row = []
    for i, category in enumerate(CATEGORIES):
        if category == current_category:
            row.append(InlineKeyboardButton(f"✅ {category}", callback_data=f"action:select_category|id:{expense_id}|category:{category}"))
        else:
            row.append(InlineKeyboardButton(category, callback_data=f"action:select_category|id:{expense_id}|category:{category}"))
        
        # Add 3 categories per row
        if len(row) == 3 or i == len(CATEGORIES) - 1:
            keyboard.append(row)
            row = []
    
    # Add back button
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data=f"action:back|id:{expense_id}")])
    
    return InlineKeyboardMarkup(keyboard)

def parse_callback_data(data: str) -> dict:
    """Parse callback data into a dictionary."""
    result = {}
    for part in data.split('|'):
        if ':' in part:
            key, value = part.split(':', 1)
            result[key] = value
    return result


def _confirmation_keyboard(expense_id: str) -> InlineKeyboardMarkup:
    """Build the standard Confirm / Cancel / Change Category keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"action:confirm|id:{expense_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"action:cancel|id:{expense_id}")
        ],
        [
            InlineKeyboardButton("🔄 Change Category", callback_data=f"action:change_category|id:{expense_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def format_daily_totals(currency_totals: dict) -> str:
    """Format currency_totals dict into a human-readable string."""
    return ", ".join(f"{amount:.2f} {cur}" for cur, amount in currency_totals.items())

async def _handle_openrouter_retry(query, expense_id, update, context):
    """Handle manual retry for OpenRouter API call."""
    if not query.message or not query.message.reply_to_message:
        await query.edit_message_text("❌ Unable to retry: original message not found.")
        return

    original_message = query.message.reply_to_message.text
    if not original_message:
        await query.edit_message_text("❌ Unable to retry: original message text is empty.")
        return

    await query.edit_message_text("🔄 Retrying OpenRouter API call...")

    result, error = await process_with_openrouter(original_message)

    if error:
        await query.edit_message_text(f"❌ Error: {error}")
        keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_openrouter_retry|id:{expense_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_reply_markup(reply_markup=reply_markup)
        return

    processed_data, model_used = result
    amount, currency, category, description = processed_data

    if not expense_id:
        expense_id = f"{update.effective_message.chat_id}-{update.effective_message.message_id}"

    pending_expenses[expense_id] = {
        'amount': amount,
        'currency': currency,
        'category': category,
        'description': description,
        'status_message': query.message
    }

    reply_markup = _confirmation_keyboard(expense_id)

    fallback_msg = ""
    if model_used != OPENROUTER_LLM_VERSION:
        fallback_msg = f"\n\n⚠️ *Fallback used:* `{model_used}`"

    await query.edit_message_text(
        f"📊 Please confirm the expense (auto-confirms in 10s):\n"
        f"Amount: {amount} {currency}\n"
        f"Category: {category}\n"
        f"Description: {description}{fallback_msg}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

    _schedule_auto_confirm(expense_id, context)


async def _handle_sheet_retry(query, expense_id):
    """Handle manual retry for saving to Google Sheets."""
    await query.edit_message_text("🔄 Retrying to save to spreadsheet...")

    expense_data = pending_expenses.get(expense_id)
    if not expense_data:
        await query.edit_message_text("❌ Unable to retry: expense data no longer available.")
        return

    success, error = await save_to_sheets(
        expense_data['amount'],
        expense_data['currency'],
        expense_data['category'],
        expense_data['description']
    )

    if success:
        currency_totals, _ = await get_daily_stats()
        totals_str = format_daily_totals(currency_totals)

        final_text = (
            f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
            f"{expense_data['category']} - {expense_data['description']}\n\n"
            f"💸 Total spent today: {totals_str}"
        )
    else:
        final_text = f"❌ Error saving to spreadsheet: {error}"

        keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_sheet_retry|id:{expense_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_reply_markup(reply_markup=reply_markup)

    await query.edit_message_text(final_text)

    pending_expenses.pop(expense_id, None)
    _remember_processed_expense(expense_id, final_text)


async def _handle_confirm(query, expense_id, expense_data):
    """Handle expense confirmation."""
    await query.edit_message_text("💾 Saving to spreadsheet...")
    success, error = await save_to_sheets(
        expense_data['amount'],
        expense_data['currency'],
        expense_data['category'],
        expense_data['description']
    )

    if success:
        currency_totals, _ = await get_daily_stats()
        totals_str = format_daily_totals(currency_totals)

        final_text = (
            f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
            f"{expense_data['category']} - {expense_data['description']}\n\n"
            f"💸 Total spent today: {totals_str}"
        )
        await query.edit_message_text(final_text)
    else:
        final_text = f"❌ Error saving to spreadsheet: {error}"

        keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_sheet_retry|id:{expense_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(final_text, reply_markup=reply_markup)

    pending_expenses.pop(expense_id, None)
    _remember_processed_expense(expense_id, final_text)


async def _handle_cancel(query, expense_id):
    """Handle expense cancellation."""
    final_text = "Expense cancelled."
    await query.edit_message_text(final_text)
    pending_expenses.pop(expense_id, None)
    _remember_processed_expense(expense_id, final_text)


async def _handle_change_category(query, expense_id, expense_data):
    """Show category selection buttons."""
    reply_markup = await show_category_buttons(expense_id, expense_data['category'])
    await query.edit_message_text(
        f"📊 Select a new category for:\n"
        f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
        f"Current category: {expense_data['category']}\n"
        f"Description: {expense_data['description']}",
        reply_markup=reply_markup
    )


async def _handle_select_category(query, expense_id, expense_data, data):
    """Handle category selection."""
    new_category = data.get('category')
    expense_data['category'] = new_category

    reply_markup = _confirmation_keyboard(expense_id)

    await query.edit_message_text(
        f"📊 Please confirm the expense:\n"
        f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
        f"Category: {expense_data['category']}\n"
        f"Description: {expense_data['description']}",
        reply_markup=reply_markup
    )


async def _handle_back(query, expense_id, expense_data):
    """Handle back button — show the confirmation screen again."""
    reply_markup = _confirmation_keyboard(expense_id)

    await query.edit_message_text(
        f"📊 Please confirm the expense:\n"
        f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
        f"Category: {expense_data['category']}\n"
        f"Description: {expense_data['description']}",
        reply_markup=reply_markup
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks — dispatches to per-action handlers."""
    query = update.callback_query
    await query.answer()

    data = parse_callback_data(query.data)
    action = data.get('action')
    expense_id = data.get('id')

    # Actions that don't require a pending expense
    if action == 'manual_openrouter_retry':
        await _handle_openrouter_retry(query, expense_id, update, context)
        return

    if action == 'manual_sheet_retry':
        await _handle_sheet_retry(query, expense_id)
        return

    # Actions that require a pending expense and lock
    lock = expense_locks.setdefault(expense_id, asyncio.Lock())
    async with lock:
        expense_data = pending_expenses.get(expense_id)
        processed_text = recently_processed_expenses.get(expense_id)

        if not expense_data:
            if processed_text:
                current_text = getattr(getattr(query, "message", None), "text", "")
                if current_text != processed_text:
                    await query.edit_message_text(processed_text)
                else:
                    await query.answer("This expense was already processed.")
            else:
                await query.edit_message_text("❌ This expense has expired or was already processed.")
            return

        if action == 'confirm':
            await _handle_confirm(query, expense_id, expense_data)
        elif action == 'cancel':
            await _handle_cancel(query, expense_id)
        elif action == 'change_category':
            await _handle_change_category(query, expense_id, expense_data)
        elif action == 'select_category':
            await _handle_select_category(query, expense_id, expense_data, data)
        elif action == 'back':
            await _handle_back(query, expense_id, expense_data)



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
    await _maybe_send_startup_notification(update)
    message = update.message.text

    # Handle command keyboard buttons
    if message == "💰 Add Expense":
        await update.message.reply_text(
            "Please send your expense in the format:\n"
            "amount currency category description\n"
            "Example: 25.50 USD food groceries"
        )
        return
    elif message == "📊 View Categories":
        categories_text = "Available categories:\n" + "\n".join(f"- {cat}" for cat in CATEGORIES)
        await update.message.reply_text(categories_text)
        return
    elif message == "❓ Help":
        await help_command(update, context)
        return
    elif message == "📅 Recent Expenses":
        await update.message.reply_text("🔄 Fetching recent expenses...")
        expenses_text = await get_recent_expenses()
        await update.message.reply_text(expenses_text)
        return
    elif message == "💸 Today's Summary":
        await update.message.reply_text("🔄 Calculating today's expenses...")
        summary_text, _ = await get_daily_summary()
        await update.message.reply_text(summary_text)
        return
    elif message == "↩️ Undo last":
        await undo_command(update, context)
        return
    elif message == "🏓 Ping":
        await update.message.reply_text("pong 🏓")
        return

    # Enqueue expense for sequential processing per chat
    chat_id = str(update.message.chat_id)
    queued = queue_size(chat_id)
    if queued > 0:
        await update.message.reply_text(f"⏳ Queued (#{queued + 1}). Your expense will be processed shortly.")
    await enqueue_expense(chat_id, _process_expense(update, context))


async def _process_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Core expense processing logic executed inside the per-chat queue."""
    t_start = time.monotonic()
    message = update.message.text
    # Send initial message and store it for updates
    status_message = await update.message.reply_text("🔄 Processing your expense...")

    # Process message with OpenRouter - with retry
    await status_message.edit_text("🤖 Analyzing your expense with AI...")
    t_ai_start = time.monotonic()
    result, error = await process_with_openrouter(message)
    t_ai_end = time.monotonic()

    if error:
        await status_message.edit_text(f"❌ Error: {error}")
        # Add manual retry button for OpenRouter failures
        expense_id = f"{update.message.chat_id}-{update.message.message_id}"
        keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_openrouter_retry|id:{expense_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await status_message.edit_reply_markup(reply_markup=reply_markup)
        return

    processed_data, model_used = result
    amount, currency, category, description = processed_data

    # Store the expense data for confirmation
    expense_id = f"{update.message.chat_id}-{update.message.message_id}"
    pending_expenses[expense_id] = {
        'amount': amount,
        'currency': currency,
        'category': category,
        'description': description,
        'status_message': status_message
    }

    # Create confirmation buttons
    reply_markup = _confirmation_keyboard(expense_id)

    fallback_msg = ""
    if model_used != OPENROUTER_LLM_VERSION:
        fallback_msg = f"\n\n⚠️ <b>Fallback used:</b> <code>{model_used}</code>"

    # Build timing footer
    t_total = time.monotonic() - t_start
    t_ai = t_ai_end - t_ai_start
    model_short = model_used.split('/')[-1] if '/' in model_used else model_used
    timing_line = f"\n\n<pre>🤖 {model_short} · {t_ai:.2f}s AI · {t_total:.2f}s total</pre>"

    await status_message.edit_text(
        f"📊 Please confirm the expense (auto-confirms in 10s):\n"
        f"Amount: {amount} {currency}\n"
        f"Category: {category}\n"
        f"Description: {description}{fallback_msg}{timing_line}",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

    # Start auto-confirmation timer
    _schedule_auto_confirm(expense_id, context)

def _get_last_commit_info() -> str:
    """Return a short summary of the last git commit, or a fallback message."""
    try:
        subject = subprocess.check_output(
            ['git', 'log', '-1', '--pretty=format:%h %s'],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        files = subprocess.check_output(
            ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD'],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return f"{subject}\n{files}" if files else subject
    except Exception:
        return "(git info unavailable)"



def _build_startup_text() -> str:
    """Build the startup notification message (called once at module load)."""
    fallbacks = ", ".join(OPENROUTER_FALLBACK_MODELS) if OPENROUTER_FALLBACK_MODELS else "(none)"
    commit_info = _get_last_commit_info()
    return (
        "\U0001f680 <b>Bot started</b>\n\n"
        f"<b>SERVICE_URL:</b> <code>{SERVICE_URL}</code>\n"
        f"<b>LLM:</b> <code>{OPENROUTER_LLM_VERSION}</code>\n"
        f"<b>Fallbacks:</b> <code>{fallbacks}</code>\n\n"
        f"<b>Last commit:</b>\n<pre>{commit_info}</pre>"
    )

_startup_text = _build_startup_text()


async def _maybe_send_startup_notification(update: Update):
    """Send a one-time startup message to this chat if not already sent."""
    chat_id = str(update.effective_chat.id)
    if chat_id in _startup_notified:
        return
    _startup_notified.add(chat_id)

    try:
        await update.effective_chat.send_message(
            text=_startup_text,
            parse_mode='HTML',
        )
        logger.info("Startup notification sent to chat %s", chat_id)
    except Exception as e:
        logger.error("Failed to send startup notification to chat %s: %s", chat_id, e)



def create_application():
    """Create and configure the Telegram application."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("undo", undo_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    return application 

def start_telegram_polling():
    """Create application and start polling."""
    application = create_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    return application
