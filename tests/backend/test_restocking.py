"""
Tests for the restocking feature: forecast pricing data, recommendation
algorithm, and restock order endpoints.
"""
from datetime import datetime

import pytest

import main
from mock_data import demand_forecasts
from restocking import (
    RestockValidationError,
    build_restock_order,
    rank_forecasts,
    recommend_restock,
)

FIXED_NOW = datetime(2026, 11, 12, 14, 3, 27, 123456)


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """The order store is module-level state; isolate every test."""
    main.restock_orders.clear()
    yield
    main.restock_orders.clear()

# Expected (unit_cost, lead_time_days) per SKU. Source: spec section 1.
EXPECTED_PRICING = {
    "WDG-001": (24.99, 7),
    "BRG-102": (89.50, 14),
    "GSK-203": (12.75, 5),
    "MTR-304": (445.00, 21),
    "FLT-405": (8.25, 5),
    "VLV-506": (156.00, 18),
    "PSU-501": (18.99, 10),
    "SNR-420": (38.50, 12),
    "CTL-330": (215.00, 15),
}


class TestForecastPricingData:
    """Demand forecasts carry the cost and lead time restocking needs."""

    def test_demand_endpoint_returns_cost_and_lead_time(self, client):
        response = client.get("/api/demand")
        assert response.status_code == 200
        for forecast in response.json():
            assert isinstance(forecast["unit_cost"], float)
            assert forecast["unit_cost"] > 0
            assert isinstance(forecast["lead_time_days"], int)
            assert forecast["lead_time_days"] > 0

    def test_pricing_matches_spec(self, client):
        data = client.get("/api/demand").json()
        actual = {f["item_sku"]: (f["unit_cost"], f["lead_time_days"]) for f in data}
        assert actual == EXPECTED_PRICING


MAX_BUDGET = 107220.48
SPEC_RANK_ORDER = [
    "WDG-001", "GSK-203", "FLT-405",                        # increasing
    "BRG-102", "SNR-420", "CTL-330", "VLV-506", "PSU-501",  # stable
    "MTR-304",                                              # decreasing
]


def make_forecast(sku, trend="stable", current=100, forecasted=100,
                  unit_cost=10.0, lead_time_days=5):
    """Minimal forecast dict for focused unit tests."""
    return {
        "id": sku, "item_sku": sku, "item_name": f"Item {sku}",
        "current_demand": current, "forecasted_demand": forecasted,
        "trend": trend, "period": "Next 30 days",
        "unit_cost": unit_cost, "lead_time_days": lead_time_days,
    }


class TestRankForecasts:

    def test_rank_order_matches_spec(self):
        ranked = rank_forecasts(demand_forecasts)
        assert [f["item_sku"] for f in ranked] == SPEC_RANK_ORDER

    def test_rank_does_not_mutate_input(self):
        original = [f["item_sku"] for f in demand_forecasts]
        rank_forecasts(demand_forecasts)
        assert [f["item_sku"] for f in demand_forecasts] == original

    def test_ties_broken_by_sku(self):
        forecasts = [make_forecast("B-2"), make_forecast("A-1")]
        assert [f["item_sku"] for f in rank_forecasts(forecasts)] == ["A-1", "B-2"]

    def test_zero_current_demand_does_not_crash(self):
        forecasts = [make_forecast("Z-1", current=0, forecasted=50)]
        assert len(rank_forecasts(forecasts)) == 1


