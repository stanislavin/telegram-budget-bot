import logging
import os
import asyncio
from datetime import datetime, timedelta

import matplotlib
matplotlib.use("Agg")  # Use headless backend to avoid GUI requirements during tests/runs.
import matplotlib.pyplot as plt
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


async def save_to_sheets_with_retry(amount: float, currency: str, category: str, description: str):
    """Save the expense to Google Sheets with retry logic."""
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
        logger.warning(f"Google Sheets API request failed: {str(e)}, retrying in 10 seconds...")

        # Wait 10 seconds before retry
        await asyncio.sleep(10)

        try:
            # Retry attempt
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

        except Exception as retry_e:
            return False, f"Error saving to Google Sheets after retry: {str(retry_e)}"

def generate_pie_chart(category_totals, currency, date_str):
    """Generate a pie chart for category spending and save as an image."""
    # Create figure and axis
    fig, ax = plt.subplots(figsize=(10, 8))
    
    # Sort categories by amount (descending)
    sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    labels = [item[0] for item in sorted_categories]
    sizes = [item[1] for item in sorted_categories]
    
    # Create custom labels with both amount and percentage
    total = sum(sizes)
    custom_labels = [f'{label}\n{size:.2f} {currency}\n({size/total*100:.1f}%)' 
                     for label, size in zip(labels, sizes)]
    
    # Create pie chart with custom labels
    wedges, _, autotexts = ax.pie(sizes, labels=custom_labels, autopct='', startangle=90)
    
    # Format the appearance
    ax.axis('equal')  # Equal aspect ratio ensures that pie is drawn as a circle
    ax.set_title(f'Daily Spending by Category - {date_str}', fontsize=16, pad=20)
    
    # Adjust label styling
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontsize(9)
        autotext.set_weight('bold')
    
    # Save the chart as an image
    chart_path = f'temp_pie_chart_{date_str.replace("/", "_")}.png'
    plt.savefig(chart_path, bbox_inches='tight', dpi=300)
    plt.close(fig)  # Close the figure to free memory
    
    return chart_path

async def get_daily_stats(target_date: datetime = None):
    """Get daily spending statistics for a specific date."""
    try:
        service = get_google_sheets_service()
        sheet = service.spreadsheets()
        
        # Use today if no date provided
        if target_date is None:
            target_date = datetime.now().date()
        elif hasattr(target_date, 'date'):
            target_date = target_date.date()
        
        logger.info(f"Fetching daily stats for {target_date.strftime('%Y-%m-%d')}")
        
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
                    if timestamp.date() == target_date:
                        amount = float(row[1])  # Column B
                        category = row[2]  # Column C
                        currency = row[5]  # Column F
                        
                        # Add to category total
                        # We need to track category totals per currency for the pie chart if we want to be precise,
                        # but for now the pie chart logic assumes one currency or mixes them.
                        # Let's keep category_totals simple for now (summing amounts regardless of currency for the chart size)
                        # or better, let's just track amounts.
                        # The original code summed up amounts for category_totals.
                        
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
        
        return currency_totals, category_totals
        
    except Exception as e:
        logger.error(f"Error fetching daily stats: {str(e)}")
        raise

async def get_daily_summary(target_date: datetime = None):
    """Get daily spending summary by category for a specific date."""
    try:
        # Use today if no date provided
        if target_date is None:
            target_date = datetime.now().date()
        elif hasattr(target_date, 'date'):
            target_date = target_date.date()
            
        currency_totals, category_totals = await get_daily_stats(target_date)
        
        formatted_date = target_date.strftime("%d/%m/%Y")
        
        if not category_totals:
            logger.info(f"No expenses found for {formatted_date}")
            return f"No expenses found for {formatted_date}.", None
        
        # Format the message
        message = f"💰 Daily Summary for {formatted_date}:\n\n"
        
        # Sort categories by amount (highest first)
        sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
        
        # Note: The category breakdown currently doesn't show currency if mixed.
        # This is a limitation of the current display logic which we might want to address later.
        # For now, we'll just list the categories and their values.
        # To make it better, we should probably list categories with their dominant currency or just the value.
        # However, the prompt asked for "totals per currency".
        
        for category, amount in sorted_categories:
             # We don't have per-category currency here easily without more refactoring.
             # Let's just show the amount and maybe a generic label or just the amount.
             # The previous code used a single 'currency' variable which was just the LAST currency seen.
             # Let's try to infer the currency if there is only one, otherwise maybe omit it or show 'mixed'?
             
             if len(currency_totals) == 1:
                 currency = list(currency_totals.keys())[0]
                 message += f"📊 {category}: {amount:.2f} {currency}\n"
             else:
                 # If mixed currencies, just show the amount (it's a sum of mixed units, which is weird but existing behavior for categories)
                 message += f"📊 {category}: {amount:.2f}\n"
        
        message += "\n💸 Total spent:\n"
        for currency, total in currency_totals.items():
            message += f"- {total:.2f} {currency}\n"
        
        logger.info(f"Generated summary message: {message[:100]}...")
        
        # Generate pie chart
        # For the pie chart, we'll pass the primary currency if only one, else 'Mixed'
        chart_currency = list(currency_totals.keys())[0] if len(currency_totals) == 1 else "Mixed"
        chart_path = generate_pie_chart(category_totals, chart_currency, formatted_date)
        logger.info(f"Generated chart at: {chart_path}")
        
        return message, chart_path
        
    except Exception as e:
        logger.error(f"Error fetching daily summary: {str(e)}", exc_info=True)
        return f"Error fetching daily summary: {str(e)}", None

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
                        category = row[2]  # Column C
                        description = row[3]  # Column D
                        currency = row[5]  # Column F
                        
                        # Format the expense entry
                        formatted_timestamp = timestamp.strftime("%d/%m/%Y %H:%M")
                        recent_expenses.append(f"{formatted_timestamp} | {amount:.2f} {currency} | {category} | {description}")
                        total_amount += amount
                        
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
        
        message += f"\n💸 Total: {total_amount:.2f} {currency}"
        
        return message
        
    except Exception as e:
        logger.error(f"Error fetching recent expenses: {str(e)}")
        return f"Error fetching recent expenses: {str(e)}"
