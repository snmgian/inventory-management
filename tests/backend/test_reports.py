"""
Tests for reports API endpoints (/api/reports/quarterly, /api/reports/monthly-trends).

The reports endpoints aggregate orders, so each filter test cross-validates the
aggregate against /api/orders called with the same filters.
"""
import pytest


FILTER_CASES = [
    "warehouse=Tokyo",
    "category=Sensors",
    "status=Delivered",
    "month=2025-03",
    "month=Q2-2025",
    "warehouse=San Francisco&category=Power Supplies&status=Delivered",
]


def _expected_monthly(orders):
    """Group raw orders by YYYY-MM the same way the endpoint should."""
    months = {}
    for order in orders:
        month = order["order_date"][:7]
        entry = months.setdefault(month, {"order_count": 0, "revenue": 0.0, "delivered_count": 0})
        entry["order_count"] += 1
        entry["revenue"] += order["total_value"]
        if order["status"] == "Delivered":
            entry["delivered_count"] += 1
    return months


def _expected_quarterly(orders):
    """Group raw orders by quarter label Qn-YYYY."""
    quarters = {}
    for order in orders:
        year, month = order["order_date"][:4], int(order["order_date"][5:7])
        label = f"Q{(month - 1) // 3 + 1}-{year}"
        entry = quarters.setdefault(label, {"total_orders": 0, "total_revenue": 0.0, "delivered": 0})
        entry["total_orders"] += 1
        entry["total_revenue"] += order["total_value"]
        if order["status"] == "Delivered":
            entry["delivered"] += 1
    return quarters


class TestQuarterlyReports:
    """Test suite for GET /api/reports/quarterly."""

    def test_get_quarterly_reports(self, client):
        """Unfiltered request returns all four 2025 quarters, sorted."""
        response = client.get("/api/reports/quarterly")
        assert response.status_code == 200

        data = response.json()
        assert [q["quarter"] for q in data] == ["Q1-2025", "Q2-2025", "Q3-2025", "Q4-2025"]
        for q in data:
            for field in ("total_orders", "total_revenue", "avg_order_value", "fulfillment_rate"):
                assert field in q

    def test_quarterly_totals_match_orders(self, client):
        """Quarter order counts add up to the total order count."""
        orders = client.get("/api/orders").json()
        data = client.get("/api/reports/quarterly").json()
        assert sum(q["total_orders"] for q in data) == len(orders)

    def test_quarterly_revenue_is_rounded_to_cents(self, client):
        """Revenue sums must not leak float noise like 794529.2899999998."""
        data = client.get("/api/reports/quarterly").json()
        for q in data:
            assert q["total_revenue"] == round(q["total_revenue"], 2)

    @pytest.mark.parametrize("query", FILTER_CASES)
    def test_quarterly_reports_respect_filters(self, client, query):
        """Each filter (alone and combined) narrows the quarterly aggregate like /api/orders."""
        orders = client.get(f"/api/orders?{query}").json()
        expected = _expected_quarterly(orders)

        response = client.get(f"/api/reports/quarterly?{query}")
        assert response.status_code == 200
        data = response.json()

        assert {q["quarter"] for q in data} == set(expected)
        for q in data:
            exp = expected[q["quarter"]]
            assert q["total_orders"] == exp["total_orders"]
            assert abs(q["total_revenue"] - exp["total_revenue"]) < 0.01
            assert q["fulfillment_rate"] == round(exp["delivered"] / exp["total_orders"] * 100, 1)

    def test_quarterly_reports_no_matches(self, client):
        """A filter combination with no orders returns an empty list, not an error."""
        response = client.get("/api/reports/quarterly?warehouse=Nowhere")
        assert response.status_code == 200
        assert response.json() == []


class TestMonthlyTrends:
    """Test suite for GET /api/reports/monthly-trends."""

    def test_get_monthly_trends(self, client):
        """Unfiltered request returns 12 months of 2025, sorted ascending."""
        response = client.get("/api/reports/monthly-trends")
        assert response.status_code == 200

        data = response.json()
        assert [m["month"] for m in data] == [f"2025-{i:02d}" for i in range(1, 13)]
        for m in data:
            for field in ("month", "order_count", "revenue", "delivered_count"):
                assert field in m

    def test_monthly_revenue_is_rounded_to_cents(self, client):
        """Revenue sums must not leak float noise."""
        data = client.get("/api/reports/monthly-trends").json()
        for m in data:
            assert m["revenue"] == round(m["revenue"], 2)

    @pytest.mark.parametrize("query", FILTER_CASES)
    def test_monthly_trends_respect_filters(self, client, query):
        """Each filter (alone and combined) narrows the monthly aggregate like /api/orders."""
        orders = client.get(f"/api/orders?{query}").json()
        expected = _expected_monthly(orders)

        response = client.get(f"/api/reports/monthly-trends?{query}")
        assert response.status_code == 200
        data = response.json()

        assert {m["month"] for m in data} == set(expected)
        for m in data:
            exp = expected[m["month"]]
            assert m["order_count"] == exp["order_count"]
            assert m["delivered_count"] == exp["delivered_count"]
            assert abs(m["revenue"] - exp["revenue"]) < 0.01

    def test_monthly_trends_no_matches(self, client):
        """A filter combination with no orders returns an empty list, not an error."""
        response = client.get("/api/reports/monthly-trends?warehouse=Nowhere")
        assert response.status_code == 200
        assert response.json() == []
