"""Tests for the web dashboard API endpoints."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from util.health import build_app


@pytest.fixture
def client():
    """Create a Flask test client with mocked DB pool.

    _run() is called twice per request: once for get_pool() → returns pool,
    once for pool.fetch() → returns whatever mock_pool.fetch() is set to.
    """
    mock_pool = MagicMock()
    mock_pool.fetch = MagicMock(return_value=[])

    call_count = 0

    def fake_run(coro):
        nonlocal call_count
        # Close the real coroutine to avoid warnings
        import asyncio

        if asyncio.iscoroutine(coro):
            coro.close()

        call_count += 1
        if call_count % 2 == 1:
            # Odd calls: get_pool()
            return mock_pool
        # Even calls: pool.fetch(...)
        return mock_pool.fetch()

    with patch("web.api._run", side_effect=fake_run):
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
                },
                {
                    "timestamp": datetime(2025, 1, 20, 9, 0),
                    "amount": 200.0,
                    "currency": "RUB",
                    "category": "taxi",
                    "description": "ride home",
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
