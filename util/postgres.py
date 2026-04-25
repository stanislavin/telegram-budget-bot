import logging
import re
from datetime import datetime, timedelta

import asyncpg

from util.config import DATABASE_URL
from util.retry_handler import with_retry

logger = logging.getLogger(__name__)

_pool = None
_spending_type_column_ensured = False
_planned_column_ensured = False


async def _ensure_spending_type_column(pool):
    """Lazily add spending_type column to expenses table if it doesn't exist."""
    global _spending_type_column_ensured
    if not _spending_type_column_ensured:
        await pool.execute(
            "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS spending_type VARCHAR DEFAULT NULL"
        )
        _spending_type_column_ensured = True


async def _ensure_planned_column(pool):
    """Lazily add planned column to expenses table if it doesn't exist."""
    global _planned_column_ensured
    if not _planned_column_ensured:
        await pool.execute(
            "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS planned BOOLEAN DEFAULT TRUE"
        )
        _planned_column_ensured = True


def _clean_dsn(dsn: str) -> str:
    """Strip unsupported libpq parameters (e.g. channel_binding) from the DSN."""
    return re.sub(r'[&?]channel_binding=[^&]*', '', dsn)


async def get_pool() -> asyncpg.Pool:
    """Return the connection pool, creating it lazily on first call."""
    global _pool
    if _pool is None:
        dsn = DATABASE_URL
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured")
        _pool = await asyncpg.create_pool(_clean_dsn(dsn), min_size=1, max_size=5)
    return _pool


@with_retry(max_retries=1, error_message="Error saving to PostgreSQL")
async def save_to_postgres(amount: float, currency: str, category: str, description: str, spending_type: str | None = None, planned: bool = True):
    """Insert a new expense row into PostgreSQL."""
    pool = await get_pool()
    await _ensure_spending_type_column(pool)
    await _ensure_planned_column(pool)
    timestamp = datetime.now()
    await pool.execute(
        """INSERT INTO expenses (timestamp, amount, currency, category, description, source_file, spending_type, planned)
           VALUES ($1, $2, $3, $4, $5, 'bot', $6, $7)""",
        timestamp, float(amount), currency, category, description, spending_type, planned,
    )
    return True


async def delete_last_expense_pg():
    """Delete the most recent expense.

    Returns:
        (dict, None) on success — dict has amount, currency, category, description.
        (None, str)  on failure — str is the error message.
    """
    try:
        pool = await get_pool()
        await _ensure_spending_type_column(pool)
        row = await pool.fetchrow(
            """DELETE FROM expenses
               WHERE id = (SELECT MAX(id) FROM expenses)
               RETURNING amount, currency, category, description, spending_type"""
        )
        if row is None:
            return None, "No expenses to delete."

        expense_info = {
            'amount': str(row['amount']),
            'currency': row['currency'],
            'category': row['category'],
            'description': row['description'],
            'spending_type': row['spending_type'],
        }
        return expense_info, None
    except Exception as e:
        logger.error(f"Error deleting last expense from Postgres: {e}")
        return None, str(e)


async def get_daily_stats_pg(target_date=None):
    """Get daily spending statistics grouped by currency and category.

    Returns:
        (currency_totals dict, category_totals dict)
    """
    if target_date is None:
        target_date = datetime.now().date()
    elif hasattr(target_date, 'date'):
        target_date = target_date.date()

    pool = await get_pool()

    currency_rows = await pool.fetch(
        "SELECT currency, SUM(amount) AS total FROM expenses WHERE DATE(timestamp) = $1 GROUP BY currency",
        target_date,
    )
    category_rows = await pool.fetch(
        "SELECT category, SUM(amount) AS total FROM expenses WHERE DATE(timestamp) = $1 GROUP BY category",
        target_date,
    )

    currency_totals = {r['currency']: float(r['total']) for r in currency_rows}
    category_totals = {r['category']: float(r['total']) for r in category_rows}
    return currency_totals, category_totals


async def get_daily_summary_pg(target_date=None):
    """Get formatted daily spending summary — mirrors sheets.get_daily_summary output."""
    try:
        if target_date is None:
            target_date = datetime.now().date()
        elif hasattr(target_date, 'date'):
            target_date = target_date.date()

        currency_totals, category_totals = await get_daily_stats_pg(target_date)

        formatted_date = target_date.strftime("%d/%m/%Y")

        if not category_totals:
            return f"No expenses found for {formatted_date}.", None

        message = f"\U0001f4b0 Daily Summary for {formatted_date}:\n\n"

        sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)

        for category, amount in sorted_categories:
            if len(currency_totals) == 1:
                currency = list(currency_totals.keys())[0]
                message += f"\U0001f4ca {category}: {amount:.2f} {currency}\n"
            else:
                message += f"\U0001f4ca {category}: {amount:.2f}\n"

        message += "\n\U0001f4b8 Total spent:\n"
        for currency, total in currency_totals.items():
            message += f"- {total:.2f} {currency}\n"

        return message, None
    except Exception as e:
        logger.error(f"Error fetching daily summary from Postgres: {e}", exc_info=True)
        return f"Error fetching daily summary: {e}", None


