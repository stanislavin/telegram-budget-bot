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
_spending_type_col_ensured = False


async def _ensure_spending_type_column(pool):
    global _spending_type_col_ensured
    if not _spending_type_col_ensured:
        await pool.execute(
            "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS spending_type VARCHAR DEFAULT NULL"
        )
        _spending_type_col_ensured = True


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
            SELECT timestamp, amount, currency, category, description, spending_type
            FROM expenses
            WHERE timestamp >= $1 AND timestamp < $2
              {cat_clause}
            ORDER BY timestamp DESC
        """
        pool = _run(_get_web_pool())
        _run(_ensure_spending_type_column(pool))
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
                    "spending_type": row["spending_type"],
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


_BUDGET_TABLE_CREATED = False


async def _ensure_budget_table(pool):
    global _BUDGET_TABLE_CREATED
    if not _BUDGET_TABLE_CREATED:
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS budget_plan_items (
                id SERIAL PRIMARY KEY,
                month DATE NOT NULL,
                category VARCHAR NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                amount NUMERIC NOT NULL DEFAULT 0
            )
        """)
        _BUDGET_TABLE_CREATED = True


@api_bp.route("/api/budget", methods=["GET"])
def get_budget():
    """Return budget plan items grouped by category alongside actual spending."""
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
        _run(_ensure_budget_table(pool))

        # Get plan items
        item_rows = _run(pool.fetch(
            """SELECT id, category, description, amount
               FROM budget_plan_items WHERE month = $1
               ORDER BY category, id""",
            dt_from.date(),
        ))

        # Group items by category
        items_by_cat = {}
        for r in item_rows:
            cat = r["category"]
            items_by_cat.setdefault(cat, []).append({
                "id": r["id"],
                "description": r["description"],
                "amount": round(float(r["amount"]), 2),
            })

        # Get actual expenses with details (converted to RUB)
        _run(_ensure_spending_type_column(pool))
        expense_rows = _run(pool.fetch("""
            SELECT e.category, e.timestamp, e.description, e.spending_type,
                   e.amount AS orig_amount, e.currency AS orig_currency,
                   CASE
                       WHEN e.currency = 'RUB' THEN e.amount
                       ELSE e.amount * COALESCE(
                           cr_exact.rate_to_rub,
                           cr_latest.rate_to_rub,
                           1
                       )
                   END AS converted_amount
            FROM expenses e
            LEFT JOIN currency_rates cr_exact
                ON cr_exact.currency = e.currency
                AND cr_exact.month = DATE_TRUNC('month', e.timestamp)::date
            LEFT JOIN LATERAL (
                SELECT rate_to_rub FROM currency_rates
                WHERE currency = e.currency
                ORDER BY month DESC LIMIT 1
            ) cr_latest ON cr_exact.rate_to_rub IS NULL
            WHERE e.timestamp >= $1 AND e.timestamp < $2
            ORDER BY e.timestamp DESC
        """, dt_from, dt_to))

        # Group expenses by category
        actuals = {}
        expenses_by_cat = {}
        spending_type_totals = {}
        for r in expense_rows:
            cat = r["category"]
            amt = round(float(r["converted_amount"]), 2)
            actuals[cat] = actuals.get(cat, 0) + amt
            st = r["spending_type"]
            if st:
                spending_type_totals[st] = spending_type_totals.get(st, 0) + amt
            expenses_by_cat.setdefault(cat, []).append({
                "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "description": r["description"] or "",
                "amount": amt,
                "orig_amount": float(r["orig_amount"]),
                "orig_currency": r["orig_currency"],
                "spending_type": st,
            })
        # Round totals
        actuals = {k: round(v, 2) for k, v in actuals.items()}

        # Merge all categories from both plans and actuals
        all_cats = sorted(set(list(items_by_cat.keys()) + list(actuals.keys())))
        data = []
        for cat in all_cats:
            items = items_by_cat.get(cat, [])
            planned = sum(item["amount"] for item in items)
            actual = actuals.get(cat, 0)
            data.append({
                "category": cat,
                "items": items,
                "expenses": expenses_by_cat.get(cat, []),
                "planned": round(planned, 2),
                "actual": round(actual, 2),
                "diff": round(planned - actual, 2),
            })

        total_planned = sum(d["planned"] for d in data)
        total_actual = sum(d["actual"] for d in data)

        # Spending type summary
        st_total = sum(spending_type_totals.values()) if spending_type_totals else 0
        spending_type_summary = {}
        for st in ("need", "want", "invest"):
            amount = round(spending_type_totals.get(st, 0), 2)
            pct = round(amount / st_total * 100, 1) if st_total > 0 else 0
            spending_type_summary[st] = {"amount": amount, "percentage": pct}

        return jsonify({
            "month": month,
            "categories": data,
            "total_planned": round(total_planned, 2),
            "total_actual": round(total_actual, 2),
            "total_diff": round(total_planned - total_actual, 2),
            "spending_type_summary": spending_type_summary,
        })
    except Exception as e:
        logger.error(f"Error fetching budget: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/budget", methods=["POST"])
