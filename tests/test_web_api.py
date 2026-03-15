"""Tests for the web dashboard API endpoints."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from util.health import build_app


def _fake_run_with_chain(coro, chain):
    """Helper for analyze tests: returns chain for _build_provider_chain_dynamic,
    delegates everything else to a no-op."""
    import asyncio
    coro_name = getattr(coro, '__qualname__', '') or getattr(coro, '__name__', '')
    if asyncio.iscoroutine(coro):
        coro.close()
    if '_build_provider_chain_dynamic' in coro_name:
        return chain
    return None


@pytest.fixture
def client():
    """Create a Flask test client with mocked DB pool.

    _run() intercepts all coroutine calls. It returns mock_pool for
    _get_web_pool and _ensure_spending_type_column (no-ops), and
    delegates to mock_pool.fetch/execute for actual queries.
    """
    mock_pool = MagicMock()
    mock_pool.fetch = MagicMock(return_value=[])
    mock_pool.execute = MagicMock(return_value="UPDATE 1")

    # Track whether we've returned the pool yet for this request
    got_pool = False

    def fake_run(coro):
        nonlocal got_pool
        import asyncio

        # Inspect the coroutine name to decide what to return
        coro_name = getattr(coro, '__qualname__', '') or getattr(coro, '__name__', '')

        if asyncio.iscoroutine(coro):
            coro.close()

        # Pool acquisition and migration calls just return the pool
        if '_get_web_pool' in coro_name or '_ensure' in coro_name:
            return mock_pool
        # Everything else is a query — delegate to mock_pool.fetch()
        return mock_pool.fetch()

    with patch("web.api._run", side_effect=fake_run):
        # Pre-set the flag to skip actual ALTER TABLE
        import web.api
        web.api._spending_type_col_ensured = True
        app = build_app()
        app.config["TESTING"] = True
        yield app.test_client(), mock_pool


# ---------- GET /api/categories ----------


class TestCategories:
    def test_returns_category_list(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"category": "food"},
                {"category": "taxi"},
                {"category": "transport"},
            ]
        )
        response = test_client.get("/api/categories")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data == ["food", "taxi", "transport"]

    def test_returns_empty_list(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/categories")
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("connection failed"))
        response = test_client.get("/api/categories")
        assert response.status_code == 500
        data = json.loads(response.data)
        assert "error" in data


# ---------- GET /api/trends ----------


class TestTrends:
    BASE_PARAMS = "?from=2025-01-01&to=2025-07-01&currency=RUB&group_by=month"

    def test_returns_trend_data_rub(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": datetime(2025, 1, 1), "total": 15000.50},
                {"period": datetime(2025, 2, 1), "total": 22000.00},
            ]
        )
        response = test_client.get(f"/api/trends{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2
        assert data[0] == {"period": "2025-01-01", "total": 15000.5}
        assert data[1] == {"period": "2025-02-01", "total": 22000.0}

    def test_returns_trend_data_eur(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": datetime(2025, 3, 1), "total": 120.75},
            ]
        )
        response = test_client.get(
            "/api/trends?from=2025-01-01&to=2025-07-01&currency=EUR&group_by=month"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0] == {"period": "2025-03-01", "total": 120.75}

    def test_with_category_filter(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": datetime(2025, 1, 1), "total": 5000.0},
            ]
        )
        response = test_client.get(f"/api/trends{self.BASE_PARAMS}&category=food")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1

    def test_week_granularity(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get(
            "/api/trends?from=2025-01-01&to=2025-02-01&currency=RUB&group_by=week"
        )
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_empty_result(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get(f"/api/trends{self.BASE_PARAMS}")
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_null_total_returns_zero(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": datetime(2025, 1, 1), "total": None},
            ]
        )
        response = test_client.get(f"/api/trends{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data[0]["total"] == 0

    def test_null_period(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": None, "total": 100.0},
            ]
        )
        response = test_client.get(f"/api/trends{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data[0]["period"] is None

    # --- Validation errors ---

    def test_missing_from_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/trends?to=2025-07-01")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_missing_to_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/trends?from=2025-01-01")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_missing_both_dates_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/trends")
        assert response.status_code == 400

    def test_invalid_date_format_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/trends?from=01-01-2025&to=2025-07-01")
        assert response.status_code == 400
        assert "YYYY-MM-DD" in json.loads(response.data)["error"]

    def test_invalid_group_by_returns_400(self, client):
        test_client, _ = client
        response = test_client.get(
            "/api/trends?from=2025-01-01&to=2025-07-01&group_by=day"
        )
        assert response.status_code == 400
        assert "group_by" in json.loads(response.data)["error"]

    def test_multi_categories_filter(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": datetime(2025, 1, 1), "total": 8000.0},
            ]
        )
        response = test_client.get(
            f"/api/trends{self.BASE_PARAMS}&categories=food,taxi"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]["total"] == 8000.0

    def test_split_mode_returns_category(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": datetime(2025, 1, 1), "total": 5000.0, "category": "food"},
                {"period": datetime(2025, 1, 1), "total": 3000.0, "category": "taxi"},
            ]
        )
        response = test_client.get(
            f"/api/trends{self.BASE_PARAMS}&categories=food,taxi&split=true"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2
        assert data[0]["category"] == "food"
        assert data[1]["category"] == "taxi"

    def test_split_false_no_category_field(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"period": datetime(2025, 1, 1), "total": 8000.0},
            ]
        )
        response = test_client.get(
            f"/api/trends{self.BASE_PARAMS}&categories=food,taxi&split=false"
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert "category" not in data[0]

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("query failed"))
        response = test_client.get(f"/api/trends{self.BASE_PARAMS}")
        assert response.status_code == 500
        assert "error" in json.loads(response.data)


# ---------- GET /api/expenses ----------


class TestExpenses:
    BASE_PARAMS = "?from=2025-01-01&to=2025-02-01"

    def test_returns_expense_list(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {
                    "timestamp": datetime(2025, 1, 15, 12, 30),
                    "amount": 500.0,
                    "currency": "RUB",
                    "category": "food",
                    "description": "lunch",
                    "spending_type": "need",
                },
                {
                    "timestamp": datetime(2025, 1, 20, 9, 0),
                    "amount": 200.0,
                    "currency": "RUB",
                    "category": "taxi",
                    "description": "ride home",
                    "spending_type": "want",
                },
            ]
        )
        response = test_client.get(f"/api/expenses{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2
        assert data[0]["timestamp"] == "2025-01-15 12:30"
        assert data[0]["amount"] == 500.0
        assert data[0]["currency"] == "RUB"
        assert data[0]["category"] == "food"
        assert data[0]["description"] == "lunch"
        assert data[0]["spending_type"] == "need"

    def test_with_category_filter(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get(f"/api/expenses{self.BASE_PARAMS}&category=food")
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_with_multi_categories_filter(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get(
            f"/api/expenses{self.BASE_PARAMS}&categories=food,taxi"
        )
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_empty_result(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get(f"/api/expenses{self.BASE_PARAMS}")
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_missing_from_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/expenses?to=2025-02-01")
        assert response.status_code == 400

    def test_missing_to_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/expenses?from=2025-01-01")
        assert response.status_code == 400

    def test_invalid_date_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/expenses?from=bad&to=2025-02-01")
        assert response.status_code == 400

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("db down"))
        response = test_client.get(f"/api/expenses{self.BASE_PARAMS}")
        assert response.status_code == 500
        assert "error" in json.loads(response.data)


# ---------- GET / (dashboard) ----------


class TestDashboard:
    def test_index_serves_html(self, client):
        test_client, _ = client
        response = test_client.get("/")
        assert response.status_code == 200
        assert b"Expense Trends" in response.data
        assert b"chart.js" in response.data.lower() or b"Chart" in response.data

    def test_index_uses_month_inputs(self, client):
        test_client, _ = client
        response = test_client.get("/")
        assert b'type="month"' in response.data
        assert b'type="date"' not in response.data


# ---------- GET /api/monthly-categories ----------


class TestMonthlyCategories:
    def test_returns_category_totals(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {"category": "food", "total": 15000.50},
                {"category": "transport", "total": 8000.00},
                {"category": "taxi", "total": 5000.25},
            ]
        )
        response = test_client.get("/api/monthly-categories?month=2025-01")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 3
        assert data[0] == {"category": "food", "total": 15000.5}
        assert data[1] == {"category": "transport", "total": 8000.0}
        assert data[2] == {"category": "taxi", "total": 5000.25}

    def test_returns_empty_list(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/monthly-categories?month=2025-01")
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_missing_month_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/monthly-categories")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_invalid_month_format_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/monthly-categories?month=01-2025")
        assert response.status_code == 400
        assert "YYYY-MM" in json.loads(response.data)["error"]

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("query failed"))
        response = test_client.get("/api/monthly-categories?month=2025-01")
        assert response.status_code == 500
        assert "error" in json.loads(response.data)


# ---------- GET /api/category-expenses ----------


class TestCategoryExpenses:
    def test_returns_expense_list(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {
                    "timestamp": datetime(2025, 1, 15, 12, 30),
                    "amount": 500.0,
                    "currency": "RUB",
                    "description": "lunch",
                },
                {
                    "timestamp": datetime(2025, 1, 20, 9, 0),
                    "amount": 200.0,
                    "currency": "RUB",
                    "description": "ride home",
                },
            ]
        )
        response = test_client.get("/api/category-expenses?month=2025-01&category=food")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 2
        assert data[0]["timestamp"] == "2025-01-15 12:30"
        assert data[0]["amount"] == 500.0
        assert data[0]["currency"] == "RUB"
        assert data[0]["description"] == "lunch"

    def test_returns_empty_list(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/category-expenses?month=2025-01&category=food")
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_missing_month_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/category-expenses?category=food")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_missing_category_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/category-expenses?month=2025-01")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_invalid_month_format_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/category-expenses?month=01-2025&category=food")
        assert response.status_code == 400
        assert "YYYY-MM" in json.loads(response.data)["error"]

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("db down"))
        response = test_client.get("/api/category-expenses?month=2025-01&category=food")
        assert response.status_code == 500
        assert "error" in json.loads(response.data)


# ---------- GET /api/analytics ----------


@pytest.fixture
def analytics_client():
    """Create a Flask test client for analytics endpoint (3 _run calls: pool + 2 queries)."""
    mock_pool = MagicMock()
    # Default: both queries return empty
    cat_result = []
    detail_result = []

    call_count = 0

    def fake_run(coro):
        nonlocal call_count
        import asyncio

        if asyncio.iscoroutine(coro):
            coro.close()

        call_count += 1
        if call_count % 3 == 1:
            return mock_pool
        elif call_count % 3 == 2:
            return mock_pool.fetch_cat()
        else:
            return mock_pool.fetch_detail()

    mock_pool.fetch_cat = MagicMock(return_value=cat_result)
    mock_pool.fetch_detail = MagicMock(return_value=detail_result)
    # Regular fetch still used by the pool.fetch() call — route it by call order
    original_fetch = MagicMock()
    fetch_calls = []

    def routed_fetch(*args, **kwargs):
        fetch_calls.append(1)
        if len(fetch_calls) % 2 == 1:
            return mock_pool.fetch_cat(*args, **kwargs)
        return mock_pool.fetch_detail(*args, **kwargs)

    mock_pool.fetch = routed_fetch

    with patch("web.api._run", side_effect=fake_run):
        from util.health import build_app

        app = build_app()
        app.config["TESTING"] = True
        yield app.test_client(), mock_pool


class TestAnalytics:
    BASE_PARAMS = "?from=2025-01-01&to=2025-07-01&currency=RUB"

    def test_returns_analytics_data(self, analytics_client):
        test_client, mock_pool = analytics_client
        mock_pool.fetch_cat = MagicMock(
            return_value=[
                {
                    "category": "food",
                    "total": 45000.0,
                    "avg_monthly": 7500.0,
                    "avg_per_expense": 500.0,
                    "expense_count": 90,
                    "stddev_amount": 300.0,
                    "num_months": 6,
                },
                {
                    "category": "taxi",
                    "total": 12000.0,
                    "avg_monthly": 2000.0,
                    "avg_per_expense": 400.0,
                    "expense_count": 30,
                    "stddev_amount": 200.0,
                    "num_months": 6,
                },
            ]
        )
        mock_pool.fetch_detail = MagicMock(
            return_value=[
                {
                    "category": "food",
                    "timestamp": datetime(2025, 3, 10, 12, 0),
                    "description": "fancy dinner",
                    "converted_amount": 5000.0,
                    "orig_amount": 5000.0,
                    "orig_currency": "RUB",
                    "rn": 1,
                    "is_outlier": True,
                },
            ]
        )
        response = test_client.get(f"/api/analytics{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["months_count"] == 6
        assert data["overall_total"] == 57000.0
        assert data["overall_avg_monthly"] == 9500.0
        assert data["currency"] == "RUB"
        assert len(data["categories"]) == 2
        assert data["categories"][0]["category"] == "food"
        assert data["categories"][0]["total"] == 45000.0
        assert data["categories"][0]["avg_monthly"] == 7500.0
        assert len(data["categories"][0]["top_expenses"]) == 1
        assert len(data["categories"][0]["outliers"]) == 1
        assert data["categories"][0]["outliers"][0]["description"] == "fancy dinner"

    def test_empty_result(self, analytics_client):
        test_client, mock_pool = analytics_client
        mock_pool.fetch_cat = MagicMock(return_value=[])
        mock_pool.fetch_detail = MagicMock(return_value=[])
        response = test_client.get(f"/api/analytics{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["months_count"] == 0
        assert data["overall_total"] == 0
        assert data["overall_avg_monthly"] == 0
        assert data["categories"] == []

    def test_eur_currency(self, analytics_client):
        test_client, mock_pool = analytics_client
        mock_pool.fetch_cat = MagicMock(
            return_value=[
                {
                    "category": "food",
                    "total": 500.0,
                    "avg_monthly": 250.0,
                    "avg_per_expense": 50.0,
                    "expense_count": 10,
                    "stddev_amount": 20.0,
                    "num_months": 2,
                },
            ]
        )
        mock_pool.fetch_detail = MagicMock(return_value=[])
        response = test_client.get("/api/analytics?from=2025-01-01&to=2025-03-01&currency=EUR")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["currency"] == "EUR"
        assert data["categories"][0]["total"] == 500.0

    def test_missing_from_returns_400(self, analytics_client):
        test_client, _ = analytics_client
        response = test_client.get("/api/analytics?to=2025-07-01")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_missing_to_returns_400(self, analytics_client):
        test_client, _ = analytics_client
        response = test_client.get("/api/analytics?from=2025-01-01")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_invalid_date_format_returns_400(self, analytics_client):
        test_client, _ = analytics_client
        response = test_client.get("/api/analytics?from=01-01-2025&to=2025-07-01")
        assert response.status_code == 400
        assert "YYYY-MM-DD" in json.loads(response.data)["error"]

    def test_db_error_returns_500(self, analytics_client):
        test_client, mock_pool = analytics_client
        mock_pool.fetch_cat = MagicMock(side_effect=Exception("query failed"))
        response = test_client.get(f"/api/analytics{self.BASE_PARAMS}")
        assert response.status_code == 500
        assert "error" in json.loads(response.data)

    def test_null_stddev(self, analytics_client):
        test_client, mock_pool = analytics_client
        mock_pool.fetch_cat = MagicMock(
            return_value=[
                {
                    "category": "misc",
                    "total": 100.0,
                    "avg_monthly": 100.0,
                    "avg_per_expense": 100.0,
                    "expense_count": 1,
                    "stddev_amount": None,
                    "num_months": 1,
                },
            ]
        )
        mock_pool.fetch_detail = MagicMock(return_value=[])
        response = test_client.get(f"/api/analytics{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["categories"][0]["stddev"] == 0

    def test_no_outliers(self, analytics_client):
        test_client, mock_pool = analytics_client
        mock_pool.fetch_cat = MagicMock(
            return_value=[
                {
                    "category": "food",
                    "total": 1000.0,
                    "avg_monthly": 500.0,
                    "avg_per_expense": 100.0,
                    "expense_count": 10,
                    "stddev_amount": 50.0,
                    "num_months": 2,
                },
            ]
        )
        mock_pool.fetch_detail = MagicMock(
            return_value=[
                {
                    "category": "food",
                    "timestamp": datetime(2025, 1, 5, 10, 0),
                    "description": "groceries",
                    "converted_amount": 200.0,
                    "orig_amount": 200.0,
                    "orig_currency": "RUB",
                    "rn": 1,
                    "is_outlier": False,
                },
            ]
        )
        response = test_client.get(f"/api/analytics{self.BASE_PARAMS}")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["categories"][0]["outliers"] == []
        assert len(data["categories"][0]["top_expenses"]) == 1


# ---------- GET /api/budget ----------


@pytest.fixture
def budget_client():
    """Client for budget endpoints. Supports pool, ensure, fetch, execute calls."""
    mock_pool = MagicMock()
    mock_pool.fetch = MagicMock(return_value=[])
    mock_pool.execute = MagicMock(return_value="DELETE 0")

    def fake_run(coro):
        import asyncio

        coro_name = getattr(coro, "__qualname__", "") or getattr(coro, "__name__", "")
        if asyncio.iscoroutine(coro):
            coro.close()

        if "_get_web_pool" in coro_name or "_ensure" in coro_name:
            return mock_pool
        # Check if it looks like an execute or a fetch
        # pool.execute returns a string, pool.fetch returns a list
        return mock_pool.fetch()

    with patch("web.api._run", side_effect=fake_run):
        import web.api

        web.api._spending_type_col_ensured = True
        web.api._BUDGET_TABLE_CREATED = True
        app = build_app()
        app.config["TESTING"] = True
        yield app.test_client(), mock_pool


class TestGetBudget:
    def test_returns_budget_structure(self, budget_client):
        test_client, mock_pool = budget_client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/budget?month=2025-01")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["month"] == "2025-01"
        assert "categories" in data
        assert "total_planned" in data
        assert "total_actual" in data
        assert "spending_type_summary" in data

    def test_missing_month_returns_400(self, budget_client):
        test_client, _ = budget_client
        response = test_client.get("/api/budget")
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_invalid_month_format_returns_400(self, budget_client):
        test_client, _ = budget_client
        response = test_client.get("/api/budget?month=01-2025")
        assert response.status_code == 400
        assert "YYYY-MM" in json.loads(response.data)["error"]

    def test_december_boundary(self, budget_client):
        test_client, mock_pool = budget_client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/budget?month=2025-12")
        assert response.status_code == 200

    def test_empty_result(self, budget_client):
        test_client, mock_pool = budget_client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/budget?month=2025-01")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["categories"] == []
        assert data["total_planned"] == 0
        assert data["total_actual"] == 0

    def test_db_error_returns_500(self, budget_client):
        test_client, mock_pool = budget_client
        mock_pool.fetch = MagicMock(side_effect=Exception("db error"))
        response = test_client.get("/api/budget?month=2025-01")
        assert response.status_code == 500


# ---------- POST /api/budget ----------


class TestSaveBudget:
    def test_save_budget_success(self, budget_client):
        test_client, mock_pool = budget_client
        response = test_client.post(
            "/api/budget",
            data=json.dumps({
                "month": "2025-01",
                "items": [
                    {"category": "food", "description": "groceries", "amount": 10000},
                ],
            }),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert json.loads(response.data)["ok"] is True

    def test_missing_month_in_body_returns_400(self, budget_client):
        test_client, _ = budget_client
        response = test_client.post(
            "/api/budget",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_missing_month_returns_400(self, budget_client):
        test_client, _ = budget_client
        response = test_client.post(
            "/api/budget",
            data=json.dumps({"items": []}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "required" in json.loads(response.data)["error"]

    def test_invalid_month_format_returns_400(self, budget_client):
        test_client, _ = budget_client
        response = test_client.post(
            "/api/budget",
            data=json.dumps({"month": "bad", "items": []}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "YYYY-MM" in json.loads(response.data)["error"]

    def test_skips_items_with_no_category_or_zero_amount(self, budget_client):
        test_client, mock_pool = budget_client
        response = test_client.post(
            "/api/budget",
            data=json.dumps({
                "month": "2025-01",
                "items": [
                    {"category": "", "description": "x", "amount": 100},
                    {"category": "food", "description": "x", "amount": 0},
                    {"category": "food", "description": "x", "amount": "bad"},
                ],
            }),
            content_type="application/json",
        )
        assert response.status_code == 200

    def test_db_error_returns_500(self, budget_client):
        test_client, mock_pool = budget_client
        mock_pool.fetch = MagicMock(side_effect=Exception("db error"))
        response = test_client.post(
            "/api/budget",
            data=json.dumps({"month": "2025-01", "items": []}),
            content_type="application/json",
        )
        assert response.status_code == 500


# ---------- GET /api/monthly-expenses ----------


class TestMonthlyExpenses:
    def test_returns_expenses(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(
            return_value=[
                {
                    "id": 42,
                    "timestamp": datetime(2025, 1, 15, 12, 30),
                    "amount": 500.0,
                    "currency": "RUB",
                    "category": "food",
                    "description": "lunch",
                    "spending_type": "need",
                },
            ]
        )
        response = test_client.get("/api/monthly-expenses?month=2025-01")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]["id"] == 42
        assert data[0]["timestamp"] == "2025-01-15 12:30"
        assert data[0]["spending_type"] == "need"

    def test_missing_month_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/monthly-expenses")
        assert response.status_code == 400

    def test_invalid_month_returns_400(self, client):
        test_client, _ = client
        response = test_client.get("/api/monthly-expenses?month=bad")
        assert response.status_code == 400

    def test_december_boundary(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/monthly-expenses?month=2025-12")
        assert response.status_code == 200

    def test_empty_result(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value=[])
        response = test_client.get("/api/monthly-expenses?month=2025-01")
        assert response.status_code == 200
        assert json.loads(response.data) == []

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("db down"))
        response = test_client.get("/api/monthly-expenses?month=2025-01")
        assert response.status_code == 500


# ---------- DELETE /api/expenses/<id> ----------


class TestDeleteExpense:
    def test_delete_success(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value="DELETE 1")
        response = test_client.delete("/api/expenses/42")
        assert response.status_code == 200
        assert json.loads(response.data)["ok"] is True

    def test_delete_not_found(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value="DELETE 0")
        response = test_client.delete("/api/expenses/999")
        assert response.status_code == 404

    def test_delete_db_error(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("db error"))
        response = test_client.delete("/api/expenses/42")
        assert response.status_code == 500


# ---------- PATCH /api/expenses/<id>/category ----------


class TestUpdateCategory:
    def test_update_success(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value="UPDATE 1")
        response = test_client.patch(
            "/api/expenses/42/category",
            data=json.dumps({"category": "transport"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert json.loads(response.data)["ok"] is True

    def test_not_found(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value="UPDATE 0")
        response = test_client.patch(
            "/api/expenses/999/category",
            data=json.dumps({"category": "food"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_missing_category_key_returns_400(self, client):
        test_client, _ = client
        response = test_client.patch(
            "/api/expenses/42/category",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_empty_category_returns_400(self, client):
        test_client, _ = client
        response = test_client.patch(
            "/api/expenses/42/category",
            data=json.dumps({"category": "  "}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("db error"))
        response = test_client.patch(
            "/api/expenses/42/category",
            data=json.dumps({"category": "food"}),
            content_type="application/json",
        )
        assert response.status_code == 500


# ---------- PATCH /api/expenses/<id>/spending-type ----------


class TestUpdateSpendingType:
    def test_update_success(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value="UPDATE 1")
        response = test_client.patch(
            "/api/expenses/42/spending-type",
            data=json.dumps({"spending_type": "want"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert json.loads(response.data)["ok"] is True

    def test_not_found(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(return_value="UPDATE 0")
        response = test_client.patch(
            "/api/expenses/999/spending-type",
            data=json.dumps({"spending_type": "need"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    def test_missing_spending_type_key_returns_400(self, client):
        test_client, _ = client
        response = test_client.patch(
            "/api/expenses/42/spending-type",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_invalid_type_returns_400(self, client):
        test_client, _ = client
        response = test_client.patch(
            "/api/expenses/42/spending-type",
            data=json.dumps({"spending_type": "luxury"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_db_error_returns_500(self, client):
        test_client, mock_pool = client
        mock_pool.fetch = MagicMock(side_effect=Exception("db error"))
        response = test_client.patch(
            "/api/expenses/42/spending-type",
            data=json.dumps({"spending_type": "need"}),
            content_type="application/json",
        )
        assert response.status_code == 500


# ---------- POST /api/analyze ----------


class TestAnalyze:
    def test_success(self, client):
        test_client, _ = client
        chain = [("http://local/v1/chat/completions", {}, "local-model", 15)]
        with patch("web.api._run", side_effect=lambda coro: _fake_run_with_chain(coro, chain)), \
             patch("web.api._call_chat_completion") as mock_call:
            mock_call.return_value = ("Here is my analysis.", "local-model")

            response = test_client.post(
                "/api/analyze",
                data=json.dumps({
                    "prompt": "summarize my spending",
                    "expenses": [
                        {"timestamp": "2025-01-15", "amount": 500, "currency": "RUB",
                         "category": "food", "spending_type": "need", "description": "lunch"},
                    ],
                }),
                content_type="application/json",
            )
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["response"] == "Here is my analysis."
            assert data["model"] == "local-model"

    def test_fallback_on_first_failure(self, client):
        test_client, _ = client
        chain = [
            ("http://local/v1/chat/completions", {}, "local-model", 15),
            ("https://openrouter.ai/api/v1/chat/completions", {}, "openrouter-model", 30),
        ]
        with patch("web.api._run", side_effect=lambda coro: _fake_run_with_chain(coro, chain)), \
             patch("web.api._call_chat_completion") as mock_call:
            mock_call.side_effect = [
                ConnectionError("timeout"),
                ("Fallback analysis.", "openrouter-model"),
            ]

            response = test_client.post(
                "/api/analyze",
                data=json.dumps({
                    "prompt": "analyze",
                    "expenses": [{"amount": 100}],
                }),
                content_type="application/json",
            )
            assert response.status_code == 200
            data = json.loads(response.data)
            assert data["model"] == "openrouter-model"

    def test_missing_prompt_in_empty_body_returns_400(self, client):
        test_client, _ = client
        response = test_client.post(
            "/api/analyze",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_missing_prompt_returns_400(self, client):
        test_client, _ = client
        response = test_client.post(
            "/api/analyze",
            data=json.dumps({"expenses": [{"amount": 1}]}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "prompt" in json.loads(response.data)["error"]

    def test_empty_expenses_returns_400(self, client):
        test_client, _ = client
        response = test_client.post(
            "/api/analyze",
            data=json.dumps({"prompt": "analyze", "expenses": []}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "expenses" in json.loads(response.data)["error"]

    def test_all_providers_fail_returns_500(self, client):
        test_client, _ = client
        chain = [("http://local/v1/chat/completions", {}, "local-model", 15)]
        with patch("web.api._run", side_effect=lambda coro: _fake_run_with_chain(coro, chain)), \
             patch("web.api._call_chat_completion") as mock_call:
            mock_call.side_effect = ConnectionError("down")

            response = test_client.post(
                "/api/analyze",
                data=json.dumps({
                    "prompt": "analyze",
                    "expenses": [{"amount": 100}],
                }),
                content_type="application/json",
            )
            assert response.status_code == 500