async def get_recent_expenses_pg(days: int = 2, category: str | None = None, spending_type: str | None = None) -> str:
    """Fetch expenses from the last N days — mirrors sheets.get_recent_expenses output."""
    try:
        today = datetime.now().date()
        start_date = today - timedelta(days=days - 1)

        pool = await get_pool()
        await _ensure_spending_type_column(pool)

        query = """SELECT timestamp, amount, currency, category, description
               FROM expenses
               WHERE DATE(timestamp) >= $1"""
        params = [start_date]  # type: ignore[list-item]
        idx = 2
        if category:
            query += f" AND category = ${idx}"
            params.append(category)  # type: ignore[arg-type]
            idx += 1
        if spending_type:
            query += f" AND spending_type = ${idx}"
            params.append(spending_type)  # type: ignore[arg-type]
            idx += 1
        query += " ORDER BY timestamp DESC"

        rows = await pool.fetch(query, *params)

        if not rows:
            return f"No expenses found in the last {days} days."

        recent_expenses = []
        currency_totals = {}

        for row in rows:
            amount = float(row['amount'])
            currency = row['currency']
            category = row['category']
            description = row['description']
            ts = row['timestamp']

            formatted_ts = ts.strftime("%d/%m/%Y %H:%M")
            recent_expenses.append(f"{formatted_ts} | {amount:.2f} {currency} | {category} | {description}")

            currency_totals[currency] = currency_totals.get(currency, 0) + amount

        message = f"\U0001f4c5 Recent Expenses (Last {days} Days):\n\n"
        message += "Date/Time | Amount | Category | Description\n"
        message += "-" * 50 + "\n"

        for expense in recent_expenses:
            message += expense + "\n"

        message += "\n\U0001f4b8 Total:\n"
        for currency, total in currency_totals.items():
            message += f"- {total:.2f} {currency}\n"

        return message
    except Exception as e:
        logger.error(f"Error fetching recent expenses from Postgres: {e}")
        return f"Error fetching recent expenses: {e}"


async def upsert_chat_id(chat_id: int) -> None:
    """Track a chat ID for broadcast messages (e.g. deploy notifications)."""
    pool = await get_pool()
    await pool.execute(
        """CREATE TABLE IF NOT EXISTS bot_chats (
               chat_id BIGINT PRIMARY KEY,
               updated_at TIMESTAMP DEFAULT NOW()
           )"""
    )
    await pool.execute(
        """INSERT INTO bot_chats (chat_id, updated_at) VALUES ($1, NOW())
           ON CONFLICT (chat_id) DO UPDATE SET updated_at = NOW()""",
        chat_id,
    )


async def get_all_chat_ids() -> list[int]:
    """Return all known chat IDs."""
    pool = await get_pool()
    await pool.execute(
        """CREATE TABLE IF NOT EXISTS bot_chats (
               chat_id BIGINT PRIMARY KEY,
               updated_at TIMESTAMP DEFAULT NOW()
           )"""
    )
    rows = await pool.fetch("SELECT chat_id FROM bot_chats")
    return [r["chat_id"] for r in rows]


async def get_last_deployed_commit() -> str | None:
    """Return the last deployed commit hash, or None if not stored yet."""
    pool = await get_pool()
    await pool.execute(
        """CREATE TABLE IF NOT EXISTS bot_meta (
               key VARCHAR PRIMARY KEY,
               value VARCHAR NOT NULL
           )"""
    )
    row = await pool.fetchrow("SELECT value FROM bot_meta WHERE key = 'deployed_commit'")
    return row["value"] if row else None


async def set_last_deployed_commit(commit: str) -> None:
    """Store the current deployed commit hash."""
    pool = await get_pool()
    await pool.execute(
        """CREATE TABLE IF NOT EXISTS bot_meta (
               key VARCHAR PRIMARY KEY,
               value VARCHAR NOT NULL
           )"""
    )
    await pool.execute(
        """INSERT INTO bot_meta (key, value) VALUES ('deployed_commit', $1)
           ON CONFLICT (key) DO UPDATE SET value = $1""",
        commit,
    )


async def close_pool():
    """Close the connection pool for graceful shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
