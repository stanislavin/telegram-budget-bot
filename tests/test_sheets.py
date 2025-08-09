import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta
from util.sheets import get_google_sheets_service, save_to_sheets, get_recent_expenses, get_daily_summary


@pytest.fixture
def mock_service_account():
    """Mock service account credentials."""
    with patch('util.sheets.service_account.Credentials.from_service_account_file') as mock_creds:
        mock_credentials = MagicMock()
        mock_creds.return_value = mock_credentials
        yield mock_credentials


@pytest.fixture
def mock_sheets_service(mock_service_account):
    """Mock Google Sheets service."""
    with patch('util.sheets.build') as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        yield mock_service


@pytest.mark.asyncio
async def test_save_to_sheets_success(mock_sheets_service):
    """Test successful saving to Google Sheets."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_append = mock_sheet.values.return_value.append
    mock_append.return_value.execute.return_value = {'updates': {'updatedCells': 6}}
    
    success, error = await save_to_sheets(25.50, 'USD', 'food', 'groceries')
    
    assert success is True
    assert error is None
    mock_append.assert_called_once()


@pytest.mark.asyncio
async def test_save_to_sheets_failure(mock_sheets_service):
    """Test failed saving to Google Sheets."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().append().execute.side_effect = Exception("API Error")
    
    success, error = await save_to_sheets(25.50, 'USD', 'food', 'groceries')
    
    assert success is False
    assert "API Error" in error


@pytest.mark.asyncio
async def test_get_recent_expenses_success(mock_sheets_service):
    """Test successful retrieval of recent expenses."""
    # Mock response data
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    
    mock_data = [
        ['timestamp', 'amount', 'category', 'description', '', 'currency'],  # Header
        [today.strftime("%m/%d/%Y %H:%M:%S"), '25.50', 'food', 'lunch', '', 'USD'],
        [yesterday.strftime("%m/%d/%Y %H:%M:%S"), '10.00', 'transport', 'bus', '', 'EUR']
    ]
    
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': mock_data}
    
    result = await get_recent_expenses(days=2)
    
    assert "Expenses for the last 2 days:" in result
    assert "lunch" in result
    assert "bus" in result


@pytest.mark.asyncio
async def test_get_recent_expenses_no_data(mock_sheets_service):
    """Test get_recent_expenses when no data is found."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': []}
    
    result = await get_recent_expenses(days=2)
    
    assert result == "No expenses found."


@pytest.mark.asyncio
async def test_get_recent_expenses_error(mock_sheets_service):
    """Test get_recent_expenses when an error occurs."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.side_effect = Exception("Sheets API Error")
    
    result = await get_recent_expenses(days=2)
    
    assert "Error fetching expenses: Sheets API Error" in result


@pytest.mark.asyncio
async def test_get_recent_expenses_no_recent_data(mock_sheets_service):
    """Test get_recent_expenses when data exists but none is recent."""
    # Mock data from 5 days ago
    old_date = (datetime.now() - timedelta(days=5)).strftime("%m/%d/%Y %H:%M:%S")
    
    mock_data = [
        ['timestamp', 'amount', 'category', 'description', '', 'currency'],  # Header
        [old_date, '25.50', 'food', 'old lunch', '', 'USD']
    ]
    
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': mock_data}
    
    result = await get_recent_expenses(days=2)
    
    assert "No expenses found for the last 2 days." in result


def test_get_google_sheets_service_success(mock_service_account):
    """Test successful Google Sheets service initialization."""
    with patch('util.sheets.build') as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        service = get_google_sheets_service()
        
        assert service == mock_service
        mock_build.assert_called_once_with('sheets', 'v4', credentials=mock_service_account)


def test_get_google_sheets_service_failure():
    """Test Google Sheets service initialization failure."""
    with patch('util.sheets.service_account.Credentials.from_service_account_file') as mock_creds:
        mock_creds.side_effect = Exception("Credential Error")
        
        with pytest.raises(Exception, match="Credential Error"):
            get_google_sheets_service()


