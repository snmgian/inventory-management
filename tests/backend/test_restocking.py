"""
Tests for the restocking feature: forecast pricing data, recommendation
algorithm, and restock order endpoints.
"""
import pytest

from mock_data import demand_forecasts
from restocking import rank_forecasts, recommend_restock

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
