import logging
import re
import time
import asyncio
from asyncio import CancelledError
import os
import subprocess
import random
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)

from util.config import (
    TELEGRAM_BOT_TOKEN,
    get_llm_prompt,
    LOCAL_LLM_MODEL,
    OPENROUTER_LLM_VERSION,
    OPENROUTER_FALLBACK_MODELS,
    SERVICE_URL,
    GIT_COMMIT_SHORT,
    GIT_RECENT_COMMITS,
    GITHUB_REPO,
    APK_RELEASE_TAG,
)
from util.openrouter import process_with_openrouter
from util.postgres import (
    save_to_postgres,
    delete_last_expense_pg,
    get_daily_stats_pg,
    get_daily_summary_pg,
    get_recent_expenses_pg,
    upsert_chat_id,
    get_all_chat_ids,
    get_last_deployed_commit,
    set_last_deployed_commit,
)
from util.message_queue import enqueue_expense, queue_size


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
    category_pattern = r"- ([a-zA-Z\s]+) \(.*?\)"
    categories = [cat.strip() for cat in re.findall(category_pattern, prompt)]
    return categories


CATEGORIES = load_categories()


def get_command_keyboard():
    """Create a custom keyboard with primary buttons.
    The menu button reveals additional commands."""
    keyboard = [
        [KeyboardButton("📅 Recent Expenses"), KeyboardButton("↩️ Undo last")],
        [KeyboardButton("📱 Get App"), KeyboardButton("📋 Menu")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_full_command_keyboard():
    """Full set of command buttons (previous layout)."""
    keyboard = [
        [KeyboardButton("💰 Add Expense")],
        [KeyboardButton("📊 View Categories"), KeyboardButton("❓ Help")],
        [KeyboardButton("📅 Recent Expenses"), KeyboardButton("💸 Today's Summary")],
        [KeyboardButton("↩️ Undo last"), KeyboardButton("ℹ️ Bot Info")],
        [KeyboardButton("🖥️ Dashboard"), KeyboardButton("📱 Get App")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        "Hi! I'm your budget tracking bot. Send me messages in the format:\n"
        "amount currency category description\n"
        "Example: 25.50 USD food groceries",
        reply_markup=get_command_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        "Send me messages in the format:\n"
        "amount currency category description\n"
        "Example: 25.50 USD food groceries\n\n"
        "Commands:\n"
        "/summary - Get today's spending summary\n"
        "/undo - Delete the last expense\n"
        "/app - Get the Android app\n\n"
        "Or use the buttons below to interact with me!",
        reply_markup=get_command_keyboard(),
    )


def _resolve_apk_release_url() -> str | None:
    """Look up the latest APK asset URL from the GitHub release."""
    import requests

    api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{APK_RELEASE_TAG}"
    try:
        resp = requests.get(
            api,
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        resp.raise_for_status()
        for asset in resp.json().get("assets", []):
            if asset.get("name", "").endswith(".apk"):
                return asset.get("browser_download_url")
    except Exception as exc:
        logger.warning("Failed to resolve APK release URL: %s", exc)
    return None


def _download_apk_bytes(url: str) -> bytes | None:
    """Fetch the APK bytes (follows redirects to the signed asset URL)."""
    import requests

    try:
        resp = requests.get(url, timeout=60, allow_redirects=True)
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        logger.warning("Failed to download APK bytes: %s", exc)
        return None


async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the Android APK file to the user.

    Prefers a local build (dev); otherwise downloads the latest GitHub release
    asset server-side and sends the bytes. Telegram's URL-based fetch rejects
    application/vnd.android.package-archive, so we can't just forward the URL.
    """
    local_apk = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "android", "expense-tracker.apk"
    )
    if os.path.isfile(local_apk):
        await update.message.reply_document(
            document=open(local_apk, "rb"), filename="expense-tracker.apk"
        )
        return

    url = await asyncio.to_thread(_resolve_apk_release_url)
    if url:
        apk_bytes = await asyncio.to_thread(_download_apk_bytes, url)
        if apk_bytes:
            await update.message.reply_document(
                document=apk_bytes, filename="expense-tracker.apk"
            )
            return

    await update.message.reply_text(
        "APK not available yet. The latest build hasn't published a release."
    )


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the service URL link."""
    await update.message.reply_text(
        f"🌐 <b>Service URL:</b>\n<code>{SERVICE_URL}</code>", parse_mode="HTML"
    )


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send today's spending summary."""
    await update.message.reply_text("🔄 Calculating today's expenses...")  # type: ignore[union-attr]
    summary_text, _ = await get_daily_summary_pg()
    await update.message.reply_text(summary_text)  # type: ignore[union-attr]


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete the last expense."""
    await update.message.reply_text("🔄 Deleting last expense...")  # type: ignore[union-attr]

    expense_info, error = await delete_last_expense_pg()

    if error:
        await update.message.reply_text(f"❌ {error}")  # type: ignore[union-attr]
        return

    await update.message.reply_text(  # type: ignore[union-attr]
        f"🗑️ Deleted last expense:\n"
        f"Amount: {expense_info['amount']} {expense_info['currency']}\n"  # type: ignore[index]
        f"Category: {expense_info['category']}\n"  # type: ignore[index]
        f"Description: {expense_info['description']}\n"  # type: ignore[index]
        f"Type: {expense_info.get('spending_type', 'N/A')}"  # type: ignore[index]
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
        status_message = expense_data["status_message"]

        # Save to sheets (+ Postgres) with retry
        success, error = await save_to_postgres(
            expense_data["amount"],
            expense_data["currency"],
            expense_data["category"],
            expense_data["description"],
            spending_type=expense_data.get("spending_type"),
        )

        if success:
            # Get daily stats
            currency_totals, _ = await get_daily_stats_pg()

            # Format totals
            totals_str = format_daily_totals(currency_totals)

            # Add spending type to the message if present
            spending_type = expense_data.get('spending_type', '')
            type_line = f"\nType: {spending_type}" if spending_type else ""

            joke = random.choice(JOKES)
            final_text = (
                f"⏱️ Auto-confirmed: {expense_data['amount']} {expense_data['currency']} - "
                f"{expense_data['category']} - {expense_data['description']}{type_line}\n\n"
                f"💸 Total spent today: {totals_str}\n\n"
                f"{joke}"
            )
            await status_message.edit_text(final_text)
        else:
            final_text = f"❌ Error auto-saving to database: {error}"
            await status_message.edit_text(final_text)

            # Add manual retry button
            keyboard = [
                [
                    InlineKeyboardButton(
                        "🔄 Manual Retry",
                        callback_data=f"action:manual_retry|id:{expense_id}",
                    )
                ]
            ]
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
            row.append(
                InlineKeyboardButton(
                    f"✅ {category}",
                    callback_data=f"action:select_category|id:{expense_id}|category:{category}",
                )
            )
        else:
            row.append(
                InlineKeyboardButton(
                    category,
                    callback_data=f"action:select_category|id:{expense_id}|category:{category}",
                )
            )

        # Add 3 categories per row
        if len(row) == 3 or i == len(CATEGORIES) - 1:
            keyboard.append(row)
            row = []

    # Add back button
    keyboard.append(
        [InlineKeyboardButton("⬅️ Back", callback_data=f"action:back|id:{expense_id}")]
    )

    return InlineKeyboardMarkup(keyboard)


def parse_callback_data(data: str) -> dict:
    """Parse callback data into a dictionary."""
    result = {}
    for part in data.split("|"):
        if ":" in part:
            key, value = part.split(":", 1)
            result[key] = value
    return result


def _confirmation_keyboard(expense_id: str) -> InlineKeyboardMarkup:
    """Build the standard Confirm / Cancel / Change Category keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(
                "✅ Confirm", callback_data=f"action:confirm|id:{expense_id}"
            ),
            InlineKeyboardButton(
                "❌ Cancel", callback_data=f"action:cancel|id:{expense_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Change Category",
                callback_data=f"action:change_category|id:{expense_id}",
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _expenses_filter_keyboard(active_type: str = None, active_category: str = None) -> InlineKeyboardMarkup:
    """Build inline keyboard for filtering recent expenses."""
    rows = []

    # Type filter row
    type_buttons = []
    for label, value in [("All", "all"), ("Needs", "need"), ("Wants", "want"), ("Invest", "invest"), ("Wellbeing", "wellbeing")]:
        prefix = ">> " if (value == "all" and not active_type and not active_category) or active_type == value else ""
        type_buttons.append(
            InlineKeyboardButton(
                f"{prefix}{label}",
                callback_data=f"action:expenses_filter|type:{value}",
            )
        )
    rows.append(type_buttons)

    # Category filter button
    cat_label = f"Category: {active_category}" if active_category else "Filter by Category"
    rows.append([
        InlineKeyboardButton(cat_label, callback_data="action:expenses_pick_category"),
    ])

    return InlineKeyboardMarkup(rows)


def _category_picker_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard to pick a category for filtering expenses."""
    keyboard = []
    row = []
    for i, cat in enumerate(CATEGORIES):
        row.append(
            InlineKeyboardButton(cat, callback_data=f"action:expenses_filter|category:{cat}")
        )
        if len(row) == 3 or i == len(CATEGORIES) - 1:
            keyboard.append(row)
            row = []
    keyboard.append([
        InlineKeyboardButton("Show All", callback_data="action:expenses_filter|type:all"),
    ])
    return InlineKeyboardMarkup(keyboard)


async def _send_filtered_expenses(message_to_edit, category: str = None, spending_type: str = None):
    """Fetch and display filtered expenses, editing the given message."""
    kwargs = {}
    if category:
        kwargs["category"] = category
    if spending_type:
        kwargs["spending_type"] = spending_type

    expenses_text = await get_recent_expenses_pg(**kwargs)

    # Add filter label
    filter_label = ""
    if spending_type:
        filter_label = f" [{spending_type}]"
    elif category:
        filter_label = f" [{category}]"

    reply_markup = _expenses_filter_keyboard(
        active_type=spending_type,
        active_category=category,
    )

    # Telegram messages have a 4096 char limit; truncate if needed
    max_len = 4096 - 100  # leave room for markup overhead
    if len(expenses_text) > max_len:
        expenses_text = expenses_text[:max_len] + "\n... (truncated)"

    await message_to_edit.edit_text(expenses_text, reply_markup=reply_markup)


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
        await query.edit_message_text(
            "❌ Unable to retry: original message text is empty."
        )
        return

    await query.edit_message_text("🔄 Retrying OpenRouter API call...")

    result, error = await process_with_openrouter(original_message)

    if error:
        await query.edit_message_text(f"❌ Error: {error}")
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Manual Retry",
                    callback_data=f"action:manual_openrouter_retry|id:{expense_id}",
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_reply_markup(reply_markup=reply_markup)
        return

    processed_data, model_used = result
    amount, currency, category, spending_type, description = processed_data

    if not expense_id:
        expense_id = (
            f"{update.effective_message.chat_id}-{update.effective_message.message_id}"
        )

    pending_expenses[expense_id] = {
        "amount": amount,
        "currency": currency,
        "category": category,
        "spending_type": spending_type,
        "description": description,
        "status_message": query.message,
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
        parse_mode="Markdown",
    )

    _schedule_auto_confirm(expense_id, context)


async def _handle_sheet_retry(query, expense_id):
    """Handle manual retry for saving to Google Sheets."""
    await query.edit_message_text("🔄 Retrying to save to database...")

    expense_data = pending_expenses.get(expense_id)
    if not expense_data:
        await query.edit_message_text(
            "❌ Unable to retry: expense data no longer available."
        )
        return

    success, error = await save_to_postgres(
        expense_data["amount"],
        expense_data["currency"],
        expense_data["category"],
        expense_data["description"],
        spending_type=expense_data.get("spending_type"),
    )

    if success:
        currency_totals, _ = await get_daily_stats_pg()
        totals_str = format_daily_totals(currency_totals)

        # Add spending type to the message if present
        spending_type = expense_data.get('spending_type', '')
        type_line = f"\nType: {spending_type}" if spending_type else ""

        joke = random.choice(JOKES)
        final_text = (
            f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
            f"{expense_data['category']} - {expense_data['description']}{type_line}\n\n"
            f"💸 Total spent today: {totals_str}\n\n"
            f"{joke}"
        )
    else:
        final_text = f"❌ Error saving to database: {error}"

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Manual Retry",
                    callback_data=f"action:manual_retry|id:{expense_id}",
                )
            ]
        ]
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
    await query.edit_message_text("💾 Saving to database...")
    success, error = await save_to_postgres(
        expense_data["amount"],
        expense_data["currency"],
        expense_data["category"],
        expense_data["description"],
        spending_type=expense_data.get("spending_type"),
    )

    if success:
        currency_totals, _ = await get_daily_stats_pg()
        totals_str = format_daily_totals(currency_totals)

        # Add spending type to the message if present
        spending_type = expense_data.get('spending_type', '')
        type_line = f"\nType: {spending_type}" if spending_type else ""

        joke = random.choice(JOKES)
        final_text = (
            f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
            f"{expense_data['category']} - {expense_data['description']}{type_line}\n\n"
            f"💸 Total spent today: {totals_str}\n\n"
            f"{joke}"
        )
        await query.edit_message_text(final_text)
    else:
        final_text = f"❌ Error saving to database: {error}"

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Manual Retry",
                    callback_data=f"action:manual_retry|id:{expense_id}",
                )
            ]
        ]
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
    reply_markup = await show_category_buttons(expense_id, expense_data["category"])
    await query.edit_message_text(
        f"📊 Select a new category for:\n"
        f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
        f"Current category: {expense_data['category']}\n"
        f"Description: {expense_data['description']}",
        reply_markup=reply_markup,
    )
    _schedule_auto_confirm(expense_id, context)


async def _handle_select_category(query, expense_id, expense_data, data, context):
    """Handle category selection."""
    new_category = data.get("category")
    expense_data["category"] = new_category

    reply_markup = _confirmation_keyboard(expense_id)

    await query.edit_message_text(
        f"📊 Please confirm the expense:\n"
        f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
        f"Category: {expense_data['category']}\n"
        f"Description: {expense_data['description']}",
        reply_markup=reply_markup,
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
        reply_markup=reply_markup,
    )
    _schedule_auto_confirm(expense_id, context)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks — dispatches to per-action handlers."""
    query = update.callback_query
    await query.answer()

    data = parse_callback_data(query.data)
    action = data.get("action")
    expense_id = data.get("id")

    # Actions that don't require a pending expense
    if action == "manual_openrouter_retry":
        await _handle_openrouter_retry(query, expense_id, update, context)
        return

    if action == "manual_retry":
        await _handle_sheet_retry(query, expense_id)
        return

    if action == "expenses_filter":
        filter_type = data.get("type")
        filter_category = data.get("category")
        spending_type = filter_type if filter_type and filter_type != "all" else None
        await _send_filtered_expenses(
            query.message,
            category=filter_category,
            spending_type=spending_type,
        )
        return

    if action == "expenses_pick_category":
        await query.message.edit_text(
            "Select a category to filter by:",
            reply_markup=_category_picker_keyboard(),
        )
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
                await query.edit_message_text(
                    "❌ This expense has expired or was already processed."
                )
            return

        if action == "confirm":
            await _handle_confirm(query, expense_id, expense_data)
        elif action == "cancel":
            await _handle_cancel(query, expense_id)
        elif action == "change_category":
            await _handle_change_category(query, expense_id, expense_data, context)
        elif action == "select_category":
            await _handle_select_category(
                query, expense_id, expense_data, data, context
            )
        elif action == "back":
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
        categories_text = "Available categories:\n" + "\n".join(
            f"- {cat}" for cat in CATEGORIES
        )
        await update.message.reply_text(categories_text)
        return
    elif message == "❓ Help":
        await help_command(update, context)
        return
    elif message == "📅 Recent Expenses":
        status_msg = await update.message.reply_text("🔄 Fetching recent expenses...")
        await _send_filtered_expenses(status_msg)
        return
    elif message == "💸 Today's Summary":
        await update.message.reply_text("🔄 Calculating today's expenses...")
        summary_text, _ = await get_daily_summary_pg()
        await update.message.reply_text(summary_text)
        return
    elif message == "↩️ Undo last":
        await undo_command(update, context)
        return
    elif message == "ℹ️ Bot Info":
        info_text = _get_bot_info_text()
        await update.message.reply_text(info_text, parse_mode="HTML")
        return
    elif message == "📋 Menu":
        # Show full set of hidden commands
        await update.message.reply_text(
            "Select a command:", reply_markup=get_full_command_keyboard()
        )
        return
    elif message == "🖥️ Dashboard":
        # Handle dashboard button
        await dashboard_command(update, context)
        return
    elif message == "📱 Get App":
        await app_command(update, context)
        return

    # Enqueue expense for sequential processing per chat
    chat_id = str(update.message.chat_id)
    queued = queue_size(chat_id)
    if queued > 0:
        await update.message.reply_text(
            f"⏳ Queued (#{queued + 1}). Your expense will be processed shortly."
        )
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
        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Manual Retry",
                    callback_data=f"action:manual_openrouter_retry|id:{expense_id}",
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await status_message.edit_reply_markup(reply_markup=reply_markup)
        return

    processed_data, model_used = result
    amount, currency, category, spending_type, description = processed_data

    # Store the expense data for confirmation
    expense_id = f"{update.message.chat_id}-{update.message.message_id}"
    pending_expenses[expense_id] = {
        "amount": amount,
        "currency": currency,
        "category": category,
        "spending_type": spending_type,
        "description": description,
        "status_message": status_message,
    }

    # Create confirmation buttons
    reply_markup = _confirmation_keyboard(expense_id)

    fallback_msg = ""
    if model_used != OPENROUTER_LLM_VERSION:
        fallback_msg = f"\n\n⚠️ <b>Fallback used:</b> <code>{model_used}</code>"

    # Build timing footer
    t_total = time.monotonic() - t_start
    t_ai = t_ai_end - t_ai_start
    model_short = model_used.split("/")[-1] if "/" in model_used else model_used
    timing_line = (
        f"\n\n<pre>🤖 {model_short} · {t_ai:.2f}s AI · {t_total:.2f}s total</pre>"
    )

    type_line = f"\nType: {spending_type}" if spending_type else ""

    await status_message.edit_text(
        f"📊 Please confirm the expense (auto-confirms in 10s):\n"
        f"Amount: {amount} {currency}\n"
        f"Category: {category}{type_line}\n"
        f"Description: {description}{fallback_msg}{timing_line}",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    # Start auto-confirmation timer
    _schedule_auto_confirm(expense_id, context)


def _get_recent_commits_info() -> str:
    """Return a short summary of the 3 latest git commits, or a fallback message."""
    try:
        log = subprocess.check_output(
            ["git", "log", "-3", "--pretty=format:%h %s"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if log:
            return log
    except Exception:
        pass
    # Fall back to build-time env var (set in Docker)
    if GIT_RECENT_COMMITS:
        return GIT_RECENT_COMMITS
    return f"{GIT_COMMIT_SHORT}" if GIT_COMMIT_SHORT != "unknown" else "(git info unavailable)"


def _get_bot_info_text() -> str:
    """Build the bot info message with version and config details."""
    fallback_models = [OPENROUTER_LLM_VERSION] + (OPENROUTER_FALLBACK_MODELS or [])
    fallbacks_str = ", ".join(fallback_models)
    commits_info = _get_recent_commits_info()
    return (
        "🤖 <b>Bot Information</b>\n\n"
        f"<b>SERVICE_URL:</b> <code>{SERVICE_URL}</code>\n"
        f"<b>Local LLM:</b> <code>{LOCAL_LLM_MODEL}</code>\n"
        f"<b>Cloud fallbacks:</b> <code>{fallbacks_str}</code>\n\n"
        f"<b>Recent commits:</b>\n<pre>{commits_info}</pre>"
    )


BOT_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("app", "Get the Android app (APK)"),
    BotCommand("summary", "Today's spending summary"),
    BotCommand("undo", "Delete the last expense"),
    BotCommand("dashboard", "Open the web dashboard"),
    BotCommand("help", "How to use the bot"),
]


async def _on_post_init(application: Application) -> None:
    """Register commands and send deploy notification if version changed."""
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception as exc:
        logger.warning("set_my_commands failed: %s", exc)

    await _notify_deploy(application)


async def _notify_deploy(application: Application) -> None:
    """Send a deploy notification to all known chats if the commit changed."""
    if GIT_COMMIT_SHORT == "unknown":
        return
    try:
        prev_commit = await get_last_deployed_commit()
        if prev_commit == GIT_COMMIT_SHORT:
            return  # same version, no notification

        commits_info = _get_recent_commits_info()
        text = (
            "🚀 <b>New version deployed!</b>\n\n"
            f"<b>Recent commits:</b>\n<pre>{commits_info}</pre>"
        )

        chat_ids = await get_all_chat_ids()
        for chat_id in chat_ids:
            try:
                await application.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode="HTML"
                )
            except Exception as exc:
                logger.warning("deploy notification to %s failed: %s", chat_id, exc)

        await set_last_deployed_commit(GIT_COMMIT_SHORT)
    except Exception as exc:
        logger.warning("deploy notification failed: %s", exc)


def create_application():
    """Create and configure the Telegram application."""
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_on_post_init)
        .build()
    )

    # Track chat IDs for deploy notifications (runs on every update, group -1)
    async def _track_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat:
            try:
                await upsert_chat_id(update.effective_chat.id)
            except Exception:
                pass

    application.add_handler(MessageHandler(filters.ALL, _track_chat), group=-1)

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("summary", summary_command))
    application.add_handler(CommandHandler("undo", undo_command))
    application.add_handler(CommandHandler("app", app_command))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(CallbackQueryHandler(button_callback))

    return application


def start_telegram_polling():
    """Create application and start polling."""
    application = create_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

    return application
