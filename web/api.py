import asyncio
import calendar
import hashlib
import logging
import random
from datetime import datetime
from threading import Thread

import asyncpg
from flask import Blueprint, jsonify, request

from util.config import DATABASE_URL, GIT_COMMIT_SHORT
from util.openrouter import _call_chat_completion, _build_provider_chain, _build_provider_chain_dynamic
from util.postgres import _clean_dsn
from util.llm_settings import (
    _ensure_table as _ensure_llm_table,
    get_all_settings,
    upsert_setting,
    delete_setting,
    apply_env_overrides,
    build_provider_chain_from_settings,
)

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
_planned_col_ensured = False
_app_settings_table_ensured = False


async def _ensure_spending_type_column(pool):
    global _spending_type_col_ensured
    if not _spending_type_col_ensured:
        await pool.execute(
            "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS spending_type VARCHAR DEFAULT NULL"
        )
        _spending_type_col_ensured = True


async def _ensure_planned_column(pool):
    global _planned_col_ensured
    if not _planned_col_ensured:
        await pool.execute(
            "ALTER TABLE expenses ADD COLUMN IF NOT EXISTS planned BOOLEAN DEFAULT TRUE"
        )
        _planned_col_ensured = True


async def _get_web_pool():
    """Return a connection pool dedicated to the web API, created on _loop."""
    global _web_pool
    if _web_pool is None:
        dsn = DATABASE_URL
        if not dsn:
            raise RuntimeError("DATABASE_URL is not configured")
        _web_pool = await asyncpg.create_pool(_clean_dsn(dsn), min_size=1, max_size=3)
    return _web_pool


def _run(coro):
    """Submit a coroutine to the persistent loop and wait for the result."""
    return asyncio.run_coroutine_threadsafe(coro, _loop).result()


@api_bp.route("/api/version")
def version():
    return jsonify({"commit": GIT_COMMIT_SHORT})


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


_BUDGET_TABLE_CREATED = False
_due_day_col_ensured = False


async def _ensure_budget_table(pool):
    global _BUDGET_TABLE_CREATED, _due_day_col_ensured
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
    if not _due_day_col_ensured:
        await pool.execute(
            "ALTER TABLE budget_plan_items ADD COLUMN IF NOT EXISTS due_day INTEGER DEFAULT NULL"
        )
        _due_day_col_ensured = True


async def _ensure_app_settings_table(pool):
    global _app_settings_table_ensured
    if not _app_settings_table_ensured:
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        _app_settings_table_ensured = True


async def _get_demo_mode(pool) -> bool:
    row = await pool.fetchrow("SELECT value FROM app_settings WHERE key = 'demo_mode'")
    return row is not None and row["value"] == "true"


