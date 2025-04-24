import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
import requests
from flask import Flask
from threading import Thread

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

def run_flask():
    app.run(host='0.0.0.0', port=8000)

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

def get_google_sheets_service():
    """Initialize and return Google Sheets service."""
    creds = service_account.Credentials.from_service_account_file(
        'credentials.json', scopes=SCOPES)
    service = build('sheets', 'v4', credentials=creds)
    return service

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    await update.message.reply_text(
        'Hi! I\'m your budget tracking bot. Send me messages in the format:\n'
        'amount currency category description\n'
        'Example: 25.50 USD food groceries'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    await update.message.reply_text(
        'Send me messages in the format:\n'
        'amount currency category description\n'
        'Example: 25.50 USD food groceries'
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages."""
    message = update.message.text
    
    # Check if message is "ping"
    if message.lower() == "ping":
        await update.message.reply_text("pong 🏓")
        return
    
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
            InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{expense_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{expense_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await status_message.edit_text(
        f"📊 Please confirm the expense:\n"
        f"Amount: {amount} {currency}\n"
        f"Category: {category}\n"
        f"Description: {description}",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()
    
    action, expense_id = query.data.split('_')
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
    
    elif action == 'cancel':
        await query.edit_message_text("❌ Expense cancelled.")
    
    # Clean up
    if expense_id in pending_expenses:
        del pending_expenses[expense_id]

def main():
    """Start the bot."""
    # Start Flask in a separate thread (for health check)
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

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