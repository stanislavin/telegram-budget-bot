import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta
from util.sheets import get_google_sheets_service, save_to_sheets, get_recent_expenses, get_daily_summary, delete_last_expense, get_daily_stats


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
    import util.sheets
    util.sheets._sheets_service = None  # Reset cache before test
    with patch('util.sheets.build') as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        yield mock_service
    util.sheets._sheets_service = None  # Reset cache after test


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
    
    assert success is None
    assert error is not None and "API Error" in error


@pytest.mark.asyncio
async def test_get_recent_expenses_success(mock_google_sheets_service):
    """Test successful retrieval of recent expenses."""
    # Use dynamic dates relative to today
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    
    mock_google_sheets_service.spreadsheets().values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],  # Header
            [today.strftime("%m/%d/%Y %H:%M:%S"), '25.50', 'food', 'lunch', '', 'USD'],
            [yesterday.strftime("%m/%d/%Y %H:%M:%S"), '10.00', 'transport', 'bus', '', 'EUR']  # Different currency to test total
        ]
    }
    
    # Execute the function
    result = await get_recent_expenses()
    
    # Verify the result
    assert "Recent Expenses (Last 2 Days)" in result
    assert "25.50 USD" in result
    assert "10.00 EUR" in result
    assert "- 25.50 USD" in result
    assert "- 10.00 EUR" in result


@pytest.mark.asyncio
async def test_get_recent_expenses_no_data(mock_sheets_service):
    """Test get_recent_expenses when no data is found."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': []}
    
    result = await get_recent_expenses(days=2)
    
    assert result == "No expenses found."


@pytest.mark.asyncio
async def test_get_recent_expenses_error(mock_google_sheets_service):
    """Test error handling in recent expenses retrieval."""
    # Mock the Google Sheets API to raise an exception
    mock_google_sheets_service.spreadsheets().values().get().execute.side_effect = Exception("Sheets API Error")
    
    # Execute the function
    result = await get_recent_expenses()
    
    # Verify the error handling
    assert "Error fetching recent expenses: Sheets API Error" in result


@pytest.mark.asyncio
async def test_get_recent_expenses_no_recent_data(mock_google_sheets_service):
    """Test handling of no recent expenses."""
    # Mock the Google Sheets API response with old data
    mock_google_sheets_service.spreadsheets().values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],  # Header
            ['01/01/2020 10:30:00', '25.50', 'food', 'lunch', '', 'USD'],  # Old data
        ]
    }
    
    # Execute the function
    result = await get_recent_expenses()
    
    # Verify the result
    assert "No expenses found in the last 2 days." in result


def test_get_google_sheets_service_success(mock_service_account):
    """Test successful Google Sheets service initialization."""
    import util.sheets
    util.sheets._sheets_service = None  # Reset cache
    with patch('util.sheets.build') as mock_build:
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        
        service = get_google_sheets_service()
        
        assert service == mock_service
        mock_build.assert_called_once_with('sheets', 'v4', credentials=mock_service_account)
    util.sheets._sheets_service = None  # Clean up


def test_get_google_sheets_service_failure():
    """Test Google Sheets service initialization failure."""
    import util.sheets
    util.sheets._sheets_service = None  # Reset cache to force re-initialization
    with patch('util.sheets.service_account.Credentials.from_service_account_file') as mock_creds:
        mock_creds.side_effect = Exception("Credential Error")
        
        with pytest.raises(Exception, match="Credential Error"):
            get_google_sheets_service()
    util.sheets._sheets_service = None  # Clean up


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
    assert "Total spent:" in summary_text
    assert "- 50.50 USD" in summary_text
    assert "dinner" not in summary_text  # Yesterday's expense should not appear
    assert chart_path is None  # Chart should not be generated


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
    assert "Total spent:" in summary_text
    assert "- 30.00 EUR" in summary_text
    assert "transport" not in summary_text  # Previous day's expense should not appear
    assert chart_path is None  # Chart should not be generated


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
    assert chart_path is None  # Chart should not be generated


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

    today_formatted = datetime.now().strftime("%d/%m/%Y")
    assert summary_text == f"No expenses found for {today_formatted}."
    assert chart_path is None  # No chart when no data


# ---------- delete_last_expense tests ----------

@pytest.mark.asyncio
async def test_delete_last_expense_success(mock_sheets_service):
    """Test successful deletion of last expense (covers lines 78-142)."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            ['2024-01-15 10:00:00', '25.50', 'food', 'lunch', '', 'USD'],
        ]
    }
    mock_sheet.get().execute.return_value = {
        'sheets': [{'properties': {'title': 'Form Responses 1', 'sheetId': 0}}]
    }
    mock_sheet.batchUpdate().execute.return_value = {}

    expense_info, error = await delete_last_expense()

    assert error is None
    assert expense_info is not None
    assert expense_info['amount'] == '25.50'
    assert expense_info['currency'] == 'USD'
    assert expense_info['category'] == 'food'
    assert expense_info['description'] == 'lunch'