@pytest.mark.asyncio
async def test_save_to_sheets_data_format(mock_sheets_service):
    """Test that save_to_sheets formats data correctly."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().append().execute.return_value = {'updates': {'updatedCells': 6}}
    
    await save_to_sheets(100.0, 'EUR', 'transport', 'taxi ride')
    
    # Verify the call was made with correct data structure
    call_args = mock_sheet.values().append.call_args
    body = call_args[1]['body']
    row_data = body['values'][0]
    
    # Check the row structure: [timestamp, amount, category, description, "", currency]
    assert len(row_data) == 6
    assert row_data[1] == 100.0  # amount
    assert row_data[2] == 'transport'  # category
    assert row_data[3] == 'taxi ride'  # description
    assert row_data[4] == ""  # empty column
    assert row_data[5] == 'EUR'  # currency
    # timestamp should be in correct format
    assert isinstance(row_data[0], str)
    datetime.strptime(row_data[0], "%Y-%m-%d %H:%M:%S")  # Should not raise exception


@pytest.mark.asyncio
async def test_get_daily_summary_success(mock_sheets_service):
    """Test successful daily summary generation."""
    today = datetime.now()
    
    mock_data = [
        ['timestamp', 'amount', 'category', 'description', '', 'currency'],  # Header
        [today.strftime("%m/%d/%Y %H:%M:%S"), '25.50', 'food', 'lunch', '', 'USD'],
        [today.strftime("%m/%d/%Y %H:%M:%S"), '15.00', 'food', 'coffee', '', 'USD'],
        [today.strftime("%m/%d/%Y %H:%M:%S"), '10.00', 'transport', 'bus', '', 'USD'],
        # Yesterday's expense (should not be included)
        [(today - timedelta(days=1)).strftime("%m/%d/%Y %H:%M:%S"), '50.00', 'food', 'dinner', '', 'USD']
    ]
    
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': mock_data}
    
    result = await get_daily_summary()
    summary_text, chart_path = result
    
    assert "Daily Summary" in summary_text
    assert "food: 40.50 USD" in summary_text  # 25.50 + 15.00
    assert "transport: 10.00 USD" in summary_text
    assert "Total spent: 50.50 USD" in summary_text
    assert "dinner" not in summary_text  # Yesterday's expense should not appear
    assert chart_path is not None  # Chart should be generated


@pytest.mark.asyncio
async def test_get_daily_summary_no_expenses_today(mock_sheets_service):
    """Test daily summary when no expenses for today."""
    yesterday = datetime.now() - timedelta(days=1)
    mock_data = [
        ['Timestamp', 'Amount', 'Category', 'Description', '', 'Currency'],
        [yesterday.strftime("%m/%d/%Y %H:%M:%S"), '50.00', 'food', 'dinner', '', 'USD']
    ]
    
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': mock_data}
    
    # Get today's date in the format used by the function
    today = datetime.now().date()
    today_formatted = today.strftime("%d/%m/%Y")
    
    result = await get_daily_summary()
    summary_text, chart_path = result
    
    assert f"No expenses found for {today_formatted}" in summary_text
    assert chart_path is None  # No chart when no expenses


@pytest.mark.asyncio
async def test_get_daily_summary_specific_date(mock_sheets_service):
    """Test daily summary for a specific date."""
    target_date = datetime(2024, 1, 15)
    target_date_formatted = target_date.strftime("%d/%m/%Y")
    
    mock_data = [
        ['Timestamp', 'Amount', 'Category', 'Description', '', 'Currency'],
        ['01/15/2024 10:30:00', '30.00', 'food', 'lunch', '', 'EUR'],
        ['01/14/2024 10:30:00', '20.00', 'transport', 'bus', '', 'EUR']  # Previous day (should not appear)
    ]
    
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': mock_data}
    
    result = await get_daily_summary(target_date)
    summary_text, chart_path = result
    
    assert f"Daily Summary for {target_date_formatted}" in summary_text
    assert "food: 30.00 EUR" in summary_text
    assert "Total spent: 30.00 EUR" in summary_text
    assert "transport" not in summary_text  # Previous day's expense should not appear
    assert chart_path is not None  # Chart should be generated


@pytest.mark.asyncio
async def test_get_daily_summary_sorted_by_amount(mock_sheets_service):
    """Test that daily summary sorts categories by amount (highest first)."""
    today = datetime.now()
    
    mock_data = [
        ['timestamp', 'amount', 'category', 'description', '', 'currency'],  # Header
        [today.strftime("%m/%d/%Y %H:%M:%S"), '5.00', 'transport', 'bus', '', 'USD'],
        [today.strftime("%m/%d/%Y %H:%M:%S"), '25.00', 'food', 'lunch', '', 'USD'],
        [today.strftime("%m/%d/%Y %H:%M:%S"), '15.00', 'entertainment', 'movie', '', 'USD'],
    ]
    
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': mock_data}
    
    summary_text, chart_path = await get_daily_summary()
    
    # Check that categories are ordered by amount (highest first)
    lines = summary_text.split('\n')
    category_lines = [line for line in lines if '📊' in line]
    
    assert len(category_lines) == 3
    assert 'food: 25.00' in category_lines[0]  # Highest amount first
    assert 'entertainment: 15.00' in category_lines[1]  # Second highest
    assert 'transport: 5.00' in category_lines[2]  # Lowest amount last
    assert chart_path is not None  # Chart should be generated


@pytest.mark.asyncio
async def test_get_daily_summary_error(mock_sheets_service):
    """Test daily summary when an error occurs."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.side_effect = Exception("Sheets API Error")
    
    result = await get_daily_summary()
    
    assert "Error fetching daily summary: Sheets API Error" in result


@pytest.mark.asyncio
async def test_get_daily_summary_no_data(mock_sheets_service):
    """Test daily summary when sheet has no data."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': []}
    
    result = await get_daily_summary()
    summary_text, chart_path = result
    
    assert summary_text == "No expenses found."
    assert chart_path is None  # No chart when no data