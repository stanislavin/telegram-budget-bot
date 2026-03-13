import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

import util.postgres as pg


# ---------- pool init ----------

@pytest.mark.asyncio
async def test_get_pool_creates_pool():
    """Test that get_pool creates and caches a pool."""
    pg._pool = None  # Force fresh creation
    mock_pool = MagicMock()
    with patch('util.postgres.asyncpg.create_pool', new_callable=AsyncMock, return_value=mock_pool):
        with patch('util.postgres.DATABASE_URL', 'postgresql://test:test@localhost/test'):
            pool = await pg.get_pool()
            assert pool is mock_pool
    pg._pool = None


@pytest.mark.asyncio
async def test_get_pool_returns_cached(mock_pg_pool):
    """Test that get_pool returns cached pool on second call."""
    pool1 = await pg.get_pool()
    pool2 = await pg.get_pool()
    assert pool1 is pool2


# ---------- _clean_dsn ----------

def test_clean_dsn_strips_channel_binding():
    """Test that channel_binding parameter is removed from DSN."""
    dsn = 'postgresql://user:pass@host/db?sslmode=require&channel_binding=require'
    assert 'channel_binding' not in pg._clean_dsn(dsn)
    assert 'sslmode=require' in pg._clean_dsn(dsn)


def test_clean_dsn_no_channel_binding():
    """Test that DSN without channel_binding is unchanged."""
    dsn = 'postgresql://user:pass@host/db?sslmode=require'
    assert pg._clean_dsn(dsn) == dsn


# ---------- save_to_postgres ----------

@pytest.mark.asyncio
async def test_save_to_postgres_success(mock_pg_pool):
    """Test successful save to Postgres."""
    pg._spending_type_column_ensured = False
    mock_pg_pool.execute = AsyncMock()

    success, error = await pg.save_to_postgres(25.50, 'USD', 'food', 'lunch')

    assert success is True
    assert error is None
    # Called twice: once for migration, once for insert
    assert mock_pg_pool.execute.await_count == 2
    insert_call = mock_pg_pool.execute.call_args_list[1]
    assert 'INSERT INTO expenses' in insert_call[0][0]
    assert insert_call[0][2] == 25.50  # amount
    assert insert_call[0][3] == 'USD'  # currency
    assert insert_call[0][4] == 'food'  # category
    assert insert_call[0][5] == 'lunch'  # description


@pytest.mark.asyncio
async def test_save_to_postgres_failure(mock_pg_pool):
    """Test save failure returns error."""
    mock_pg_pool.execute = AsyncMock(side_effect=Exception("DB error"))

    success, error = await pg.save_to_postgres(10, 'EUR', 'transport', 'bus')

    assert success is None
    assert 'DB error' in error


# ---------- delete_last_expense_pg ----------

@pytest.mark.asyncio
async def test_delete_last_expense_pg_success(mock_pg_pool):
    """Test successful delete of last expense."""
    pg._spending_type_column_ensured = False
    mock_pg_pool.execute = AsyncMock()
    mock_pg_pool.fetchrow = AsyncMock(return_value={
        'amount': 25.50,
        'currency': 'USD',
        'category': 'food',
        'description': 'lunch',
        'spending_type': 'need',
    })

    expense_info, error = await pg.delete_last_expense_pg()

    assert error is None
    assert expense_info['amount'] == '25.5'
    assert expense_info['currency'] == 'USD'
    assert expense_info['category'] == 'food'
    assert expense_info['description'] == 'lunch'
    assert expense_info['spending_type'] == 'need'


@pytest.mark.asyncio
async def test_delete_last_expense_pg_empty_table(mock_pg_pool):
    """Test delete when no expenses exist."""
    mock_pg_pool.fetchrow = AsyncMock(return_value=None)

    expense_info, error = await pg.delete_last_expense_pg()

    assert expense_info is None
    assert error == "No expenses to delete."


@pytest.mark.asyncio
async def test_delete_last_expense_pg_error(mock_pg_pool):
    """Test delete when an exception occurs."""
    mock_pg_pool.fetchrow = AsyncMock(side_effect=Exception("Connection lost"))

    expense_info, error = await pg.delete_last_expense_pg()

    assert expense_info is None
    assert 'Connection lost' in error


# ---------- get_daily_stats_pg ----------

@pytest.mark.asyncio
async def test_get_daily_stats_pg_success(mock_pg_pool):
    """Test daily stats with data."""
    mock_pg_pool.fetch = AsyncMock(side_effect=[
        [{'currency': 'USD', 'total': 50.0}, {'currency': 'EUR', 'total': 30.0}],
        [{'category': 'food', 'total': 40.0}, {'category': 'transport', 'total': 40.0}],
    ])

    currency_totals, category_totals = await pg.get_daily_stats_pg()

    assert currency_totals == {'USD': 50.0, 'EUR': 30.0}
    assert category_totals == {'food': 40.0, 'transport': 40.0}


@pytest.mark.asyncio
async def test_get_daily_stats_pg_empty(mock_pg_pool):
    """Test daily stats when no data for date."""
    mock_pg_pool.fetch = AsyncMock(return_value=[])

    currency_totals, category_totals = await pg.get_daily_stats_pg()

    assert currency_totals == {}
    assert category_totals == {}


@pytest.mark.asyncio
async def test_get_daily_stats_pg_with_datetime(mock_pg_pool):
    """Test that get_daily_stats_pg accepts a full datetime object."""
    mock_pg_pool.fetch = AsyncMock(return_value=[])
    target = datetime(2024, 6, 15, 10, 30)

    await pg.get_daily_stats_pg(target)

    # Should have converted to date and passed to SQL
    call_args = mock_pg_pool.fetch.call_args_list[0]
    assert call_args[0][1] == target.date()


