import logging
import re
import time
import asyncio
from asyncio import CancelledError
import os
import subprocess
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

from util.config import (
    TELEGRAM_BOT_TOKEN, get_llm_prompt, OPENROUTER_LLM_VERSION,
    OPENROUTER_FALLBACK_MODELS, SERVICE_URL, DATABASE_URL,
)
from util.sheets import save_to_sheets, get_recent_expenses, get_daily_summary, get_daily_stats, delete_last_expense
from util.openrouter import process_with_openrouter

if DATABASE_URL:
    from util.postgres import (
        save_to_postgres, delete_last_expense_pg,
        get_daily_stats_pg, get_daily_summary_pg, get_recent_expenses_pg,
    )

from util.message_queue import enqueue_expense, queue_size


async def _dual_save(amount, currency, category, description):
    """Save to Sheets (+ Postgres when configured). Returns Sheets result."""
    if DATABASE_URL:
        sheets_coro = save_to_sheets(amount, currency, category, description)
        pg_coro = save_to_postgres(amount, currency, category, description)
        (sheets_result, pg_result) = await asyncio.gather(sheets_coro, pg_coro, return_exceptions=True)
        if isinstance(pg_result, Exception):
            logger.warning(f"Postgres save failed (non-blocking): {pg_result}")
        elif isinstance(pg_result, tuple) and pg_result[1] is not None:
            logger.warning(f"Postgres save error (non-blocking): {pg_result[1]}")
        if isinstance(sheets_result, Exception):
            raise sheets_result
        return sheets_result
    return await save_to_sheets(amount, currency, category, description)


async def _dual_delete():
    """Delete from Sheets (+ Postgres when configured). Returns Sheets result."""
    if DATABASE_URL:
        sheets_coro = delete_last_expense()
        pg_coro = delete_last_expense_pg()
        (sheets_result, pg_result) = await asyncio.gather(sheets_coro, pg_coro, return_exceptions=True)
        if isinstance(pg_result, Exception):
            logger.warning(f"Postgres delete failed (non-blocking): {pg_result}")
        if isinstance(sheets_result, Exception):
            raise sheets_result
        return sheets_result
    return await delete_last_expense()

logger = logging.getLogger(__name__)

# Store pending expenses
pending_expenses = {}
recently_processed_expenses = {}
expense_locks = {}
auto_confirm_tasks = {}
PROCESSED_EXPENSE_TTL_SECONDS = 30