def save_budget():
    """Save budget plan items for a month (full replace)."""
    try:
        body = request.get_json()
        if not body:
            return jsonify({"error": "JSON body required"}), 400

        month = body.get("month")
        items = body.get("items", [])

        if not month:
            return jsonify({"error": "month is required"}), 400

        try:
            dt = datetime.strptime(month, "%Y-%m")
        except ValueError:
            return jsonify({"error": "month must be YYYY-MM"}), 400

        month_date = datetime(dt.year, dt.month, 1).date()

        pool = _run(_get_web_pool())
        _run(_ensure_budget_table(pool))

        # Delete all existing items for this month, then insert new ones
        _run(pool.execute(
            "DELETE FROM budget_plan_items WHERE month = $1", month_date
        ))

        for item in items:
            category = item.get("category", "").strip()
            description = item.get("description", "").strip()
            try:
                amount = float(item.get("amount", 0))
            except (ValueError, TypeError):
                amount = 0
            if not category or amount <= 0:
                continue
            _run(pool.execute(
                """INSERT INTO budget_plan_items (month, category, description, amount)
                   VALUES ($1, $2, $3, $4)""",
                month_date, category, description, amount,
            ))

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error saving budget: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/monthly-expenses")
def monthly_expenses():
    """Return all expenses for a month with their IDs for editing."""
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
        _run(_ensure_spending_type_column(pool))
        rows = _run(pool.fetch("""
            SELECT id, timestamp, amount, currency, category, description, spending_type
            FROM expenses
            WHERE timestamp >= $1 AND timestamp < $2
            ORDER BY timestamp DESC
        """, dt_from, dt_to))

        data = []
        for row in rows:
            data.append({
                "id": row["id"],
                "timestamp": row["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "amount": float(row["amount"]),
                "currency": row["currency"],
                "category": row["category"],
                "description": row["description"] or "",
                "spending_type": row["spending_type"],
            })

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching monthly expenses: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/expenses/<int:expense_id>/category", methods=["PATCH"])
def update_expense_category(expense_id):
    """Update the category of a specific expense."""
    try:
        body = request.get_json()
        if not body or "category" not in body:
            return jsonify({"error": "category is required"}), 400

        category = body["category"].strip()
        if not category:
            return jsonify({"error": "category cannot be empty"}), 400

        pool = _run(_get_web_pool())
        result = _run(pool.execute(
            "UPDATE expenses SET category = $1 WHERE id = $2",
            category, expense_id,
        ))

        if result == "UPDATE 0":
            return jsonify({"error": "expense not found"}), 404

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error updating expense category: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/expenses/<int:expense_id>/spending-type", methods=["PATCH"])
def update_expense_spending_type(expense_id):
    """Update the spending type of a specific expense."""
    try:
        body = request.get_json()
        if not body or "spending_type" not in body:
            return jsonify({"error": "spending_type is required"}), 400

        spending_type = body["spending_type"].strip().lower()
        if spending_type not in ("need", "want", "invest"):
            return jsonify({"error": "spending_type must be one of: need, want, invest"}), 400

        pool = _run(_get_web_pool())
        _run(_ensure_spending_type_column(pool))
        result = _run(pool.execute(
            "UPDATE expenses SET spending_type = $1 WHERE id = $2",
            spending_type, expense_id,
        ))

        if result == "UPDATE 0":
            return jsonify({"error": "expense not found"}), 404

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error updating expense spending type: {e}", exc_info=True)
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
