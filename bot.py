import os
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import requests
from flask import Flask
from threading import Thread
import re
import time
import asyncio

# Load environment variables directly from .env
load_dotenv('.env')

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Create Flask app for health check
app = Flask(__name__)

@app.route('/health')
def health_check():
    return 'OK', 200

@app.route('/nudge')
def nudge():
    """Endpoint to keep the service alive."""
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=8000)

def run_nudge_pinger():
    """Run the nudge pinger in a separate thread."""
    service_url = os.getenv('SERVICE_URL', 'http://0.0.0.0:8000')
    nudge_url = f"{service_url}/nudge"
    logger.info(f"Starting nudge pinger for {nudge_url}...")
    
    while True:
        try:
            response = requests.get(nudge_url)
            if response.status_code == 200:
                logger.info(f"Successfully pinged {nudge_url}")
            else:
                logger.error(f"Failed to ping {nudge_url}: {response.status_code}")
        except Exception as e:
            logger.error(f"Error pinging {nudge_url}: {str(e)}")
        time.sleep(60)  # Sleep for 1 minute

# Load prompt and extract categories
def load_categories():
    with open('prompt.txt', 'r') as f:
        prompt = f.read()
    # Extract categories from the prompt using regex
    category_pattern = r'- ([a-zA-Z]+) \(.*?\)'
    categories = re.findall(category_pattern, prompt)
    return categories

CATEGORIES = load_categories()

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = os.getenv('GOOGLE_SHEET_ID')
SHEET_NAME = 'Form Responses 1'  # Specific sheet name
RANGE_NAME = f'{SHEET_NAME}!A:F'  # Using columns A through F

# OpenRouter setup
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_LLM_VERSION = os.getenv('OPENROUTER_LLM_VERSION', 'anthropic/claude-3-opus-20240229')

# Load prompt from prompt.txt
LLM_PROMPT = open('prompt.txt', 'r').read()

# Store pending expenses
pending_expenses = {}

# Get credentials path from environment variable
CREDENTIALS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')

