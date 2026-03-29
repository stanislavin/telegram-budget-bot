import logging
import os
import asyncio
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build

from util.config import GOOGLE_CREDENTIALS_PATH, GOOGLE_SHEET_ID, SHEET_NAME, RANGE_NAME, GOOGLE_SCOPES
from util.retry_handler import with_retry

logger = logging.getLogger(__name__)

_sheets_service = None
_daily_stats_cache = {}
_daily_stats_cache_time = 0
_DAILY_STATS_TTL = 30  # seconds

def get_google_sheets_service():
    """Initialize and return Google Sheets service (cached singleton)."""
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service

    try:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_CREDENTIALS_PATH, scopes=GOOGLE_SCOPES)
        _sheets_service = build('sheets', 'v4', credentials=creds)
        return _sheets_service
    except Exception as e:
        logger.error(f"Failed to initialize Google Sheets service: {str(e)}")
        raise

@with_retry(max_retries=1, error_message="Error saving to Google Sheets")
async def save_to_sheets(amount: float, currency: str, category: str, description: str, spending_type: str = None):
    """Save the expense to Google Sheets."""
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
        spending_type or "",  # Column E - spending type (need/want/wellbeing)
        currency      # Column F - currency
    ]

    body = {
        'values': [row]
    }

    # Append the row to the sheet
    sheet.values().append(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=RANGE_NAME,
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body=body
    ).execute()

    return True


async def delete_last_expense():
    """Delete the last expense (bottom row) from the sheet.

    Returns:
        (dict, None) on success — dict has amount, currency, category, description.
        (None, str)  on failure — str is the error message.
    """
    global _daily_stats_cache, _daily_stats_cache_time

    try:
        service = get_google_sheets_service()
        sheet = service.spreadsheets()

        # Fetch all values to find the last row
        result = sheet.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=RANGE_NAME
        ).execute()

        values = result.get('values', [])
        if len(values) <= 1:
            return None, "No expenses to delete."

        last_row = values[-1]
        # Row index in the sheet (0-based); header is row 0 in the values list
        sheet_row_index = len(values)  # 1-based row number (header = row 1)

        # Parse the row for the response message
        expense_info = {
            'amount': last_row[1] if len(last_row) > 1 else '?',
            'currency': last_row[5] if len(last_row) > 5 else '?',
            'category': last_row[2] if len(last_row) > 2 else '?',
            'description': last_row[3] if len(last_row) > 3 else '?',
            'spending_type': last_row[4] if len(last_row) > 4 else None,
        }

        # Get the sheet's internal ID (gid) for the deleteDimension request
        spreadsheet_meta = service.spreadsheets().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            fields='sheets.properties'
        ).execute()

        sheet_id = None
        for s in spreadsheet_meta.get('sheets', []):
            if s['properties']['title'] == SHEET_NAME:
                sheet_id = s['properties']['sheetId']
                break

        if sheet_id is None:
            return None, f"Sheet '{SHEET_NAME}' not found."

        # Delete the last row
        requests_body = {
            'requests': [{
                'deleteDimension': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': sheet_row_index - 1,  # 0-based
                        'endIndex': sheet_row_index,          # exclusive
                    }
                }
            }]
        }

        service.spreadsheets().batchUpdate(
            spreadsheetId=GOOGLE_SHEET_ID,
            body=requests_body
        ).execute()

        # Invalidate daily stats cache
        _daily_stats_cache = {}
        _daily_stats_cache_time = 0

        return expense_info, None

    except Exception as e:
        logger.error(f"Error deleting last expense: {str(e)}")
        return None, str(e)

async def get_daily_stats(target_date: datetime | None = None) -> tuple[dict, dict]:
    """Get daily spending statistics for a specific date (with short TTL cache)."""
    import time as _time
    global _daily_stats_cache, _daily_stats_cache_time

    if target_date is None:
        target_date = datetime.now().date()  # type: ignore[assignment]
    elif hasattr(target_date, 'date'):
        target_date = target_date.date()  # type: ignore[assignment]

    cache_key = target_date.strftime('%Y-%m-%d')  # type: ignore[union-attr]
    now = _time.monotonic()
    if cache_key in _daily_stats_cache and (now - _daily_stats_cache_time) < _DAILY_STATS_TTL:
        return _daily_stats_cache[cache_key]

    try:
        service = get_google_sheets_service()
        sheet = service.spreadsheets()
        
        logger.info(f"Fetching daily stats for {cache_key}")
        
        # Get all data from the sheet
        result = sheet.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            return {}, {}
        
        # Group expenses by category for the target date
        category_totals = {}
        currency_totals = {}
        
        for i, row in enumerate(values[1:]):  # Skip header row
            if len(row) >= 6:  # Ensure row has all required columns
                try:
                    # Parse the timestamp from the sheet
                    timestamp_str = row[0]  # Column A
                    
                    # Try multiple date formats
                    timestamp = None
                    for fmt in ["%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"]:
                        try:
                            timestamp = datetime.strptime(timestamp_str, fmt)
                            break
                        except ValueError:
                            continue
                    
                    if timestamp is None:
                        continue
                    
                    # Check if the expense is from the target date
                    if timestamp.date() == target_date:  # type: ignore[union-attr]
                        amount = float(row[1])  # Column B
                        category = row[2]  # Column C
                        currency = row[5]  # Column F
                        
                        # Add to category total
                        if category in category_totals:
                            category_totals[category] += amount
                        else:
                            category_totals[category] = amount
                        
                        # Add to currency total
                        if currency in currency_totals:
                            currency_totals[currency] += amount
                        else:
                            currency_totals[currency] = amount
                        
                except (ValueError, IndexError):
                    continue
        
        result_data = (currency_totals, category_totals)
        _daily_stats_cache[cache_key] = result_data
        _daily_stats_cache_time = now
        return result_data
        
    except Exception as e:
        logger.error(f"Error fetching daily stats: {str(e)}")
        raise

