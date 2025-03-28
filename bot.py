import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

# Load environment variables directly from .env.example
load_dotenv('.env.variables')

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = os.getenv('GOOGLE_SHEET_ID')
SHEET_NAME = 'Form Responses 1'  # Specific sheet name
RANGE_NAME = f'{SHEET_NAME}!A:F'  # Using columns A through F

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

def process_message(message: str) -> tuple:
    """Process the message and extract amount, currency, category, and description."""
    try:
        parts = message.split()
        if len(parts) < 3:
            return None, "Message format: amount currency category [description]"
        
        amount = float(parts[0])
        currency = parts[1].upper()
        category = parts[2]
        description = ' '.join(parts[3:]) if len(parts) > 3 else ''
        
        return (amount, currency, category, description), None
    except ValueError:
        return None, "Amount must be a number"

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
    processed_data, error = process_message(message)
    
    if error:
        await update.message.reply_text(error)
        return
    
    amount, currency, category, description = processed_data
    success, error = await save_to_sheets(amount, currency, category, description)
    
    if success:
        await update.message.reply_text(
            f'✅ Saved: {amount} {currency} - {category} - {description}'
        )
    else:
        await update.message.reply_text(
            f'❌ Error saving to spreadsheet: {error}'
        )

def main():
    """Start the bot."""
    # Create the Application and pass it your bot's token
    application = Application.builder().token(os.getenv('TELEGRAM_BOT_TOKEN')).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start the Bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main() 