def _generate_demo_budget(month: str, include_compare: bool = True) -> dict:
    """Generate a fully synthetic budget response for demo mode."""
    seed = int(hashlib.md5(month.encode()).hexdigest(), 16) % (2 ** 32)
    rng = random.Random(seed)

    dt = datetime.strptime(month, "%Y-%m")
    days_in_month = calendar.monthrange(dt.year, dt.month)[1]
    today = datetime.now()
    is_current = (dt.year == today.year and dt.month == today.month)
    active_days = today.day if is_current else days_in_month

    cfg = [
        ("Housing",       "need",      [("Rent", 35000, 1), ("Electricity", 3000, 25), ("Internet", 900, 15)]),
        ("Food",          "need",      [("Groceries", 18000, None), ("Restaurants", 7000, None)]),
        ("Transport",     "need",      [("Public transit", 2200, None), ("Taxi", 3500, None)]),
        ("Health",        "wellbeing", [("Gym", 3500, 1), ("Pharmacy", 1500, None)]),
        ("Entertainment", "want",      [("Streaming", 900, 10), ("Leisure", 4500, None)]),
        ("Clothing",      "want",      [("Clothing", 5000, None)]),
        ("Savings",       "invest",    [("Emergency fund", 10000, 1), ("Investments", 5000, 1)]),
    ]

    categories = []
    st_totals: dict = {}

    for cat_name, stype, items_cfg in cfg:
        items = []
        for desc, base, due_day in items_cfg:
            amount = round(base * rng.uniform(0.9, 1.1) / 500) * 500
            items.append({"id": rng.randint(1000, 9999), "description": desc,
                          "amount": float(amount), "due_day": due_day})

        planned = sum(it["amount"] for it in items)
        ratio = rng.uniform(0.65, 1.10) * (active_days / days_in_month)
        target = planned * ratio

        n = rng.randint(3, 8)
        weights = [rng.random() for _ in range(n)]
        total_w = sum(weights)
        expenses = []
        for w in weights:
            amt = max(100.0, round(target * w / total_w / 100) * 100)
            day = rng.randint(1, active_days)
            ts = f"{dt.year:04d}-{dt.month:02d}-{day:02d} {rng.randint(8, 22):02d}:{rng.randint(0, 59):02d}"
            expenses.append({
                "timestamp": ts,
                "description": rng.choice(items_cfg)[0],
                "amount": float(amt),
                "orig_amount": float(amt),
                "orig_currency": "RUB",
                "spending_type": stype,
                "planned": True,
            })

        expenses.sort(key=lambda e: e["timestamp"])
        actual = round(sum(e["amount"] for e in expenses), 2)
        st_totals[stype] = st_totals.get(stype, 0) + actual

        categories.append({
            "category": cat_name, "items": items, "expenses": expenses,
            "planned": round(planned, 2), "actual": actual,
            "diff": round(planned - actual, 2),
        })

    total_planned = round(sum(c["planned"] for c in categories), 2)
    total_actual = round(sum(c["actual"] for c in categories), 2)

    st_sum = sum(st_totals.values()) or 1
    spending_type_summary = {
        st: {"amount": round(st_totals.get(st, 0), 2),
             "percentage": round(st_totals.get(st, 0) / st_sum * 100, 1)}
        for st in ("need", "want", "invest", "wellbeing")
    }

    p_exp = round(sum(e["amount"] for c in categories for e in c["expenses"]), 2)
    planned_summary = {
        "planned": {"amount": p_exp, "percentage": 100.0},
        "unplanned": {"amount": 0.0, "percentage": 0.0},
    }

    if dt.month == 1:
        prev_dt = datetime(dt.year - 1, 12, 1)
    else:
        prev_dt = datetime(dt.year, dt.month - 1, 1)
    prev_month = prev_dt.strftime("%Y-%m")

    if include_compare:
        prev = _generate_demo_budget(prev_month, include_compare=False)
        compare_data = {
            "month": prev_month,
            "items_by_cat": {c["category"]: c["items"] for c in prev["categories"]},
            "actuals": {c["category"]: c["actual"] for c in prev["categories"]},
            "expenses_by_cat": {c["category"]: c["expenses"] for c in prev["categories"]},
        }
    else:
        compare_data = {"month": prev_month, "items_by_cat": {}, "actuals": {}, "expenses_by_cat": {}}

    return {
        "month": month, "categories": categories,
        "total_planned": total_planned, "total_actual": total_actual,
        "total_diff": round(total_planned - total_actual, 2),
        "spending_type_summary": spending_type_summary,
        "planned_summary": planned_summary,
        "compare_data": compare_data,
    }


@api_bp.route("/api/demo-mode")
def get_demo_mode():
    try:
        pool = _run(_get_web_pool())
        _run(_ensure_app_settings_table(pool))
        demo = _run(_get_demo_mode(pool))
        return jsonify({"demo": demo})
    except Exception as e:
        logger.error(f"Error getting demo mode: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/demo-mode", methods=["POST"])
def set_demo_mode():
    try:
        body = request.get_json() or {}
        demo = bool(body.get("demo", False))
        pool = _run(_get_web_pool())
        _run(_ensure_app_settings_table(pool))
        _run(pool.execute(
            "INSERT INTO app_settings (key, value) VALUES ('demo_mode', $1) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "true" if demo else "false",
        ))
        return jsonify({"demo": demo})
    except Exception as e:
        logger.error(f"Error setting demo mode: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/budget", methods=["GET"])
