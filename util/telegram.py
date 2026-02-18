import logging
import re
import time
import asyncio
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

from util.config import TELEGRAM_BOT_TOKEN, get_llm_prompt, OPENROUTER_LLM_VERSION
from util.sheets import save_to_sheets, get_recent_expenses, get_daily_summary, get_daily_stats
from util.openrouter import process_with_openrouter
from util.scheduler import start_daily_summary_scheduler
from util.message_queue import enqueue_expense, queue_size

logger = logging.getLogger(__name__)

# Store pending expenses
pending_expenses = {}
recently_processed_expenses = {}
expense_locks = {}
PROCESSED_EXPENSE_TTL_SECONDS = 30


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
        [KeyboardButton("🏓 Ping")]
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
        '/summary - Get today\'s spending summary\n\n'
        'Or use the buttons below to interact with me!',
        reply_markup=get_command_keyboard()
    )

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send today's spending summary."""
    await update.message.reply_text("🔄 Calculating today's expenses...")
    summary_text, _ = await get_daily_summary()
    await update.message.reply_text(summary_text)

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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = parse_callback_data(query.data)
    action = data.get('action')
    expense_id = data.get('id')
    
    if action == 'manual_openrouter_retry':
        # Handle manual retry for OpenRouter API call
        # The original message is the one that was replied to by the status message
        if not query.message or not query.message.reply_to_message:
            await query.edit_message_text("❌ Unable to retry: original message not found.")
            return

        original_message = query.message.reply_to_message.text
        if not original_message:
            await query.edit_message_text("❌ Unable to retry: original message text is empty.")
            return

        await query.edit_message_text("🔄 Retrying OpenRouter API call...")

        # Process message with OpenRouter - with retry
        # process_with_openrouter now returns ((data), model_name), error
        result, error = await process_with_openrouter(original_message)

        if error:
            await query.edit_message_text(f"❌ Error: {error}")
            # Add manual retry button again
            keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_openrouter_retry|id:{expense_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_reply_markup(reply_markup=reply_markup)
            return

        processed_data, model_used = result
        amount, currency, category, description = processed_data

        # Store the expense data for confirmation
        # Use the ID from callback data if available, otherwise generate from message
        if not expense_id:
            expense_id = f"{update.effective_message.chat_id}-{update.effective_message.message_id}"
            
        pending_expenses[expense_id] = {
            'amount': amount,
            'currency': currency,
            'category': category,
            'description': description,
            'status_message': query.message
        }

        # Create confirmation buttons
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

        # Start auto-confirmation timer
        _schedule_auto_confirm(expense_id, context)
        return

    if action == 'manual_sheet_retry':
        # Handle manual retry for saving to Google Sheets
        await query.edit_message_text("🔄 Retrying to save to spreadsheet...")

        # Retrieve expense data from the stored pending expense if still available
        expense_data = pending_expenses.get(expense_id)
        if not expense_data:
            # If expense is not in pending (e.g. from auto-confirm), use the original data from context
            await query.edit_message_text("❌ Unable to retry: expense data no longer available.")
            return

        # Retry saving to sheets
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
                f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
                f"{expense_data['category']} - {expense_data['description']}\n\n"
                f"💸 Total spent today: {totals_str}"
            )
        else:
            final_text = f"❌ Error saving to spreadsheet: {error}"

            # Show retry button again if it still fails
            keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_sheet_retry|id:{expense_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_reply_markup(reply_markup=reply_markup)

        await query.edit_message_text(final_text)

        # Clean up regardless of success or failure as the button action has been processed
        pending_expenses.pop(expense_id, None)
        _remember_processed_expense(expense_id, final_text)
        return

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
            await query.edit_message_text("💾 Saving to spreadsheet...")
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
                    f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
                    f"{expense_data['category']} - {expense_data['description']}\n\n"
                    f"💸 Total spent today: {totals_str}"
                )
            else:
                final_text = f"❌ Error saving to spreadsheet: {error}"

                # Add manual retry button for failed saves
                keyboard = [[InlineKeyboardButton("🔄 Manual Retry", callback_data=f"action:manual_sheet_retry|id:{expense_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_reply_markup(reply_markup=reply_markup)

            await query.edit_message_text(final_text)

            # Clean up regardless of success or failure as the button action has been processed
            pending_expenses.pop(expense_id, None)
            _remember_processed_expense(expense_id, final_text)
                
        elif action == 'cancel':
            final_text = "Expense cancelled."
            await query.edit_message_text(final_text)
            pending_expenses.pop(expense_id, None)
            _remember_processed_expense(expense_id, final_text)
        
        elif action == 'change_category':
            # Show category selection buttons
            reply_markup = await show_category_buttons(expense_id, expense_data['category'])
            await query.edit_message_text(
                f"📊 Select a new category for:\n"
                f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
                f"Current category: {expense_data['category']}\n"
                f"Description: {expense_data['description']}",
                reply_markup=reply_markup
            )
        
        elif action == 'select_category':
            # Update the category
            new_category = data.get('category')
            expense_data['category'] = new_category
            
            # Show the main confirmation buttons again
            reply_markup = _confirmation_keyboard(expense_id)
            
            await query.edit_message_text(
                f"📊 Please confirm the expense:\n"
                f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
                f"Category: {expense_data['category']}\n"
                f"Description: {expense_data['description']}",
                reply_markup=reply_markup
            )
        
        elif action == 'back':
            # Show the main confirmation buttons again
            reply_markup = _confirmation_keyboard(expense_id)

            await query.edit_message_text(
                f"📊 Please confirm the expense:\n"
                f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
                f"Category: {expense_data['category']}\n"
                f"Description: {expense_data['description']}",
                reply_markup=reply_markup
            )


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
        expenses_text = await get_recent_expenses()
        await update.message.reply_text(expenses_text)
        return
    elif message == "💸 Today's Summary":
        await update.message.reply_text("🔄 Calculating today's expenses...")
        summary_text, _ = await get_daily_summary()
        await update.message.reply_text(summary_text)
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

def create_application():
    """Create and configure the Telegram application."""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    return application 

def start_telegram_polling():
    """Create application and start polling."""
    application = create_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    return application