async def get_daily_summary(target_date: datetime | None = None) -> tuple[str, None]:
    """Get daily spending summary by category for a specific date."""
    try:
        # Use today if no date provided
        if target_date is None:
            target_date = datetime.now().date()  # type: ignore[assignment]
        elif hasattr(target_date, 'date'):
            target_date = target_date.date()  # type: ignore[assignment]
            
        currency_totals, category_totals = await get_daily_stats(target_date)
        
        formatted_date = target_date.strftime("%d/%m/%Y")  # type: ignore[union-attr]
        
        if not category_totals:
            logger.info(f"No expenses found for {formatted_date}")
            return f"No expenses found for {formatted_date}.", None
        
        # Format the message
        message = f"💰 Daily Summary for {formatted_date}:\n\n"
        
        # Sort categories by amount (highest first)
        sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
        
        for category, amount in sorted_categories:
             if len(currency_totals) == 1:
                 currency = list(currency_totals.keys())[0]
                 message += f"📊 {category}: {amount:.2f} {currency}\n"
             else:
                 message += f"📊 {category}: {amount:.2f}\n"
        
        message += "\n💸 Total spent:\n"
        for currency, total in currency_totals.items():
            message += f"- {total:.2f} {currency}\n"
        
        logger.info(f"Generated summary message: {message[:100]}...")
        
        return message, None
        
    except Exception as e:
        logger.error(f"Error fetching daily summary: {str(e)}", exc_info=True)
        return f"Error fetching daily summary: {str(e)}", None

async def get_recent_expenses(days: int = 2, category: str | None = None, spending_type: str | None = None) -> str:
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
        currency_totals = {}
        
        for row in values[1:]:  # Skip header row
            if len(row) >= 6:  # Ensure row has all required columns
                try:
                    # Parse timestamp and amount
                    timestamp_str = row[0]  # Column A
                    
                    # Try multiple date formats
                    timestamp = None
                    for fmt in ["%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"]:
                        try:
                            timestamp = datetime.strptime(timestamp_str, fmt)
                            break
                        except ValueError:
                            continue
                    
                    if timestamp is None:
                        continue
                    
                    # Check if the expense is within the last N days
                    if start_date <= timestamp.date() <= today:
                        amount = float(row[1])  # Column B
                        row_category = row[2]  # Column C
                        description = row[3]  # Column D
                        currency = row[5]  # Column F

                        if category and row_category != category:
                            continue

                        # Format the expense entry
                        formatted_timestamp = timestamp.strftime("%d/%m/%Y %H:%M")
                        recent_expenses.append(f"{formatted_timestamp} | {amount:.2f} {currency} | {row_category} | {description}")
                        
                        # Track per-currency totals
                        if currency in currency_totals:
                            currency_totals[currency] += amount
                        else:
                            currency_totals[currency] = amount
                        
                except (ValueError, IndexError):
                    # Skip rows with parsing errors
                    continue
        
        if not recent_expenses:
            return f"No expenses found in the last {days} days."
        
        # Format the message
        message = f"📅 Recent Expenses (Last {days} Days):\n\n"
        message += "Date/Time | Amount | Category | Description\n"
        message += "-" * 50 + "\n"
        
        # Sort by timestamp (newest first)
        recent_expenses.sort(reverse=True)
        
        for expense in recent_expenses:
            message += expense + "\n"
        
        message += "\n💸 Total:\n"
        for currency, total in currency_totals.items():
            message += f"- {total:.2f} {currency}\n"
        
        return message
        
    except Exception as e:
        logger.error(f"Error fetching recent expenses: {str(e)}")
        return f"Error fetching recent expenses: {str(e)}"