# ---------- get_daily_summary_pg ----------

@pytest.mark.asyncio
async def test_get_daily_summary_pg_success(mock_pg_pool):
    """Test daily summary with data."""
    mock_pg_pool.fetch = AsyncMock(side_effect=[
        [{'currency': 'USD', 'total': 50.0}],
        [{'category': 'food', 'total': 30.0}, {'category': 'transport', 'total': 20.0}],
    ])

    summary_text, chart = await pg.get_daily_summary_pg()

    assert 'Daily Summary' in summary_text
    assert 'food: 30.00 USD' in summary_text
    assert 'transport: 20.00 USD' in summary_text
    assert '50.00 USD' in summary_text
    assert chart is None


@pytest.mark.asyncio
async def test_get_daily_summary_pg_no_data(mock_pg_pool):
    """Test daily summary when no expenses for the day."""
    mock_pg_pool.fetch = AsyncMock(return_value=[])

    today = datetime.now().date().strftime("%d/%m/%Y")
    summary_text, chart = await pg.get_daily_summary_pg()

    assert f"No expenses found for {today}" in summary_text
    assert chart is None


@pytest.mark.asyncio
async def test_get_daily_summary_pg_multi_currency(mock_pg_pool):
    """Test daily summary with multiple currencies omits inline currency."""
    mock_pg_pool.fetch = AsyncMock(side_effect=[
        [{'currency': 'USD', 'total': 30.0}, {'currency': 'EUR', 'total': 20.0}],
        [{'category': 'food', 'total': 50.0}],
    ])

    summary_text, chart = await pg.get_daily_summary_pg()

    assert 'food: 50.00\n' in summary_text
    assert '30.00 USD' in summary_text
    assert '20.00 EUR' in summary_text


@pytest.mark.asyncio
async def test_get_daily_summary_pg_error(mock_pg_pool):
    """Test daily summary on error."""
    mock_pg_pool.fetch = AsyncMock(side_effect=Exception("DB error"))

    summary_text, chart = await pg.get_daily_summary_pg()

    assert 'Error fetching daily summary' in summary_text
    assert chart is None


@pytest.mark.asyncio
async def test_get_daily_summary_pg_specific_date(mock_pg_pool):
    """Test daily summary for a specific date."""
    target = datetime(2024, 3, 15)
    mock_pg_pool.fetch = AsyncMock(side_effect=[
        [{'currency': 'EUR', 'total': 100.0}],
        [{'category': 'rent', 'total': 100.0}],
    ])

    summary_text, chart = await pg.get_daily_summary_pg(target)

    assert '15/03/2024' in summary_text
    assert 'rent: 100.00 EUR' in summary_text


# ---------- get_recent_expenses_pg ----------

@pytest.mark.asyncio
async def test_get_recent_expenses_pg_success(mock_pg_pool):
    """Test recent expenses with data."""
    now = datetime.now()
    mock_pg_pool.fetch = AsyncMock(return_value=[
        {'timestamp': now, 'amount': 25.0, 'currency': 'USD', 'category': 'food', 'description': 'lunch'},
        {'timestamp': now, 'amount': 10.0, 'currency': 'USD', 'category': 'transport', 'description': 'bus'},
    ])

    result = await pg.get_recent_expenses_pg()

    assert 'Recent Expenses (Last 2 Days)' in result
    assert '25.00 USD' in result
    assert '10.00 USD' in result
    assert '35.00 USD' in result  # total


@pytest.mark.asyncio
async def test_get_recent_expenses_pg_no_data(mock_pg_pool):
    """Test recent expenses when empty."""
    mock_pg_pool.fetch = AsyncMock(return_value=[])

    result = await pg.get_recent_expenses_pg()

    assert 'No expenses found in the last 2 days.' in result


@pytest.mark.asyncio
async def test_get_recent_expenses_pg_custom_days(mock_pg_pool):
    """Test recent expenses with custom days parameter."""
    mock_pg_pool.fetch = AsyncMock(return_value=[])

    result = await pg.get_recent_expenses_pg(days=7)

    assert 'No expenses found in the last 7 days.' in result


@pytest.mark.asyncio
async def test_get_recent_expenses_pg_error(mock_pg_pool):
    """Test recent expenses on error."""
    mock_pg_pool.fetch = AsyncMock(side_effect=Exception("Connection lost"))

    result = await pg.get_recent_expenses_pg()

    assert 'Error fetching recent expenses' in result


@pytest.mark.asyncio
async def test_get_recent_expenses_pg_multi_currency(mock_pg_pool):
    """Test recent expenses accumulates per-currency totals."""
    now = datetime.now()
    mock_pg_pool.fetch = AsyncMock(return_value=[
        {'timestamp': now, 'amount': 25.0, 'currency': 'USD', 'category': 'food', 'description': 'lunch'},
        {'timestamp': now, 'amount': 15.0, 'currency': 'EUR', 'category': 'food', 'description': 'coffee'},
    ])

    result = await pg.get_recent_expenses_pg()

    assert '- 25.00 USD' in result
    assert '- 15.00 EUR' in result


# ---------- close_pool ----------

@pytest.mark.asyncio
async def test_close_pool(mock_pg_pool):
    """Test close_pool closes the pool and resets singleton."""
    mock_pg_pool.close = AsyncMock()
    # Initialize the pool
    await pg.get_pool()
    assert pg._pool is not None

    await pg.close_pool()

    mock_pg_pool.close.assert_awaited_once()
    assert pg._pool is None


@pytest.mark.asyncio
async def test_close_pool_when_no_pool():
    """Test close_pool is safe when pool was never created."""
    pg._pool = None
    await pg.close_pool()  # should not raise
    assert pg._pool is None
