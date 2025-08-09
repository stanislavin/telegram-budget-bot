import logging
import os
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
import matplotlib.pyplot as plt

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

async def get_daily_summary(target_date: datetime = None):
    """Get daily spending summary by category for a specific date."""
    try:
        service = get_google_sheets_service()
        sheet = service.spreadsheets()
        
        # Use today if no date provided
        if target_date is None:
            target_date = datetime.now().date()
        elif hasattr(target_date, 'date'):
            target_date = target_date.date()
        
        logger.info(f"Fetching daily summary for {target_date.strftime('%Y-%m-%d')}")
        
        # Get all data from the sheet
        result = sheet.values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=RANGE_NAME
        ).execute()
        
        values = result.get('values', [])
        if not values:
            return "No expenses found.", None
        
        # Group expenses by category for the target date
        category_totals = {}
        total_spent = 0
        currency = None
        
        for row in values[1:]:  # Skip header row
            if len(row) >= 6:  # Ensure row has all required columns
                try:
                    # Parse the timestamp from the sheet
                    timestamp_str = row[0]  # Column A
                    timestamp = datetime.strptime(timestamp_str, "%m/%d/%Y %H:%M:%S")
                    
                    # Check if the expense is from the target date
                    if timestamp.date() == target_date:
                        amount = float(row[1])  # Column B
                        category = row[2]  # Column C
                        currency = row[5]  # Column F
                        
                        # Add to category total
                        if category in category_totals:
                            category_totals[category] += amount
                        else:
                            category_totals[category] = amount
                        
                        total_spent += amount
                except (ValueError, IndexError) as e:
                    logger.error(f"Error parsing row: {row}, error: {str(e)}")
                    continue
        
        if not category_totals:
            formatted_date = target_date.strftime("%d/%m/%Y")
            return f"No expenses found for {formatted_date}.", None
        
        # Format the message
        formatted_date = target_date.strftime("%d/%m/%Y")
        message = f"💰 Daily Summary for {formatted_date}:\n\n"
        
        # Sort categories by amount (highest first)
        sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
        
        for category, amount in sorted_categories:
            message += f"📊 {category}: {amount:.2f} {currency}\n"
        
        message += f"\n💸 Total spent: {total_spent:.2f} {currency}"
        
        # Generate pie chart
        chart_path = generate_pie_chart(category_totals, currency, formatted_date)
        
        return message, chart_path
        
    except Exception as e:
        logger.error(f"Error fetching daily summary: {str(e)}")
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
                        recent_expenses.append((timestamp, line))
                        total_amount += amount
                except (ValueError, IndexError) as e:
                    logger.error(f"Error parsing row: {row}, error: {str(e)}")
                    continue
        
        if not recent_expenses:
            return f"No expenses found for the last {days} days."
        
        # Sort expenses by date in ascending order (oldest first)
        recent_expenses.sort(key=lambda x: x[0])
        
        # Format the message
        message = f"Expenses for the last {days} days:\n\n"
        message += "\n".join(line for _, line in recent_expenses)
        
        return message
        
    except Exception as e:
        logger.error(f"Error fetching recent expenses: {str(e)}")
        return f"Error fetching expenses: {str(e)}" 