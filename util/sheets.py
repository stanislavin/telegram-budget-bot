import logging
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

from util.config import GOOGLE_CREDENTIALS_PATH, GOOGLE_SHEET_ID, SHEET_NAME, RANGE_NAME, GOOGLE_SCOPES

logger = logging.getLogger(__name__)

def get_google_sheets_service():
    """Initialize and return Google Sheets service."""
    
    try:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_PATH, scopes=GOOGLE_SCOPES)
        service = build('sheets', 'v4', credentials=creds)
        return service
    except Exception as e:
        logger.error(f"Failed to initialize Google Sheets service: {str(e)}")
        raise

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
            "",           # Column E - empty
            currency      # Column F - currency
        ]
        
        body = {
            'values': [row]
        }
        
        # Append the row to the sheet
        result = sheet.values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=RANGE_NAME,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body=body
        ).execute()
        
        return True, None
    except Exception as e:
        return False, str(e)

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
            spreadsheetId=GOOGLE_SHEET_ID,
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