def get_budget():
    """Return budget plan items grouped by category alongside actual spending."""
    try:
        month = request.args.get("month")

        if not month:
            return jsonify({"error": "month parameter is required"}), 400

        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError:
            return jsonify({"error": "month must be YYYY-MM"}), 400

        pool = _run(_get_web_pool())
        _run(_ensure_app_settings_table(pool))
        if _run(_get_demo_mode(pool)):
            return jsonify(_generate_demo_budget(month))

        dt = datetime.strptime(month, "%Y-%m")
        dt_from = datetime(dt.year, dt.month, 1)
        if dt.month == 12:
            dt_to = datetime(dt.year + 1, 1, 1)
        else:
            dt_to = datetime(dt.year, dt.month + 1, 1)

        _run(_ensure_budget_table(pool))

        # Get plan items for main month
        item_rows = _run(pool.fetch(
            """SELECT id, category, description, amount, due_day
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
                "due_day": r["due_day"],
            })

        # Always compute previous month for inline comparison
        if dt.month == 1:
            compare_dt_from = datetime(dt.year - 1, 12, 1)
        else:
            compare_dt_from = datetime(dt.year, dt.month - 1, 1)
        compare_dt_to = dt_from

        compare_item_rows = _run(pool.fetch(
            """SELECT id, category, description, amount, due_day
               FROM budget_plan_items WHERE month = $1
               ORDER BY category, id""",
            compare_dt_from.date(),
        ))

        compare_items_by_cat = {}
        for r in compare_item_rows:
            cat = r["category"]
            compare_items_by_cat.setdefault(cat, []).append({
                "id": r["id"],
                "description": r["description"],
                "amount": round(float(r["amount"]), 2),
                "due_day": r["due_day"],
            })

        compare_data = {
            "month": compare_dt_from.strftime("%Y-%m"),
            "items_by_cat": compare_items_by_cat,
            "actuals": {},  # filled after expense fetch below
            "expenses_by_cat": {},  # filled after expense fetch below
        }

        # Get actual expenses with details (converted to RUB)
        _run(_ensure_spending_type_column(pool))
        _run(_ensure_planned_column(pool))
        expense_rows = _run(pool.fetch("""
            SELECT e.category, e.timestamp, e.description, e.spending_type,
                   COALESCE(e.planned, TRUE) AS planned,
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
                "planned": r["planned"],
            })
        # Round totals
        actuals = {k: round(v, 2) for k, v in actuals.items()}

        # Get actual expenses for previous month (always)
        compare_actuals = {}
        compare_expenses_by_cat = {}
        compare_expense_rows = _run(pool.fetch("""
                SELECT e.category, e.timestamp, e.description, e.spending_type,
                       COALESCE(e.planned, TRUE) AS planned,
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
            """, compare_dt_from, compare_dt_to))

        for r in compare_expense_rows:
            cat = r["category"]
            amt = round(float(r["converted_amount"]), 2)
            compare_actuals[cat] = compare_actuals.get(cat, 0) + amt
            compare_expenses_by_cat.setdefault(cat, []).append({
                "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M"),
                "description": r["description"] or "",
                "amount": amt,
                "orig_amount": float(r["orig_amount"]),
                "orig_currency": r["orig_currency"],
                "spending_type": r["spending_type"],
                "planned": r["planned"],
            })
        compare_actuals = {k: round(v, 2) for k, v in compare_actuals.items()}
        compare_data["actuals"] = compare_actuals
        compare_data["expenses_by_cat"] = compare_expenses_by_cat

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
        for st in ("need", "want", "invest", "wellbeing"):
            amount = round(spending_type_totals.get(st, 0), 2)
            pct = round(amount / st_total * 100, 1) if st_total > 0 else 0
            spending_type_summary[st] = {"amount": amount, "percentage": pct}

        # Planned vs unplanned summary
        planned_total = 0
        unplanned_total = 0
        for cat_data in data:
            for exp in cat_data.get("expenses", []):
                if exp["planned"]:
                    planned_total += exp["amount"]
                else:
                    unplanned_total += exp["amount"]
        p_total = planned_total + unplanned_total
        planned_summary = {
            "planned": {"amount": round(planned_total, 2), "percentage": round(planned_total / p_total * 100, 1) if p_total > 0 else 0},
            "unplanned": {"amount": round(unplanned_total, 2), "percentage": round(unplanned_total / p_total * 100, 1) if p_total > 0 else 0},
        }

        return jsonify({
            "month": month,
            "categories": data,
            "total_planned": round(total_planned, 2),
            "total_actual": round(total_actual, 2),
            "total_diff": round(total_planned - total_actual, 2),
            "spending_type_summary": spending_type_summary,
            "planned_summary": planned_summary,
            "compare_data": compare_data,
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
        _run(_ensure_app_settings_table(pool))
        if _run(_get_demo_mode(pool)):
            return jsonify({"error": "Demo mode is active — saving is disabled"}), 403
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
            due_day = item.get("due_day")
            if due_day is not None:
                try:
                    due_day = int(due_day)
                    if due_day < 1 or due_day > 31:
                        due_day = None
                except (ValueError, TypeError):
                    due_day = None
            _run(pool.execute(
                """INSERT INTO budget_plan_items (month, category, description, amount, due_day)
                   VALUES ($1, $2, $3, $4, $5)""",
                month_date, category, description, amount, due_day,
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
        _run(_ensure_planned_column(pool))
        rows = _run(pool.fetch("""
            SELECT id, timestamp, amount, currency, category, description, spending_type,
                   COALESCE(planned, TRUE) AS planned
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
                "planned": row["planned"],
            })

        return jsonify(data)
    except Exception as e:
        logger.error(f"Error fetching monthly expenses: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/expenses/<int:expense_id>", methods=["DELETE"])
def delete_expense(expense_id):
    """Delete a specific expense."""
    try:
        pool = _run(_get_web_pool())
        result = _run(pool.execute(
            "DELETE FROM expenses WHERE id = $1", expense_id,
        ))

        if result == "DELETE 0":
            return jsonify({"error": "expense not found"}), 404

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error deleting expense: {e}", exc_info=True)
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
        if spending_type not in ("need", "want", "invest", "wellbeing"):
            return jsonify({"error": "spending_type must be one of: need, want, invest, wellbeing"}), 400

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


@api_bp.route("/api/expenses/<int:expense_id>/planned", methods=["PATCH"])
def update_expense_planned(expense_id):
    """Update the planned flag of a specific expense."""
    try:
        body = request.get_json()
        if not body or "planned" not in body:
            return jsonify({"error": "planned is required"}), 400

        planned = body["planned"]
        if not isinstance(planned, bool):
            return jsonify({"error": "planned must be a boolean"}), 400

        pool = _run(_get_web_pool())
        _run(_ensure_planned_column(pool))
        result = _run(pool.execute(
            "UPDATE expenses SET planned = $1 WHERE id = $2",
            planned, expense_id,
        ))

        if result == "UPDATE 0":
            return jsonify({"error": "expense not found"}), 404

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error updating expense planned flag: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/analyze", methods=["POST"])
def analyze_expenses():
    """Send expenses + user prompt to OpenRouter for analysis."""
    try:
        body = request.get_json()
        if not body:
            return jsonify({"error": "JSON body required"}), 400

        prompt = body.get("prompt", "").strip()
        expenses = body.get("expenses", [])

        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        if not expenses:
            return jsonify({"error": "no expenses to analyze"}), 400

        # Format expenses as a table for the LLM
        lines = ["Date | Amount | Currency | Category | Type | Description"]
        lines.append("-" * 70)
        for e in expenses:
            lines.append(
                f"{e.get('timestamp', '')} | "
                f"{e.get('amount', '')} | "
                f"{e.get('currency', '')} | "
                f"{e.get('category', '')} | "
                f"{e.get('spending_type', '') or ''} | "
                f"{e.get('description', '')}"
            )

        expenses_text = "\n".join(lines)

        system_msg = (
            "You are a helpful financial analyst. The user will provide a list of "
            "their expenses and a question or prompt. Analyze the expenses and "
            "respond clearly and concisely. Use markdown formatting for readability. "
            "When mentioning amounts, include the currency."
        )

        user_msg = (
            f"Here are my expenses:\n\n```\n{expenses_text}\n```\n\n"
            f"My question: {prompt}"
        )

        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]

        last_error = None
        pool = _run(_get_web_pool())
        chain = _run(_build_provider_chain_dynamic(pool))
        for url, headers, model, timeout in chain:
            try:
                result, used_model = _call_chat_completion(
                    url, headers, model, messages, timeout=max(timeout, 60)
                )
                return jsonify({"response": result, "model": used_model, "full_prompt": system_msg + "\n\n" + user_msg})
            except Exception as e:
                logger.warning(f"Analyze: model {model} failed: {e}")
                last_error = e
                continue

        raise last_error or RuntimeError("All models failed")
    except Exception as e:
        logger.error(f"Error analyzing expenses: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------- LLM Settings endpoints ----------

@api_bp.route("/api/llm-settings")
def get_llm_settings():
    """Return all LLM provider settings (DB values with env overrides noted)."""
    try:
        pool = _run(_get_web_pool())
        settings = _run(get_all_settings(pool))

        # Mark which fields are overridden by env vars
        import os
        env_overrides = {}
        if os.getenv("LOCAL_LLM_URL"):
            env_overrides["local.primary.url"] = os.getenv("LOCAL_LLM_URL")
        if os.getenv("LOCAL_LLM_MODEL"):
            env_overrides["local.primary.model"] = os.getenv("LOCAL_LLM_MODEL")
        if os.getenv("LOCAL_LLM_TIMEOUT"):
            env_overrides["local.primary.timeout"] = os.getenv("LOCAL_LLM_TIMEOUT")
        if os.getenv("OPENROUTER_API_KEY"):
            env_overrides["openrouter.*.api_key"] = "***"
        if os.getenv("OPENROUTER_LLM_VERSION"):
            env_overrides["openrouter.primary.model"] = os.getenv("OPENROUTER_LLM_VERSION")
        if os.getenv("OPENROUTER_URL"):
            env_overrides["openrouter.*.url"] = os.getenv("OPENROUTER_URL")
        if os.getenv("OPENROUTER_FALLBACK_MODELS"):
            env_overrides["openrouter.fallbacks"] = os.getenv("OPENROUTER_FALLBACK_MODELS")

        # Apply overrides to get effective values
        effective = apply_env_overrides([dict(s) for s in settings])
        effective_chain = build_provider_chain_from_settings(effective)

        return jsonify({
            "settings": settings,
            "env_overrides": env_overrides,
            "effective_chain": [
                {"url": url, "model": model, "timeout": timeout}
                for url, _headers, model, timeout in effective_chain
            ],
        })
    except Exception as e:
        logger.error(f"Error fetching LLM settings: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/llm-settings", methods=["POST"])
def save_llm_setting():
    """Create or update an LLM provider setting."""
    try:
        body = request.get_json()
        if not body:
            return jsonify({"error": "JSON body required"}), 400

        provider = (body.get("provider") or "").strip()
        name = (body.get("name") or "").strip()
        model = (body.get("model") or "").strip()
        url = (body.get("url") or "").strip()

        if not all([provider, name, model, url]):
            return jsonify({"error": "provider, name, model, and url are required"}), 400

        api_key = body.get("api_key")
        timeout = int(body.get("timeout", 30))
        priority = int(body.get("priority", 0))
        enabled = body.get("enabled", True)

        pool = _run(_get_web_pool())
        _run(upsert_setting(
            pool, provider, name, model, url,
            api_key=api_key, timeout=timeout, priority=priority, enabled=enabled,
        ))

        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error saving LLM setting: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/llm-settings/<int:setting_id>", methods=["DELETE"])
def delete_llm_setting(setting_id):
    """Delete an LLM provider setting."""
    try:
        pool = _run(_get_web_pool())
        deleted = _run(delete_setting(pool, setting_id))
        if not deleted:
            return jsonify({"error": "setting not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error deleting LLM setting: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500
