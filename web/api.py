import asyncio
import logging
from datetime import datetime
from threading import Thread

import asyncpg
from flask import Blueprint, jsonify, request

from util.config import DATABASE_URL
from util.postgres import _clean_dsn

logger = logging.getLogger(__name__)

api_bp = Blueprint("api", __name__)

# Persistent event loop running in a background thread.
# The web API needs its own loop AND its own pool so it doesn't interfere
# with the bot's main event loop (asyncpg pools are bound to the loop that
# created them).
_loop = asyncio.new_event_loop()
_thread = Thread(target=_loop.run_forever, daemon=True)
_thread.start()

_web_pool = None


async def _get_web_pool():
    """Return a connection pool dedicated to the web API, created on _loop."""
    global _web_pool
    if _web_pool is None:
        dsn = _clean_dsn(DATABASE_URL)
        _web_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    return _web_pool


def _run(coro):
    """Submit a coroutine to the persistent loop and wait for the result."""
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


@api_bp.route("/api/categories")
def categories():
    try:
        pool = _run(_get_web_pool())
        rows = _run(
            pool.fetch("SELECT DISTINCT category FROM expenses ORDER BY category")
        )
        return jsonify([r["category"] for r in rows])
    except Exception as e:
        logger.error(f"Error fetching categories: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/trends")