def _schedule_auto_confirm(expense_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Schedule the auto-confirm timer as a background task."""
    try:
        if expense_id in auto_confirm_tasks:
            try:
                auto_confirm_tasks[expense_id].cancel()
            except CancelledError:
                pass
    except RuntimeError:
        pass
    
    try:
        task = asyncio.create_task(auto_confirm_expense(expense_id, context))
        auto_confirm_tasks[expense_id] = task
    except RuntimeError:
        pass


async def _cleanup_processed_expense(expense_id: str):
    """Drop processed state after a short retention window."""
    try:
        await asyncio.sleep(PROCESSED_EXPENSE_TTL_SECONDS)
    except CancelledError:
        pass
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
    """Create a custom keyboard with primary buttons.
    The menu button reveals additional commands."""
    keyboard = [
        [KeyboardButton("📅 Recent Expenses"), KeyboardButton("↩️ Undo last")],
        [KeyboardButton("📋 Menu")]  # Button to show hidden options
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_full_command_keyboard():
    """Full set of command buttons (previous layout)."""
    keyboard = [
        [KeyboardButton("💰 Add Expense")],
        [KeyboardButton("📊 View Categories"), KeyboardButton("❓ Help")],
        [KeyboardButton("📅 Recent Expenses"), KeyboardButton("💸 Today's Summary")],
        [KeyboardButton("↩️ Undo last"), KeyboardButton("ℹ️ Bot Info")],
        [KeyboardButton("🖥️ Dashboard")]  # /dashboard command
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        'Hi! I\'m your budget tracking bot. Send me messages in the format:\n'
        'amount currency category description\n'
        'Example: 25.50 USD food groceries',
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
        '/undo - Delete the last expense\n'
        '/app - Get the Android app\n\n'
        'Or use the buttons below to interact with me!',
        reply_markup=get_command_keyboard()
    )

async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the Android APK file to the user."""
    apk_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "android", "budget-tracker.apk")
    if os.path.isfile(apk_path):
        await update.message.reply_document(document=open(apk_path, "rb"), filename="budget-tracker.apk")
    else:
        await update.message.reply_text("APK not available. Please build it first with `make build-apk`.")


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the service URL link."""
    await update.message.reply_text(f"🌐 <b>Service URL:</b>\n<code>{SERVICE_URL}</code>", parse_mode='HTML')


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send today's spending summary."""
    await update.message.reply_text("🔄 Calculating today's expenses...")
    _get_summary = get_daily_summary_pg if DATABASE_URL else get_daily_summary
    summary_text, _ = await _get_summary()
    await update.message.reply_text(summary_text)


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete the last expense from the sheet."""
    await update.message.reply_text("🔄 Deleting last expense...")

    expense_info, error = await _dual_delete()

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

        # Save to sheets (+ Postgres) with retry
        success, error = await _dual_save(
            expense_data['amount'],
            expense_data['currency'],
            expense_data['category'],
            expense_data['description']
        )

        if success:
            # Get daily stats
            _get_stats = get_daily_stats_pg if DATABASE_URL else get_daily_stats
            currency_totals, _ = await _get_stats()

            # Format totals
            totals_str = format_daily_totals(currency_totals)

            joke = random.choice(JOKES)
            final_text = (
                f"⏱️ Auto-confirmed: {expense_data['amount']} {expense_data['currency']} - "
                f"{expense_data['category']} - {expense_data['description']}\n\n"
                f"💸 Total spent today: {totals_str}\n\n"
                f"{joke}"
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

    success, error = await _dual_save(
        expense_data['amount'],
        expense_data['currency'],
        expense_data['category'],
        expense_data['description']
    )

    if success:
        _get_stats = get_daily_stats_pg if DATABASE_URL else get_daily_stats
        currency_totals, _ = await _get_stats()
        totals_str = format_daily_totals(currency_totals)

        joke = random.choice(JOKES)
        final_text = (
            f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
            f"{expense_data['category']} - {expense_data['description']}\n\n"
            f"💸 Total spent today: {totals_str}\n\n"
            f"{joke}"
        )
    else:
        final_text = f"❌ Error saving to spreadsheet: {error}"

        keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_sheet_retry|id:{expense_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_reply_markup(reply_markup=reply_markup)

    await query.edit_message_text(final_text)

    pending_expenses.pop(expense_id, None)
    auto_confirm_tasks.pop(expense_id, None)
    _remember_processed_expense(expense_id, final_text)


JOKES = [
    "💰 Money doesn't grow on trees, but at least you're tracking it!",
    "📉 Every expense tells a story... yours is being saved!",
    "💸 Spending wisely? Or just spending? Either way, tracked!",
    "🧾 Another expense! Your future self will thank you for tracking.",
    "🎯 Budget goals: 1% better every day. Today's step: tracked!",
    "💼 Expense saved! Remember: even small leaks sink ships.",
    "📊 Data point added. Your wallet is crying, but you're learning!",
    "✨ Another expense logged! Financial freedom starts with awareness.",
    "🎲 Luck favors the prepared. Your expenses are now prepared!",
    "💡 Pro tip: Tracking expenses is like dieting for your wallet.",
]

async def _handle_confirm(query, expense_id, expense_data):
    """Handle expense confirmation."""
    await query.edit_message_text("💾 Saving to spreadsheet...")
    success, error = await _dual_save(
        expense_data['amount'],
        expense_data['currency'],
        expense_data['category'],
        expense_data['description']
    )

    if success:
        _get_stats = get_daily_stats_pg if DATABASE_URL else get_daily_stats
        currency_totals, _ = await _get_stats()
        totals_str = format_daily_totals(currency_totals)

        joke = random.choice(JOKES)
        final_text = (
            f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
            f"{expense_data['category']} - {expense_data['description']}\n\n"
            f"💸 Total spent today: {totals_str}\n\n"
            f"{joke}"
        )
        await query.edit_message_text(final_text)
    else:
        final_text = f"❌ Error saving to spreadsheet: {error}"

        keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_sheet_retry|id:{expense_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(final_text, reply_markup=reply_markup)

    pending_expenses.pop(expense_id, None)
    auto_confirm_tasks.pop(expense_id, None)
    _remember_processed_expense(expense_id, final_text)


async def _handle_cancel(query, expense_id):
    """Handle expense cancellation."""
    final_text = "Expense cancelled."
    await query.edit_message_text(final_text)
    pending_expenses.pop(expense_id, None)
    auto_confirm_tasks.pop(expense_id, None)
    _remember_processed_expense(expense_id, final_text)


async def _handle_change_category(query, expense_id, expense_data, context):
    """Show category selection buttons."""
    reply_markup = await show_category_buttons(expense_id, expense_data['category'])
    await query.edit_message_text(
        f"📊 Select a new category for:\n"
        f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
        f"Current category: {expense_data['category']}\n"
        f"Description: {expense_data['description']}",
        reply_markup=reply_markup
    )
    _schedule_auto_confirm(expense_id, context)



async def _handle_select_category(query, expense_id, expense_data, data, context):
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
    _schedule_auto_confirm(expense_id, context)


async def _handle_back(query, expense_id, expense_data, context):
    """Handle back button — show the confirmation screen again."""
    reply_markup = _confirmation_keyboard(expense_id)

    await query.edit_message_text(
        f"📊 Please confirm the expense:\n"
        f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
        f"Category: {expense_data['category']}\n"
        f"Description: {expense_data['description']}",
        reply_markup=reply_markup
    )
    _schedule_auto_confirm(expense_id, context)


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
            await _handle_change_category(query, expense_id, expense_data, context)
        elif action == 'select_category':
            await _handle_select_category(query, expense_id, expense_data, data, context)
        elif action == 'back':
            await _handle_back(query, expense_id, expense_data, context)



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
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
        _get_recent = get_recent_expenses_pg if DATABASE_URL else get_recent_expenses
        expenses_text = await _get_recent()
        await update.message.reply_text(expenses_text)
        return
    elif message == "💸 Today's Summary":
        await update.message.reply_text("🔄 Calculating today's expenses...")
        _get_summary = get_daily_summary_pg if DATABASE_URL else get_daily_summary
        summary_text, _ = await _get_summary()
        await update.message.reply_text(summary_text)
        return
    elif message == "↩️ Undo last":
        await undo_command(update, context)
        return
    elif message == "ℹ️ Bot Info":
        info_text = _get_bot_info_text()
        await update.message.reply_text(info_text, parse_mode='HTML')
        return
    elif message == "📋 Menu":
        # Show full set of hidden commands
        await update.message.reply_text("Select a command:", reply_markup=get_full_command_keyboard())
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


def _get_bot_info_text() -> str:
    """Build the bot info message with version and config details."""
    fallbacks = ", ".join(OPENROUTER_FALLBACK_MODELS) if OPENROUTER_FALLBACK_MODELS else "(none)"
    commit_info = _get_last_commit_info()
    return (
        "🤖 <b>Bot Information</b>\n\n"
        f"<b>SERVICE_URL:</b> <code>{SERVICE_URL}</code>\n"
        f"<b>LLM:</b> <code>{OPENROUTER_LLM_VERSION}</code>\n"
        f"<b>Fallbacks:</b> <code>{fallbacks}</code>\n\n"
        f"<b>Last commit:</b>\n<pre>{commit_info}</pre>"
    )



def create_application():
    """Create and configure the Telegram application."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("undo", undo_command))
    application.add_handler(CommandHandler("app", app_command))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    return application 

def start_telegram_polling():
    """Create application and start polling."""
    application = create_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    return application