class TestRecommendRestock:

    def test_zero_budget_returns_no_items(self):
        result = recommend_restock(demand_forecasts, 0)
        assert result["items"] == []
        assert result["total_cost"] == 0
        assert result["remaining_budget"] == 0
        assert result["lead_time_days"] == 0
        assert result["max_budget"] == MAX_BUDGET

    def test_worked_example_30000(self):
        result = recommend_restock(demand_forecasts, 30000)
        lines = [(i["sku"], i["quantity"], i["partial"]) for i in result["items"]]
        assert lines == [
            ("WDG-001", 450, False),
            ("GSK-203", 600, False),
            ("FLT-405", 950, False),
            ("BRG-102", 36, True),
        ]
        assert result["total_cost"] == 29955.0
        assert result["remaining_budget"] == 45.0
        assert result["lead_time_days"] == 14
        assert result["budget"] == 30000.0

    def test_exact_fit_has_no_partial_item(self):
        # Budget equals WDG-001's full line cost (450 x 24.99), so the
        # remaining budget is 0 and no partial GSK-203 line is added.
        result = recommend_restock(demand_forecasts, 11245.50)
        assert [(i["sku"], i["partial"]) for i in result["items"]] == [("WDG-001", False)]
        assert result["remaining_budget"] == 0.0

    @pytest.mark.parametrize("budget", [MAX_BUDGET, 200000])
    def test_budget_at_or_above_max_restocks_everything(self, budget):
        result = recommend_restock(demand_forecasts, budget)
        assert len(result["items"]) == 9
        assert not any(i["partial"] for i in result["items"])
        assert result["total_cost"] == MAX_BUDGET
        assert result["lead_time_days"] == 21

    def test_partial_line_skipped_when_less_than_one_unit_fits(self):
        forecasts = [
            make_forecast("A-1", trend="increasing", forecasted=10, unit_cost=1.0),
            make_forecast("B-2", trend="stable", forecasted=10, unit_cost=10.0),
            make_forecast("C-3", trend="decreasing", forecasted=1, unit_cost=1.0),
        ]
        # A-1 costs 10, leaving 5: less than one B-2 unit, and filling stops
        # at B-2, so C-3 is not considered even though it would fit.
        result = recommend_restock(forecasts, 15)
        assert [i["sku"] for i in result["items"]] == ["A-1"]
        assert result["remaining_budget"] == 5.0

    def test_item_fields(self):
        item = recommend_restock(demand_forecasts, 30000)["items"][0]
        assert item == {
            "sku": "WDG-001",
            "name": "Industrial Widget Type A",
            "trend": "increasing",
            "quantity": 450,
            "forecasted_demand": 450,
            "unit_cost": 24.99,
            "line_total": 11245.5,
            "lead_time_days": 7,
            "partial": False,
        }


class TestRecommendationsEndpoint:

    def test_returns_worked_example(self, client):
        response = client.get("/api/restock/recommendations?budget=30000")
        assert response.status_code == 200
        data = response.json()
        assert [i["sku"] for i in data["items"]] == ["WDG-001", "GSK-203", "FLT-405", "BRG-102"]
        assert data["max_budget"] == MAX_BUDGET
        assert data["total_cost"] == 29955.0

    def test_missing_budget_is_422(self, client):
        assert client.get("/api/restock/recommendations").status_code == 422

    def test_negative_budget_is_422(self, client):
        assert client.get("/api/restock/recommendations?budget=-1").status_code == 422

    @pytest.mark.parametrize("budget", ["inf", "nan", "1e308"])
    def test_non_finite_or_oversized_budget_is_422(self, client, budget):
        # Without the upper bound these reach the cents conversion and 500.
        response = client.get(f"/api/restock/recommendations?budget={budget}")
        assert response.status_code == 422