def trends():
    try:
        date_from = request.args.get("from")
        date_to = request.args.get("to")
        # Support both old 'category' and new 'categories' param
        categories_raw = (
            request.args.get("categories") or request.args.get("category") or ""
        )
        cat_list = [c.strip() for c in categories_raw.split(",") if c.strip()]
        split = request.args.get("split", "false").lower() in ("true", "1")
        currency = request.args.get("currency", "RUB")
        group_by = request.args.get("group_by", "month")

        if group_by not in ("month", "week"):
            return jsonify({"error": "group_by must be 'month' or 'week'"}), 400

        if not date_from or not date_to:
            return jsonify({"error": "'from' and 'to' are required"}), 400

        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Dates must be YYYY-MM-DD"}), 400

        target = currency.upper()

        # Build category filter clause and params
        # Parameter numbering depends on target currency
        if target == "RUB":
            base_idx = 4  # $1=group_by, $2=dt_from, $3=dt_to
        else:
            base_idx = 5  # $1=group_by, $2=target, $3=dt_from, $4=dt_to

        if cat_list:
            cat_placeholders = ", ".join(
                f"${base_idx + i}" for i in range(len(cat_list))
            )
            cat_clause = f"AND e.category IN ({cat_placeholders})"
        else:
            cat_clause = ""

        extra_select = ", e.category" if split else ""
        extra_group = ", e.category" if split else ""

        if target == "RUB":
            query = f"""
                SELECT
                    DATE_TRUNC($1, e.timestamp) AS period{extra_select},
                    SUM(
                        CASE
                            WHEN e.currency = 'RUB' THEN e.amount
                            ELSE e.amount * COALESCE(cr_source.rate_to_rub, 1)
                        END
                    ) AS total
                FROM expenses e
                LEFT JOIN currency_rates cr_source
                    ON cr_source.currency = e.currency
                    AND cr_source.month = DATE_TRUNC('month', e.timestamp)::date
                WHERE e.timestamp >= $2 AND e.timestamp < $3
                    {cat_clause}
                GROUP BY 1{extra_group} ORDER BY 1
            """
            params = [group_by, dt_from, dt_to] + cat_list
        else:
            query = f"""
                SELECT
                    DATE_TRUNC($1, e.timestamp) AS period{extra_select},
                    SUM(
                        CASE
                            WHEN e.currency = $2 THEN e.amount
                            WHEN e.currency = 'RUB' THEN e.amount / NULLIF(cr_target.rate_to_rub, 0)
                            ELSE e.amount * COALESCE(cr_source.rate_to_rub, 1)
                                 / NULLIF(cr_target.rate_to_rub, 0)
                        END
                    ) AS total
                FROM expenses e
                LEFT JOIN currency_rates cr_source
                    ON cr_source.currency = e.currency
                    AND cr_source.month = DATE_TRUNC('month', e.timestamp)::date
                LEFT JOIN currency_rates cr_target
                    ON cr_target.currency = $2
                    AND cr_target.month = DATE_TRUNC('month', e.timestamp)::date
                WHERE e.timestamp >= $3 AND e.timestamp < $4
                    {cat_clause}
                GROUP BY 1{extra_group} ORDER BY 1
            """
            params = [group_by, target, dt_from, dt_to] + cat_list

        pool = _run(_get_web_pool())
        rows = _run(pool.fetch(query, *params))

        data = []
        for row in rows:
            period = row["period"]
            total = row["total"]
            entry = {
                "period": period.strftime("%Y-%m-%d") if period else None,
                "total": round(float(total), 2) if total else 0,
            }
            if split:
                entry["category"] = row["category"]
            data.append(entry)

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching trends: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/expenses")
def expenses():
    """Return individual expenses for a given period."""
    try:
        date_from = request.args.get("from")
        date_to = request.args.get("to")
        categories_raw = (
            request.args.get("categories") or request.args.get("category") or ""
        )
        cat_list = [c.strip() for c in categories_raw.split(",") if c.strip()]

        if not date_from or not date_to:
            return jsonify({"error": "'from' and 'to' are required"}), 400

        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Dates must be YYYY-MM-DD"}), 400

        if cat_list:
            cat_placeholders = ", ".join(f"${3 + i}" for i in range(len(cat_list)))
            cat_clause = f"AND category IN ({cat_placeholders})"
        else:
            cat_clause = ""

        query = f"""
            SELECT timestamp, amount, currency, category, description
            FROM expenses
            WHERE timestamp >= $1 AND timestamp < $2
              {cat_clause}
            ORDER BY timestamp DESC
        """
        pool = _run(_get_web_pool())
        rows = _run(pool.fetch(query, dt_from, dt_to, *cat_list))

        data = []
        for row in rows:
            data.append(
                {
                    "timestamp": row["timestamp"].strftime("%Y-%m-%d %H:%M"),
                    "amount": float(row["amount"]),
                    "currency": row["currency"],
                    "category": row["category"],
                    "description": row["description"],
                }
            )

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching expenses: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/monthly-categories")
def monthly_categories():
    """Return category totals for a specific month."""
    try:
        month = request.args.get("month")
        if not month:
            return jsonify({"error": "month parameter is required"}), 400

        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return jsonify({"error": "month must be YYYY-MM"}), 400

        dt_from = datetime(dt.year, dt.month, 1)
        dt_to = datetime(dt.year, dt.month + 1, 1)

        query = f"""
            SELECT category,
                   SUM(
                       CASE
                           WHEN e.currency = 'RUB' THEN e.amount
                           ELSE e.amount * COALESCE(cr_source.rate_to_rub, 1)
                       END
                   ) AS total
            FROM expenses e
            LEFT JOIN currency_rates cr_source
                ON cr_source.currency = e.currency
                AND cr_source.month = DATE_TRUNC('month', e.timestamp)::date
            WHERE e.timestamp >= $1 AND e.timestamp < $2
            GROUP BY e.category
            ORDER BY total DESC
        """
        pool = _run(_get_web_pool())
        rows = _run(pool.fetch(query, dt_from, dt_to))

        data = []
        for row in rows:
            data.append(
                {
                    "category": row["category"],
                    "total": round(float(row["total"]), 2),
                }
            )

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching monthly categories: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/category-expenses")
def category_expenses():
    """Return expenses for a specific category within a month."""
    try:
        month = request.args.get("month")
        category = request.args.get("category")

        if not month or not category:
            return jsonify({"error": "month and category parameters are required"}), 400

        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return jsonify({"error": "month must be YYYY-MM"}), 400

        dt_from = datetime(dt.year, dt.month, 1)
        dt_to = datetime(dt.year, dt.month + 1, 1)

        query = f"""
            SELECT timestamp, amount, currency, description
            FROM expenses
            WHERE timestamp >= $1 AND timestamp < $2
              AND category = $3
            ORDER BY timestamp DESC
        """
        pool = _run(_get_web_pool())
        rows = _run(pool.fetch(query, dt_from, dt_to, category))

        data = []
        for row in rows:
            data.append(
                {
                    "timestamp": row["timestamp"].strftime("%Y-%m-%d %H:%M"),
                    "amount": float(row["amount"]),
                    "currency": row["currency"],
                    "description": row["description"],
                }
            )

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching category expenses: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
