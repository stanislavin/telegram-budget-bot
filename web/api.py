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


@api_bp.route("/api/analytics")
def analytics():
    """Return analytics: averages, biggest spenders, and outliers across a date range."""
    try:
        date_from = request.args.get("from")
        date_to = request.args.get("to")
        currency = request.args.get("currency", "RUB")

        if not date_from or not date_to:
            return jsonify({"error": "'from' and 'to' are required"}), 400

        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Dates must be YYYY-MM-DD"}), 400

        target = currency.upper()

        # Build the amount conversion expression
        if target == "RUB":
            amt_expr = """
                CASE
                    WHEN e.currency = 'RUB' THEN e.amount
                    ELSE e.amount * COALESCE(cr_source.rate_to_rub, 1)
                END
            """
            joins = """
                LEFT JOIN currency_rates cr_source
                    ON cr_source.currency = e.currency
                    AND cr_source.month = DATE_TRUNC('month', e.timestamp)::date
            """
            where_params = [dt_from, dt_to]
            param_offset = 2
        else:
            amt_expr = """
                CASE
                    WHEN e.currency = $1 THEN e.amount
                    WHEN e.currency = 'RUB' THEN e.amount / NULLIF(cr_target.rate_to_rub, 0)
                    ELSE e.amount * COALESCE(cr_source.rate_to_rub, 1)
                         / NULLIF(cr_target.rate_to_rub, 0)
                END
            """
            joins = """
                LEFT JOIN currency_rates cr_source
                    ON cr_source.currency = e.currency
                    AND cr_source.month = DATE_TRUNC('month', e.timestamp)::date
                LEFT JOIN currency_rates cr_target
                    ON cr_target.currency = $1
                    AND cr_target.month = DATE_TRUNC('month', e.timestamp)::date
            """
            where_params = [target, dt_from, dt_to]
            param_offset = 3

        from_idx = param_offset - 1  # $N for dt_from
        to_idx = param_offset        # $N+1 for dt_to

        # 1) Per-category monthly averages and totals
        cat_query = f"""
            WITH converted AS (
                SELECT e.category,
                       e.timestamp,
                       e.description,
                       ({amt_expr}) AS converted_amount
                FROM expenses e
                {joins}
                WHERE e.timestamp >= ${from_idx} AND e.timestamp < ${to_idx}
            ),
            months AS (
                SELECT COUNT(DISTINCT DATE_TRUNC('month', timestamp)) AS num_months
                FROM converted
            ),
            cat_stats AS (
                SELECT category,
                       SUM(converted_amount) AS total,
                       AVG(converted_amount) AS avg_per_expense,
                       COUNT(*) AS expense_count,
                       STDDEV(converted_amount) AS stddev_amount
                FROM converted
                GROUP BY category
            ),
            cat_monthly AS (
                SELECT category,
                       DATE_TRUNC('month', timestamp) AS month,
                       SUM(converted_amount) AS monthly_total
                FROM converted
                GROUP BY category, DATE_TRUNC('month', timestamp)
            ),
            cat_monthly_avg AS (
                SELECT category,
                       AVG(monthly_total) AS avg_monthly
                FROM cat_monthly
                GROUP BY category
            )
            SELECT cs.category,
                   COALESCE(cs.total, 0) AS total,
                   COALESCE(cma.avg_monthly, 0) AS avg_monthly,
                   cs.avg_per_expense,
                   cs.expense_count,
                   cs.stddev_amount,
                   m.num_months
            FROM cat_stats cs
            JOIN cat_monthly_avg cma ON cma.category = cs.category
            CROSS JOIN months m
            ORDER BY cs.total DESC
        """

        pool = _run(_get_web_pool())
        cat_rows = _run(pool.fetch(cat_query, *where_params))

        num_months = int(cat_rows[0]["num_months"]) if cat_rows else 0
        overall_total = sum(float(r["total"]) for r in cat_rows)
        overall_avg_monthly = overall_total / num_months if num_months > 0 else 0

        categories_data = []
        for row in cat_rows:
            cat = row["category"]
            avg_expense = float(row["avg_per_expense"]) if row["avg_per_expense"] else 0
            stddev = float(row["stddev_amount"]) if row["stddev_amount"] else 0

            categories_data.append({
                "category": cat,
                "total": round(float(row["total"]), 2),
                "avg_monthly": round(float(row["avg_monthly"]), 2),
                "avg_per_expense": round(avg_expense, 2),
                "expense_count": int(row["expense_count"]),
                "stddev": round(stddev, 2),
            })

        # 2) Top expenses and outliers per category
        detail_query = f"""
            WITH converted AS (
                SELECT e.category,
                       e.timestamp,
                       e.description,
                       e.amount AS orig_amount,
                       e.currency AS orig_currency,
                       ({amt_expr}) AS converted_amount
                FROM expenses e
                {joins}
                WHERE e.timestamp >= ${from_idx} AND e.timestamp < ${to_idx}
            ),
            cat_stats AS (
                SELECT category,
                       AVG(converted_amount) AS avg_amt,
                       STDDEV(converted_amount) AS std_amt
                FROM converted
                GROUP BY category
            ),
            ranked AS (
                SELECT c.*,
                       cs.avg_amt,
                       cs.std_amt,
                       ROW_NUMBER() OVER (PARTITION BY c.category ORDER BY c.converted_amount DESC) AS rn,
                       CASE WHEN cs.std_amt > 0 AND c.converted_amount > cs.avg_amt + 2 * cs.std_amt
                            THEN true ELSE false END AS is_outlier
                FROM converted c
                JOIN cat_stats cs ON cs.category = c.category
            )
            SELECT category, timestamp, description, orig_amount, orig_currency,
                   converted_amount, rn, is_outlier
            FROM ranked
            WHERE rn <= 5 OR is_outlier
            ORDER BY category, converted_amount DESC
        """

        detail_rows = _run(pool.fetch(detail_query, *where_params))

        # Group details by category
        top_by_cat = {}
        outliers_by_cat = {}
        for dr in detail_rows:
            cat = dr["category"]
            entry = {
                "timestamp": dr["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "description": dr["description"] or "",
                "amount": round(float(dr["converted_amount"]), 2),
                "orig_amount": float(dr["orig_amount"]),
                "orig_currency": dr["orig_currency"],
            }
            if int(dr["rn"]) <= 5:
                top_by_cat.setdefault(cat, []).append(entry)
            if dr["is_outlier"]:
                outliers_by_cat.setdefault(cat, []).append(entry)

        # Attach details to categories
        for cd in categories_data:
            cat = cd["category"]
            cd["top_expenses"] = top_by_cat.get(cat, [])
            cd["outliers"] = outliers_by_cat.get(cat, [])

        return jsonify({
            "months_count": num_months,
            "overall_total": round(overall_total, 2),
            "overall_avg_monthly": round(overall_avg_monthly, 2),
            "currency": target,
            "categories": categories_data,
        })
    except Exception as e:
        logger.error(f"Error fetching analytics: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/budget", methods=["GET"])
def get_budget():
    """Return budget plan for a month alongside actual spending."""
    try:
        month = request.args.get("month")
        if not month:
            return jsonify({"error": "month parameter is required"}), 400

        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return jsonify({"error": "month must be YYYY-MM"}), 400

        dt_from = datetime(dt.year, dt.month, 1)
        if dt.month == 12:
            dt_to = datetime(dt.year + 1, 1, 1)
        else:
            dt_to = datetime(dt.year, dt.month + 1, 1)

        pool = _run(_get_web_pool())

        # Ensure table exists
        _run(pool.execute("""
            CREATE TABLE IF NOT EXISTS budget_plans (
                month DATE NOT NULL,
                category VARCHAR NOT NULL,
                planned_amount NUMERIC NOT NULL DEFAULT 0,
                PRIMARY KEY (month, category)
            )
        """))

        # Get planned amounts
        plan_rows = _run(pool.fetch(
            "SELECT category, planned_amount FROM budget_plans WHERE month = $1",
            dt_from.date(),
        ))
        plans = {r["category"]: float(r["planned_amount"]) for r in plan_rows}

        # Get actual spending (converted to RUB)
        actual_rows = _run(pool.fetch("""
            SELECT e.category,
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
        """, dt_from, dt_to))
        actuals = {r["category"]: round(float(r["total"]), 2) for r in actual_rows}

        # Merge all categories from both plans and actuals
        all_cats = sorted(set(list(plans.keys()) + list(actuals.keys())))
        data = []
        for cat in all_cats:
            planned = plans.get(cat, 0)
            actual = actuals.get(cat, 0)
            data.append({
                "category": cat,
                "planned": round(planned, 2),
                "actual": round(actual, 2),
                "diff": round(planned - actual, 2),
            })

        total_planned = sum(d["planned"] for d in data)
        total_actual = sum(d["actual"] for d in data)

        return jsonify({
            "month": month,
            "categories": data,
            "total_planned": round(total_planned, 2),
            "total_actual": round(total_actual, 2),
            "total_diff": round(total_planned - total_actual, 2),
        })
    except Exception as e:
        logger.error(f"Error fetching budget: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/budget", methods=["POST"])
def save_budget():
    """Save budget plan entries for a month."""
    try:
        body = request.get_json()
        if not body:
            return jsonify({"error": "JSON body required"}), 400

        month = body.get("month")
        entries = body.get("entries", [])

        if not month:
            return jsonify({"error": "month is required"}), 400

        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return jsonify({"error": "month must be YYYY-MM"}), 400

        month_date = datetime(dt.year, dt.month, 1).date()

        pool = _run(_get_web_pool())

        # Ensure table exists
        _run(pool.execute("""
            CREATE TABLE IF NOT EXISTS budget_plans (
                month DATE NOT NULL,
                category VARCHAR NOT NULL,
                planned_amount NUMERIC NOT NULL DEFAULT 0,
                PRIMARY KEY (month, category)
            )
        """))

        # Upsert each entry
        for entry in entries:
            category = entry.get("category", "").strip()
            amount = entry.get("amount", 0)
            if not category:
                continue
            try:
                amount = float(amount)
            except (ValueError, TypeError):
                amount = 0

            if amount > 0:
                _run(pool.execute("""
                    INSERT INTO budget_plans (month, category, planned_amount)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (month, category)
                    DO UPDATE SET planned_amount = $3
                """, month_date, category, amount))
            else:
                # Remove zero entries
                _run(pool.execute(
                    "DELETE FROM budget_plans WHERE month = $1 AND category = $2",
                    month_date, category,
                ))

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error saving budget: {e}", exc_info=True)
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