@pytest.mark.asyncio
async def test_delete_last_expense_no_expenses(mock_sheets_service):
    """Test delete when sheet has only a header row (covers line 90)."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {
        'values': [['timestamp', 'amount', 'category', 'description', '', 'currency']]
    }

    expense_info, error = await delete_last_expense()

    assert expense_info is None
    assert error == "No expenses to delete."


@pytest.mark.asyncio
async def test_delete_last_expense_empty_sheet(mock_sheets_service):
    """Test delete when sheet has no data at all."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {'values': []}

    expense_info, error = await delete_last_expense()

    assert expense_info is None
    assert error == "No expenses to delete."


@pytest.mark.asyncio
async def test_delete_last_expense_sheet_not_found(mock_sheets_service):
    """Test delete when target sheet name is not found (covers lines 116-117)."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            ['2024-01-15 10:00:00', '25.50', 'food', 'lunch', '', 'USD'],
        ]
    }
    mock_sheet.get().execute.return_value = {
        'sheets': [{'properties': {'title': 'WrongSheet', 'sheetId': 1}}]
    }

    expense_info, error = await delete_last_expense()

    assert expense_info is None
    assert error is not None and isinstance(error, str) and "not found" in error


@pytest.mark.asyncio
async def test_delete_last_expense_exception(mock_sheets_service):
    """Test delete when API call raises an exception (covers lines 144-146)."""
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.side_effect = Exception("API failure")

    expense_info, error = await delete_last_expense()

    assert expense_info is None
    assert error is not None and isinstance(error, str) and "API failure" in error


# ---------- get_daily_stats edge case tests ----------

@pytest.mark.asyncio
async def test_get_daily_stats_with_datetime_object(mock_sheets_service):
    """Test get_daily_stats accepts a full datetime object (covers line 156)."""
    import util.sheets
    util.sheets._daily_stats_cache = {}

    today = datetime.now()
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            [today.strftime("%Y-%m-%d %H:%M:%S"), '50.0', 'Food', 'lunch', '', 'USD'],
        ]
    }

    currency_totals, category_totals = await get_daily_stats(today)

    assert 'USD' in currency_totals
    assert 'Food' in category_totals


@pytest.mark.asyncio
async def test_get_daily_stats_cache_hit(mock_sheets_service):
    """Test that get_daily_stats returns cached result (covers line 161)."""
    import util.sheets
    import time as _time

    target_date = datetime.now().date()
    cache_key = target_date.strftime('%Y-%m-%d')
    expected_result = ({'USD': 100.0}, {'food': 100.0})

    util.sheets._daily_stats_cache = {cache_key: expected_result}
    util.sheets._daily_stats_cache_time = _time.monotonic()

    result = await get_daily_stats(target_date)  # type: ignore[arg-type]

    assert result == expected_result
    mock_sheets_service.spreadsheets().values().get.assert_not_called()


@pytest.mark.asyncio
async def test_get_daily_stats_invalid_timestamp(mock_sheets_service):
    """Test that rows with unparseable timestamps are skipped (covers line 199)."""
    import util.sheets
    util.sheets._daily_stats_cache = {}

    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            ['not-a-date', '50.0', 'Food', 'lunch', '', 'USD'],
            ['', '20.0', 'Transport', 'bus', '', 'EUR'],
        ]
    }

    currency_totals, category_totals = await get_daily_stats()

    assert currency_totals == {}
    assert category_totals == {}


@pytest.mark.asyncio
async def test_get_daily_stats_row_parse_error(mock_sheets_service):
    """Test that rows causing ValueError are skipped (covers lines 219-220)."""
    import util.sheets
    util.sheets._daily_stats_cache = {}

    today = datetime.now()
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            [today.strftime("%Y-%m-%d %H:%M:%S"), 'not-a-number', 'Food', 'lunch', '', 'USD'],
        ]
    }

    currency_totals, category_totals = await get_daily_stats()

    assert currency_totals == {}
    assert category_totals == {}


# ---------- get_recent_expenses edge case tests ----------

@pytest.mark.asyncio
async def test_get_recent_expenses_invalid_timestamp(mock_google_sheets_service):
    """Test that rows with unparseable timestamps are skipped (covers line 316)."""
    mock_google_sheets_service.spreadsheets().values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            ['not-a-date', '25.50', 'food', 'lunch', '', 'USD'],
        ]
    }

    result = await get_recent_expenses()

    assert "No expenses found in the last 2 days." in result


@pytest.mark.asyncio
async def test_get_recent_expenses_same_currency_accumulates(mock_google_sheets_service):
    """Test that multiple expenses in the same currency accumulate total (covers line 331)."""
    today = datetime.now()

    mock_google_sheets_service.spreadsheets().values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            [today.strftime("%m/%d/%Y %H:%M:%S"), '10.00', 'food', 'lunch', '', 'USD'],
            [today.strftime("%m/%d/%Y %H:%M:%S"), '15.00', 'food', 'coffee', '', 'USD'],
        ]
    }

    result = await get_recent_expenses()

    assert "Recent Expenses (Last 2 Days)" in result
    assert "- 25.00 USD" in result  # Accumulated total


@pytest.mark.asyncio
async def test_get_recent_expenses_invalid_amount(mock_google_sheets_service):
    """Test that rows with invalid amount values are skipped (covers lines 335-337)."""
    today = datetime.now()

    mock_google_sheets_service.spreadsheets().values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            [today.strftime("%m/%d/%Y %H:%M:%S"), 'not-a-number', 'food', 'lunch', '', 'USD'],
        ]
    }

    result = await get_recent_expenses()

    assert "No expenses found in the last 2 days." in result


# ---------- get_daily_summary multi-currency test ----------

@pytest.mark.asyncio
async def test_get_daily_summary_multi_currency(mock_sheets_service):
    """Test daily summary with multiple currencies omits inline currency (covers line 259)."""
    import util.sheets
    util.sheets._daily_stats_cache = {}

    today = datetime.now()
    mock_sheet = mock_sheets_service.spreadsheets()
    mock_sheet.values().get().execute.return_value = {
        'values': [
            ['timestamp', 'amount', 'category', 'description', '', 'currency'],
            [today.strftime("%Y-%m-%d %H:%M:%S"), '25.00', 'Food', 'lunch', '', 'USD'],
            [today.strftime("%Y-%m-%d %H:%M:%S"), '15.00', 'Food', 'coffee', '', 'EUR'],
        ]
    }

    summary_text, chart_path = await get_daily_summary()

    assert 'Daily Summary' in summary_text
    # With multiple currencies the format omits inline currency (line 259)
    assert '📊 Food: 40.00\n' in summary_text
    assert chart_path is None