def get_google_sheets_service():
    """Initialize and return Google Sheets service."""
    try:
        creds = service_account.Credentials.from_service_account_file(
            CREDENTIALS_PATH, scopes=SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Failed to initialize Google Sheets service: {str(e)}")
        raise

def get_command_keyboard():
    """Create a custom keyboard with command buttons."""
    keyboard = [
        [KeyboardButton("💰 Add Expense")],
        [KeyboardButton("📊 View Categories"), KeyboardButton("❓ Help")],
        [KeyboardButton("📅 Recent Expenses"), KeyboardButton("🏓 Ping")]
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
        'Or use the buttons below to interact with me!',
        reply_markup=get_command_keyboard()
    )

async def process_with_openrouter(message: str) -> tuple:
    """Process message using OpenRouter API and return formatted data."""
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/stanislavin/telegram-budget-bot",
        }
        
        prompt = LLM_PROMPT + "\n\nDescription of expense is: " + message
        data = {
            "model": OPENROUTER_LLM_VERSION,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        response = requests.post(OPENROUTER_URL, headers=headers, json=data)
        response.raise_for_status()
        
        # Extract the formatted response
        formatted_text = response.json()['choices'][0]['message']['content'].strip()
        
        # Parse the formatted response
        parts = formatted_text.split(',')
        if len(parts) != 4:
            return None, "Failed to parse OpenRouter response"
            
        amount = float(parts[0])
        currency = parts[1].upper()
        category = parts[2]
        description = parts[3]
        
        return (amount, currency, category, description), None
        
    except Exception as e:
        return None, f"Error processing with OpenRouter: {str(e)}"

async def save_to_sheets(amount: float, currency: str, category: str, description: str):
    """Save the expense to Google Sheets."""
    try:
        service = get_google_sheets_service()
        sheet = service.spreadsheets()
        
        # Format timestamp as YYYY-MM-DD HH:MM:SS
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Prepare the row data with specific column layout
        row = [
            timestamp,      # Column A - timestamp
            amount,        # Column B - amount
            category,      # Column C - category
            description,   # Column D - description
            "",              # Column E - empty
            currency       # Column F - currency
        ]
        
        body = {
            'values': [row]
        }
        
        # Append the row to the sheet
        result = sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        
        return True, None
    except Exception as e:
        return False, str(e)

async def auto_confirm_expense(expense_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Automatically confirm an expense after 10 seconds if no user action."""
    await asyncio.sleep(10)  # Wait for 10 seconds
    
    # Check if expense still exists (not confirmed or cancelled)
    expense_data = pending_expenses.get(expense_id)
    if not expense_data:
        return
    
    # Get the status message
    status_message = expense_data['status_message']
    
    # Save to sheets
    success, error = await save_to_sheets(
        expense_data['amount'],
        expense_data['currency'],
        expense_data['category'],
        expense_data['description']
    )
    
    if success:
        await status_message.edit_text(
            f"⏱️ Auto-confirmed: {expense_data['amount']} {expense_data['currency']} - "
            f"{expense_data['category']} - {expense_data['description']}"
        )
    else:
        await status_message.edit_text(f"❌ Error auto-saving to spreadsheet: {error}")
    
    # Clean up
    if expense_id in pending_expenses:
        del pending_expenses[expense_id]

async def get_recent_expenses(days: int = 2):
    """Fetch expenses from the last N days from Google Sheets."""
    try:
        service = get_google_sheets_service()
        sheet = service.spreadsheets()
        
        # Get today's date and N-1 days before
        today = datetime.now().date()
        start_date = today - timedelta(days=days-1)
        
        # Format dates for comparison
        logger.info(f"Fetching expenses from {start_date.strftime('%Y-%m-%d')} to {today.strftime('%Y-%m-%d')}")
        
        # Get all data from the sheet
        result = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            return "No expenses found."
        
        # Filter and format expenses
        recent_expenses = []
        total_amount = 0
        currency = None
        
        for row in values[1:]:  # Skip header row
            if len(row) >= 6:  # Ensure row has all required columns
                try:
                    # Parse the timestamp from the sheet
                    timestamp_str = row[0]  # Column A
                    timestamp = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S")
                    
                    # Check if the expense is within the date range
                    if start_date <= timestamp.date() <= today:
                        amount = float(row[1])  # Column B
                        description = row[3]  # Column D
                        currency = row[5]  # Column F
                        
                        # Format date and amount
                        formatted_date = timestamp.strftime("%d/%m")
                        whole_amount = int(amount)
                        
                        # Create a simple line
                        line = f"{formatted_date} {whole_amount}{currency} {description}"
                        # Truncate line if longer than 36 characters
                        # (figured out by trial and error)
                        if len(line) > 36:
                            line = line[:33] + "..."
                        recent_expenses.append(line)
                        total_amount += amount
                except (ValueError, IndexError) as e:
                    logger.error(f"Error parsing row: {row}, error: {str(e)}")
                    continue
        
        if not recent_expenses:
            return f"No expenses found for the last {days} days."
        
        # Format the message
        message = f"Expenses for the last {days} days:\n\n"
        message += "\n".join(recent_expenses)
        
        return message
        
    except Exception as e:
        logger.error(f"Error fetching recent expenses: {str(e)}")
        return f"Error fetching expenses: {str(e)}"

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
    elif message == "🏓 Ping":
        await update.message.reply_text("pong 🏓")
        return
    
    # Process regular expense messages
    # Send initial message and store it for updates
    status_message = await update.message.reply_text("🔄 Processing your expense...")
    
    # Process message with OpenRouter
    await status_message.edit_text("🤖 Analyzing your expense with AI...")
    processed_data, error = await process_with_openrouter(message)
    
    if error:
        await status_message.edit_text(f"❌ Error: {error}")
        return
    
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
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"action:confirm|id:{expense_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"action:cancel|id:{expense_id}")
        ],
        [
            InlineKeyboardButton("🔄 Change Category", callback_data=f"action:change_category|id:{expense_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await status_message.edit_text(
        f"📊 Please confirm the expense (auto-confirms in 10s):\n"
        f"Amount: {amount} {currency}\n"
        f"Category: {category}\n"
        f"Description: {description}",
        reply_markup=reply_markup
    )
    
    # Start auto-confirmation timer
    asyncio.create_task(auto_confirm_expense(expense_id, context))

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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = parse_callback_data(query.data)
    action = data.get('action')
    expense_id = data.get('id')
    expense_data = pending_expenses.get(expense_id)
    
    if not expense_data:
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
            await query.edit_message_text(
                f"✅ Saved: {expense_data['amount']} {expense_data['currency']} - "
                f"{expense_data['category']} - {expense_data['description']}"
            )
        else:
            await query.edit_message_text(f"❌ Error saving to spreadsheet: {error}")
        
        # Clean up
        if expense_id in pending_expenses:
            del pending_expenses[expense_id]
    
    elif action == 'cancel':
        await query.edit_message_text("Expense cancelled.")
        if expense_id in pending_expenses:
            del pending_expenses[expense_id]
    
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
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"action:confirm|id:{expense_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"action:cancel|id:{expense_id}")
            ],
            [
                InlineKeyboardButton("🔄 Change Category", callback_data=f"action:change_category|id:{expense_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📊 Please confirm the expense:\n"
            f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
            f"Category: {expense_data['category']}\n"
            f"Description: {expense_data['description']}",
            reply_markup=reply_markup
        )
    
    elif action == 'back':
        # Show the main confirmation buttons again
        keyboard = [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"action:confirm|id:{expense_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"action:cancel|id:{expense_id}")
            ],
            [
                InlineKeyboardButton("🔄 Change Category", callback_data=f"action:change_category|id:{expense_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📊 Please confirm the expense:\n"
            f"Amount: {expense_data['amount']} {expense_data['currency']}\n"
            f"Category: {expense_data['category']}\n"
            f"Description: {expense_data['description']}",
            reply_markup=reply_markup
        )

def main():
    """Start the bot."""
    # Start Flask in a separate thread (for health check)
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start nudge pinger in a separate thread
    nudge_thread = Thread(target=run_nudge_pinger)
    nudge_thread.daemon = True
    nudge_thread.start()

    # Create the Application and pass it your bot's token
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 