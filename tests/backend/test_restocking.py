"""
Tests for the restocking feature: forecast pricing data, recommendation
algorithm, and restock order endpoints.
"""
import pytest

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