class TestBuildRestockOrder:

    def test_prices_lines_from_server_data(self):
        order = build_restock_order(
            demand_forecasts, 30000,
            [{"sku": "WDG-001", "quantity": 450}, {"sku": "BRG-102", "quantity": 36}],
            order_id=1, now=FIXED_NOW,
        )
        assert order["id"] == "1"
        assert order["order_number"] == "RST-0001"
        assert order["status"] == "Submitted"
        assert order["budget"] == 30000.0
        assert order["total_cost"] == 14467.5          # 11245.50 + 3222.00
        assert order["lead_time_days"] == 14           # max(7, 14)
        assert order["submitted_at"] == "2026-11-12T14:03:27"
        assert order["expected_delivery"] == "2026-11-26T14:03:27"
        assert order["items"] == [
            {"sku": "WDG-001", "name": "Industrial Widget Type A", "quantity": 450,
             "unit_cost": 24.99, "line_total": 11245.5, "lead_time_days": 7},
            {"sku": "BRG-102", "name": "Steel Bearing Assembly", "quantity": 36,
             "unit_cost": 89.5, "line_total": 3222.0, "lead_time_days": 14},
        ]

    def test_order_number_is_zero_padded(self):
        order = build_restock_order(
            demand_forecasts, 100, [{"sku": "FLT-405", "quantity": 1}],
            order_id=12, now=FIXED_NOW,
        )
        assert order["order_number"] == "RST-0012"

    @pytest.mark.parametrize("budget, items, message", [
        (1000, [], "at least one item"),
        (1000, [{"sku": "NOPE-1", "quantity": 1}], "Unknown SKU: NOPE-1"),
        (1000, [{"sku": "FLT-405", "quantity": 1}, {"sku": "FLT-405", "quantity": 1}],
         "Duplicate SKU: FLT-405"),
        (1000, [{"sku": "FLT-405", "quantity": 0}], "between 1 and 950"),
        (100000, [{"sku": "FLT-405", "quantity": 951}], "between 1 and 950"),
        (100, [{"sku": "MTR-304", "quantity": 1}], "exceeds budget"),
    ])
    def test_validation_errors(self, budget, items, message):
        with pytest.raises(RestockValidationError, match=message):
            build_restock_order(demand_forecasts, budget, items, order_id=1, now=FIXED_NOW)


class TestRestockOrderEndpoints:

    def test_list_is_empty_initially(self, client):
        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_returns_201_with_server_pricing(self, client):
        response = client.post("/api/restock-orders", json={
            "budget": 30000,
            "items": [{"sku": "WDG-001", "quantity": 450}, {"sku": "BRG-102", "quantity": 36}],
        })
        assert response.status_code == 201
        order = response.json()
        assert order["order_number"] == "RST-0001"
        assert order["status"] == "Submitted"
        assert order["total_cost"] == 14467.5
        assert order["lead_time_days"] == 14
        submitted = datetime.fromisoformat(order["submitted_at"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert (expected - submitted).days == 14
        assert submitted.microsecond == 0

    def test_recommendation_round_trip_is_accepted(self, client):
        """Submitting exactly what the recommendations endpoint returned succeeds."""
        rec = client.get("/api/restock/recommendations?budget=30000").json()
        response = client.post("/api/restock-orders", json={
            "budget": rec["budget"],
            "items": [{"sku": i["sku"], "quantity": i["quantity"]} for i in rec["items"]],
        })
        assert response.status_code == 201
        assert response.json()["total_cost"] == rec["total_cost"]

    def test_list_is_newest_first_with_sequential_numbers(self, client):
        for _ in range(2):
            client.post("/api/restock-orders", json={
                "budget": 100, "items": [{"sku": "FLT-405", "quantity": 1}],
            })
        numbers = [o["order_number"] for o in client.get("/api/restock-orders").json()]
        assert numbers == ["RST-0002", "RST-0001"]

    @pytest.mark.parametrize("payload", [
        {"budget": 1000, "items": []},
        {"budget": 1000, "items": [{"sku": "NOPE-1", "quantity": 1}]},
        {"budget": 1000, "items": [{"sku": "FLT-405", "quantity": 1},
                                   {"sku": "FLT-405", "quantity": 1}]},
        {"budget": 1000, "items": [{"sku": "FLT-405", "quantity": 0}]},
        {"budget": 100, "items": [{"sku": "MTR-304", "quantity": 1}]},
    ])
    def test_invalid_orders_are_400_and_not_stored(self, client, payload):
        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 400
        assert isinstance(response.json()["detail"], str)
        assert client.get("/api/restock-orders").json() == []

    def test_negative_budget_is_422(self, client):
        response = client.post("/api/restock-orders", json={
            "budget": -1, "items": [{"sku": "FLT-405", "quantity": 1}],
        })
        assert response.status_code == 422

    def test_oversized_budget_is_422(self, client):
        # Same cap as the recommendations endpoint: keeps huge values out of the cents conversion.
        response = client.post("/api/restock-orders", json={
            "budget": 1e12, "items": [{"sku": "FLT-405", "quantity": 1}],
        })
        assert response.status_code == 422
