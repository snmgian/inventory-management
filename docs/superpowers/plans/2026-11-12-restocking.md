# Restocking Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Restocking tab where a budget slider drives server-computed restock recommendations from the demand forecast. A Place Order button submits an order, which then appears in a new Submitted Orders section of the Orders tab with its delivery lead time.

**Architecture:** Pure Python logic in a new `server/restocking.py` handles ranking, budget fill, and order validation and pricing. Thin FastAPI routes in `server/main.py` expose it and keep submitted orders in an in-memory list. A new `Restocking.vue` view calls the recommendations endpoint with a debounce, and `Orders.vue` lists submitted orders.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, pytest with FastAPI TestClient, Vue 3 (Composition API through `setup()`), axios, Vite.

**Spec:** `docs/superpowers/specs/2026-11-12-restocking-design.md`. Read it before starting any task.

## Global Constraints

- Money in the backend is computed in **integer cents** and returned as floats rounded to 2 decimals.
- `max_budget` for the shipped data is **107220.48**.
- Rank order is **trend** (`increasing` < `stable` < `decreasing`), then **growth %** (highest first), then **`item_sku`** (ascending).
- Fill stops after the **first item that doesn't fit in full**. That item is included with `partial: true` only if at least 1 unit fits.
- Order numbers are **`RST-0001`, `RST-0002`, ...**, counted within each server run. Status is always **`"Submitted"`**.
- `submitted_at` and `expected_delivery` are ISO 8601 **without microseconds**. `expected_delivery = submitted_at + lead_time_days`.
- POST validation failures return **400**. A negative or missing budget returns **422** (FastAPI validation).
- The slider step is **$500**. The slider max is `max_budget` rounded **up** to the step ($107,500). It starts at 50%, and recommendations are refetched **200 ms** after the slider stops moving.
- The Restocking tab and the Submitted Orders section **ignore the global filters**.
- **No emojis in UI.** Use the slate palette (`#0f172a`, `#64748b`, `#e2e8f0`) and the existing `card`, `stat-card` and `badge` classes.
- Every user-visible string goes in **both** `client/src/locales/en.js` and `ja.js`.
- **Document non-obvious logic changes with comments** (root CLAUDE.md).
- **All `.vue` creation or significant edits must be delegated to the `vue-expert` agent** (root CLAUDE.md).
- Use unique `v-for` keys (`sku`, `id`), never the index.
- Backend test command: `cd tests && uv run --project ../server pytest backend/ -v`
- Only stage the files listed in each task. The working tree also has unrelated uncommitted changes (`CLAUDE.md`, `docs/architecture.html`) that must **not** be included.
- Every commit message ends with these trailer lines:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_018sN4YGcMVKshYmTxnpYchQ
  ```

## File Map

| File | Action | Responsibility |
|---|---|---|
| `server/data/demand_forecasts.json` | Modify | Add `unit_cost` and `lead_time_days` to every record |
| `server/restocking.py` | Create | Pure functions: `rank_forecasts`, `recommend_restock`, `build_restock_order`, plus `RestockValidationError` |
| `server/main.py` | Modify | `DemandForecast` fields, restock Pydantic models, 3 routes, `restock_orders` list |
| `tests/backend/test_restocking.py` | Create | Unit and API tests for everything above |
| `client/src/api.js` | Modify | `getRestockRecommendations`, `createRestockOrder`, `getRestockOrders` |
| `client/src/locales/en.js`, `ja.js` | Modify | `nav.restocking`, `restocking.*`, `orders.submittedOrders`, new `orders.table.*` keys, `status.submitted` |
| `client/src/views/Restocking.vue` | Create | Slider, recommendations table, summary, Place Order |
| `client/src/main.js` | Modify | `/restocking` route |
| `client/src/App.vue` | Modify | Nav link after Demand Forecast |
| `client/src/views/Orders.vue` | Modify | Submitted Orders card |

---

### Task 1: Forecast cost and lead-time data

**Files:**
- Modify: `server/data/demand_forecasts.json`
- Modify: `server/main.py`, the `DemandForecast` class at about lines 83-90
- Create: `tests/backend/test_restocking.py`

**Interfaces:**
- Produces: every forecast dict has `unit_cost: float` and `lead_time_days: int`, and `GET /api/demand` returns both.

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_restocking.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd tests && uv run --project ../server pytest backend/test_restocking.py -v`
Expected: both tests FAIL with `KeyError: 'unit_cost'`.

- [ ] **Step 3: Add the data fields**

In `server/data/demand_forecasts.json`, add the two fields to each record after `"period"`. Set values by `item_sku`: WDG-001 `24.99`/`7`, BRG-102 `89.50`/`14`, GSK-203 `12.75`/`5`, MTR-304 `445.00`/`21`, FLT-405 `8.25`/`5`, VLV-506 `156.00`/`18`, PSU-501 `18.99`/`10`, SNR-420 `38.50`/`12`, CTL-330 `215.00`/`15`. Use a script so no record is missed:

```bash
cd server && uv run python - <<'EOF'
import json
pricing = {
    "WDG-001": (24.99, 7), "BRG-102": (89.50, 14), "GSK-203": (12.75, 5),
    "MTR-304": (445.00, 21), "FLT-405": (8.25, 5), "VLV-506": (156.00, 18),
    "PSU-501": (18.99, 10), "SNR-420": (38.50, 12), "CTL-330": (215.00, 15),
}
path = "data/demand_forecasts.json"
data = json.load(open(path))
for f in data:
    f["unit_cost"], f["lead_time_days"] = pricing[f["item_sku"]]
json.dump(data, open(path, "w"), indent=2)
open(path, "a").write("\n")
EOF
```

Then in `server/main.py`, extend `DemandForecast`:

```python
class DemandForecast(BaseModel):
    id: str
    item_sku: str
    item_name: str
    current_demand: int
    forecasted_demand: int
    trend: str
    period: str
    unit_cost: float
    lead_time_days: int
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd tests && uv run --project ../server pytest backend/ -v`
Expected: all tests PASS (40 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add server/data/demand_forecasts.json server/main.py tests/backend/test_restocking.py
git commit -F - <<'EOF'
Add unit cost and lead time to demand forecasts

Restocking needs a price and supplier lead time per forecast SKU; most
forecast SKUs have no inventory record to borrow them from.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018sN4YGcMVKshYmTxnpYchQ
EOF
```

---

### Task 2: Recommendation algorithm and endpoint

**Files:**
- Create: `server/restocking.py`
- Modify: `server/main.py` (imports, models, one route)
- Test: `tests/backend/test_restocking.py`

**Interfaces:**
- Consumes: forecast dicts with `item_sku, item_name, current_demand, forecasted_demand, trend, unit_cost, lead_time_days` (from Task 1).
- Produces:
  - `restocking.rank_forecasts(forecasts: list[dict]) -> list[dict]`
  - `restocking.recommend_restock(forecasts: list[dict], budget: float) -> dict`, which returns the keys `budget, max_budget, total_cost, remaining_budget, lead_time_days, items`. Each item has `sku, name, trend, quantity, forecasted_demand, unit_cost, line_total, lead_time_days, partial`.
  - `restocking._cents(amount: float) -> int` and `restocking._dollars(cents: int) -> float`, which Task 3 reuses.
  - `GET /api/restock/recommendations?budget=<float>` returns `RestockRecommendation`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/backend/test_restocking.py`:

```python
from mock_data import demand_forecasts
from restocking import rank_forecasts, recommend_restock

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd tests && uv run --project ../server pytest backend/test_restocking.py -v`
Expected: collection ERROR with `ModuleNotFoundError: No module named 'restocking'`.

- [ ] **Step 3: Create `server/restocking.py`**

```python
"""
Restocking recommendation and order-building logic.

Pure functions only (no I/O, no global state) so they can be unit-tested
directly. main.py owns the HTTP layer and the in-memory order list.

Money is handled in integer cents internally: summing float dollar amounts
(e.g. 450 * 24.99) accumulates rounding error, which would make "does this
line fit in the remaining budget?" comparisons flaky at exact-fit budgets.
"""
from typing import List

# Lower rank is restocked first: rising demand is the most urgent to cover.
TREND_RANK = {"increasing": 0, "stable": 1, "decreasing": 2}


def _cents(amount: float) -> int:
    return int(round(amount * 100))


def _dollars(cents: int) -> float:
    return round(cents / 100, 2)


def _growth(forecast: dict) -> float:
    """Fractional demand growth, e.g. 0.5 for 300 -> 450. 0 if current is 0."""
    current = forecast["current_demand"]
    if current <= 0:
        return 0.0
    return (forecast["forecasted_demand"] - current) / current


def rank_forecasts(forecasts: List[dict]) -> List[dict]:
    """Return forecasts in restock priority order without mutating the input.

    Order: trend (increasing, stable, decreasing), then growth % descending,
    then SKU ascending so ties are deterministic.
    """
    return sorted(
        forecasts,
        key=lambda f: (
            # Unknown trends sort after all known ones.
            TREND_RANK.get(f["trend"].lower(), len(TREND_RANK)),
            -_growth(f),
            f["item_sku"],
        ),
    )


def recommend_restock(forecasts: List[dict], budget: float) -> dict:
    """Greedy priority fill of forecast items within the budget.

    Each ranked item is added at its full forecasted quantity while it fits.
    The first item that does not fit gets as many whole units as the remaining
    budget allows (marked partial, only if at least one unit fits), and
    filling STOPS there, even if cheaper lower-priority items would still
    fit. This keeps the recommendation strictly in priority order.
    """
    remaining = _cents(budget)
    items = []

    for f in rank_forecasts(forecasts):
        unit = _cents(f["unit_cost"])
        full_qty = f["forecasted_demand"]

        if full_qty * unit <= remaining:
            qty, partial = full_qty, False
        else:
            qty, partial = remaining // unit, True

        if qty >= 1:
            remaining -= qty * unit
            items.append({
                "sku": f["item_sku"],
                "name": f["item_name"],
                "trend": f["trend"],
                "quantity": qty,
                "forecasted_demand": full_qty,
                "unit_cost": f["unit_cost"],
                "line_total": _dollars(qty * unit),
                "lead_time_days": f["lead_time_days"],
                "partial": partial,
            })

        if partial:
            break

    max_cents = sum(f["forecasted_demand"] * _cents(f["unit_cost"]) for f in forecasts)
    return {
        "budget": _dollars(_cents(budget)),
        "max_budget": _dollars(max_cents),
        "total_cost": _dollars(_cents(budget) - remaining),
        "remaining_budget": _dollars(remaining),
        # The order arrives when its slowest item does.
        "lead_time_days": max((i["lead_time_days"] for i in items), default=0),
        "items": items,
    }
```

- [ ] **Step 4: Add models and the route to `server/main.py`**

Change the `fastapi` import line and add the restocking import below the `mock_data` import:

```python
from fastapi import FastAPI, HTTPException, Query
```

```python
from restocking import recommend_restock
```

Add these models after `CreatePurchaseOrderRequest`:

```python
class RestockRecommendationItem(BaseModel):
    sku: str
    name: str
    trend: str
    quantity: int
    forecasted_demand: int
    unit_cost: float
    line_total: float
    lead_time_days: int
    partial: bool

class RestockRecommendation(BaseModel):
    budget: float
    max_budget: float
    total_cost: float
    remaining_budget: float
    lead_time_days: int
    items: List[RestockRecommendationItem]
```

Add this route after `get_monthly_trends` and before `if __name__ == "__main__":`:

```python
@app.get("/api/restock/recommendations", response_model=RestockRecommendation)
def get_restock_recommendations(budget: float = Query(..., ge=0)):
    """Recommend forecast items to restock within the given budget (USD)."""
    return recommend_restock(demand_forecasts, budget)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd tests && uv run --project ../server pytest backend/ -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/restocking.py server/main.py tests/backend/test_restocking.py
git commit -F - <<'EOF'
Add restock recommendation algorithm and endpoint

Ranks forecasts by trend, then growth, and greedily fills the budget,
stopping after one partial line. Exposed as GET /api/restock/recommendations.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018sN4YGcMVKshYmTxnpYchQ
EOF
```

---

### Task 3: Restock order building and endpoints

**Files:**
- Modify: `server/restocking.py` (append)
- Modify: `server/main.py` (imports, state, models, two routes)
- Test: `tests/backend/test_restocking.py`

**Interfaces:**
- Consumes: `_cents`, `_dollars` (Task 2) and forecast dicts (Task 1).
- Produces:
  - `restocking.RestockValidationError(ValueError)`
  - `restocking.build_restock_order(forecasts: list[dict], budget: float, requested_items: list[dict], order_id: int, now: datetime) -> dict`, where `requested_items` holds `{sku, quantity}` dicts. It returns the keys `id, order_number, submitted_at, status, budget, total_cost, lead_time_days, expected_delivery, items`. Each item has `sku, name, quantity, unit_cost, line_total, lead_time_days`.
  - `main.restock_orders: List[dict]`, the module-level in-memory store.
  - `POST /api/restock-orders` takes `{budget: float >= 0, items: [{sku: str, quantity: int}]}` and returns 201 with `RestockOrder`, or 400 with `detail`.
  - `GET /api/restock-orders` returns `List[RestockOrder]`, newest first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/backend/test_restocking.py`:

```python
from datetime import datetime

import main
from restocking import RestockValidationError, build_restock_order

FIXED_NOW = datetime(2026, 11, 12, 14, 3, 27, 123456)


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """The order store is module-level state; isolate every test."""
    main.restock_orders.clear()
    yield
    main.restock_orders.clear()


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd tests && uv run --project ../server pytest backend/test_restocking.py -v`
Expected: collection ERROR with `ImportError: cannot import name 'RestockValidationError'`.

- [ ] **Step 3: Append to `server/restocking.py`**

Change the imports at the top of the file to:

```python
from datetime import datetime, timedelta
from typing import List
```

Append:

```python
class RestockValidationError(ValueError):
    """A submitted restock order is invalid. The API maps this to HTTP 400."""


def build_restock_order(forecasts: List[dict], budget: float,
                        requested_items: List[dict], order_id: int,
                        now: datetime) -> dict:
    """Validate a submitted restock order and price it from server data.

    Client-sent prices are never trusted: every line is re-priced from the
    forecast data, and the re-priced total must fit within the budget.
    `now` is injected so tests can assert exact timestamps.
    """
    if not requested_items:
        raise RestockValidationError("Order must contain at least one item")

    by_sku = {f["item_sku"]: f for f in forecasts}
    seen = set()
    lines = []
    total = 0

    for req in requested_items:
        sku, qty = req["sku"], req["quantity"]
        if sku in seen:
            raise RestockValidationError(f"Duplicate SKU: {sku}")
        seen.add(sku)

        forecast = by_sku.get(sku)
        if forecast is None:
            raise RestockValidationError(f"Unknown SKU: {sku}")

        max_qty = forecast["forecasted_demand"]
        if qty < 1 or qty > max_qty:
            raise RestockValidationError(
                f"Quantity for {sku} must be between 1 and {max_qty}")

        line_cents = qty * _cents(forecast["unit_cost"])
        total += line_cents
        lines.append({
            "sku": sku,
            "name": forecast["item_name"],
            "quantity": qty,
            "unit_cost": forecast["unit_cost"],
            "line_total": _dollars(line_cents),
            "lead_time_days": forecast["lead_time_days"],
        })

    if total > _cents(budget):
        raise RestockValidationError(
            f"Order total ${_dollars(total):,.2f} exceeds budget ${budget:,.2f}")

    submitted = now.replace(microsecond=0)
    # The order arrives when its slowest item does.
    lead_time = max(line["lead_time_days"] for line in lines)
    return {
        "id": str(order_id),
        "order_number": f"RST-{order_id:04d}",
        "submitted_at": submitted.isoformat(),
        "status": "Submitted",
        "budget": _dollars(_cents(budget)),
        "total_cost": _dollars(total),
        "lead_time_days": lead_time,
        "expected_delivery": (submitted + timedelta(days=lead_time)).isoformat(),
        "items": lines,
    }
```

- [ ] **Step 4: Wire up `server/main.py`**

Update the imports:

```python
from datetime import datetime
from pydantic import BaseModel, Field
from restocking import recommend_restock, build_restock_order, RestockValidationError
```

The second line replaces the existing `from pydantic import BaseModel`, and the third replaces the Task 2 restocking import.

Add the state right after the `QUARTER_MAP` block:

```python
# Submitted restock orders, newest last. In-memory only by design: like all
# other data in this demo they reset when the server restarts.
restock_orders: List[dict] = []
```

Add these models after `RestockRecommendation`:

```python
class RestockOrderItemRequest(BaseModel):
    sku: str
    quantity: int

class CreateRestockOrderRequest(BaseModel):
    budget: float = Field(ge=0)
    items: List[RestockOrderItemRequest]

class RestockOrderLine(BaseModel):
    sku: str
    name: str
    quantity: int
    unit_cost: float
    line_total: float
    lead_time_days: int

class RestockOrder(BaseModel):
    id: str
    order_number: str
    submitted_at: str
    status: str
    budget: float
    total_cost: float
    lead_time_days: int
    expected_delivery: str
    items: List[RestockOrderLine]
```

Add these routes after `get_restock_recommendations`:

```python
@app.post("/api/restock-orders", response_model=RestockOrder, status_code=201)
def create_restock_order(request: CreateRestockOrderRequest):
    """Submit a restock order. Lines are re-priced and validated server-side."""
    try:
        order = build_restock_order(
            demand_forecasts,
            request.budget,
            [item.model_dump() for item in request.items],
            order_id=len(restock_orders) + 1,
            now=datetime.now(),
        )
    except RestockValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    restock_orders.append(order)
    return order

@app.get("/api/restock-orders", response_model=List[RestockOrder])
def get_restock_orders():
    """List submitted restock orders, newest first."""
    return list(reversed(restock_orders))
```

- [ ] **Step 5: Run the full backend suite**

Run: `cd tests && uv run --project ../server pytest backend/ -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/restocking.py server/main.py tests/backend/test_restocking.py
git commit -F - <<'EOF'
Add restock order submission and listing endpoints

POST /api/restock-orders re-prices lines from server data and rejects
unknown/duplicate SKUs, out-of-range quantities, and over-budget totals.
Orders are kept in memory and listed newest first.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018sN4YGcMVKshYmTxnpYchQ
EOF
```

---

### Task 4: Restocking view (frontend)

**Delegate to the `vue-expert` agent.** Give it this task's full text plus the Global Constraints.

**Files:**
- Modify: `client/src/api.js`
- Modify: `client/src/locales/en.js`, `client/src/locales/ja.js`
- Create: `client/src/views/Restocking.vue`
- Modify: `client/src/main.js`
- Modify: `client/src/App.vue` (nav only)

**Interfaces:**
- Consumes: `GET /api/restock/recommendations` and `POST /api/restock-orders` (Tasks 2-3).
- Produces:
  - `api.getRestockRecommendations(budget: number) -> Promise<RestockRecommendation>`
  - `api.createRestockOrder({budget, items:[{sku, quantity}]}) -> Promise<RestockOrder>`
  - `api.getRestockOrders() -> Promise<RestockOrder[]>`
  - The locale keys `restocking.days` (`'{count} days'`) and `status.submitted`, which Task 5 reuses.

- [ ] **Step 1: Add the API methods**

In `client/src/api.js`, add these inside the `api` object after `getPurchaseOrderByBacklogItem` (add a comma after that method):

```javascript
  async getRestockRecommendations(budget) {
    const response = await axios.get(`${API_BASE_URL}/restock/recommendations`, {
      params: { budget }
    })
    return response.data
  },

  async createRestockOrder(orderData) {
    const response = await axios.post(`${API_BASE_URL}/restock-orders`, orderData)
    return response.data
  },

  async getRestockOrders() {
    const response = await axios.get(`${API_BASE_URL}/restock-orders`)
    return response.data
  }
```

- [ ] **Step 2: Add the locale strings**

`client/src/locales/en.js`:
- In `nav`, after `demandForecast: 'Demand Forecast',` add `restocking: 'Restocking',`
- In `status`, after `backordered: 'Backordered',` add `submitted: 'Submitted',`
- Add this new top-level block before `// Finance/Spending`:

```javascript
  // Restocking
  restocking: {
    title: 'Restocking',
    description: 'Set a budget to get restock recommendations from the demand forecast',
    budget: 'Available Budget',
    recommendedItems: 'Recommended Items',
    noItems: 'No items fit within this budget. Increase the budget to see recommendations.',
    totalCost: 'Total Cost',
    remainingBudget: 'Remaining Budget',
    leadTime: 'Order Lead Time',
    itemCount: 'Items',
    days: '{count} days',
    partial: 'Partial',
    placeOrder: 'Place Order',
    placingOrder: 'Placing Order...',
    orderPlaced: 'Order {orderNumber} placed.',
    viewInOrders: 'View in Orders',
    loadError: 'Failed to load recommendations',
    table: {
      sku: 'SKU',
      item: 'Item',
      trend: 'Trend',
      quantity: 'Quantity',
      unitCost: 'Unit Cost',
      lineTotal: 'Line Total',
      leadTime: 'Lead Time'
    }
  },
```

`client/src/locales/ja.js`. Put each key in the same position as in `en.js`:
- `nav`: `restocking: '補充',`
- `status`: `submitted: '提出済み',`
- New block:

```javascript
  // Restocking
  restocking: {
    title: '在庫補充',
    description: '予算を設定して需要予測に基づく補充推奨を表示します',
    budget: '利用可能な予算',
    recommendedItems: '推奨品目',
    noItems: 'この予算内に収まる品目はありません。予算を増やすと推奨が表示されます。',
    totalCost: '合計金額',
    remainingBudget: '残り予算',
    leadTime: '注文リードタイム',
    itemCount: '品目数',
    days: '{count}日',
    partial: '一部',
    placeOrder: '注文する',
    placingOrder: '注文中...',
    orderPlaced: '注文 {orderNumber} を受け付けました。',
    viewInOrders: '注文で確認',
    loadError: '推奨の読み込みに失敗しました',
    table: {
      sku: 'SKU',
      item: '品目',
      trend: '傾向',
      quantity: '数量',
      unitCost: '単価',
      lineTotal: '小計',
      leadTime: 'リードタイム'
    }
  },
```

- [ ] **Step 3: Create `client/src/views/Restocking.vue`**

```vue
<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="initialLoading" class="loading">{{ t('common.loading') }}</div>
    <div v-else>
      <div class="card budget-card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget') }}</h3>
          <span class="budget-value">{{ formatMoney(budget) }}</span>
        </div>
        <input
          v-model.number="budget"
          type="range"
          class="budget-slider"
          min="0"
          :max="sliderMax"
          :step="BUDGET_STEP"
          :aria-label="t('restocking.budget')"
        />
        <div class="slider-scale">
          <span>{{ formatMoney(0) }}</span>
          <span>{{ formatMoney(sliderMax) }}</span>
        </div>
      </div>

      <div v-if="error" class="error">{{ error }}</div>

      <div class="stats-grid">
        <div class="stat-card info">
          <div class="stat-label">{{ t('restocking.totalCost') }}</div>
          <div class="stat-value">{{ formatMoney(recommendation?.total_cost ?? 0) }}</div>
        </div>
        <div class="stat-card success">
          <div class="stat-label">{{ t('restocking.remainingBudget') }}</div>
          <div class="stat-value">{{ formatMoney(recommendation?.remaining_budget ?? 0) }}</div>
        </div>
        <div class="stat-card warning">
          <div class="stat-label">{{ t('restocking.leadTime') }}</div>
          <div class="stat-value">{{ t('restocking.days', { count: recommendation?.lead_time_days ?? 0 }) }}</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">{{ t('restocking.itemCount') }}</div>
          <div class="stat-value">{{ items.length }}</div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }} ({{ items.length }})</h3>
        </div>

        <div v-if="items.length === 0" class="empty-state">{{ t('restocking.noItems') }}</div>
        <div v-else class="table-container">
          <table>
            <thead>
              <tr>
                <th>{{ t('restocking.table.sku') }}</th>
                <th>{{ t('restocking.table.item') }}</th>
                <th>{{ t('restocking.table.trend') }}</th>
                <th class="num">{{ t('restocking.table.quantity') }}</th>
                <th class="num">{{ t('restocking.table.unitCost') }}</th>
                <th class="num">{{ t('restocking.table.lineTotal') }}</th>
                <th class="num">{{ t('restocking.table.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.sku">
                <td><strong>{{ item.sku }}</strong></td>
                <td>{{ translateProductName(item.name) }}</td>
                <td><span :class="['badge', item.trend]">{{ t(`trends.${item.trend}`) }}</span></td>
                <td class="num">
                  {{ item.quantity.toLocaleString() }}
                  <template v-if="item.partial">
                    / {{ item.forecasted_demand.toLocaleString() }}
                    <span class="badge warning">{{ t('restocking.partial') }}</span>
                  </template>
                </td>
                <td class="num">{{ formatUnitCost(item.unit_cost) }}</td>
                <td class="num"><strong>{{ formatMoney(item.line_total) }}</strong></td>
                <td class="num">{{ t('restocking.days', { count: item.lead_time_days }) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="order-actions">
          <div v-if="submitError" class="error">{{ submitError }}</div>
          <div v-if="lastOrder" class="success-message">
            {{ t('restocking.orderPlaced', { orderNumber: lastOrder.order_number }) }}
            <router-link to="/orders">{{ t('restocking.viewInOrders') }}</router-link>
          </div>
          <button class="place-order-btn" :disabled="!canPlaceOrder" @click="placeOrder">
            {{ submitting ? t('restocking.placingOrder') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

// Slider granularity in USD. The slider max is rounded UP to a multiple of
// this so a full restock (max_budget) is always reachable.
const BUDGET_STEP = 500
// Wait this long after the slider stops moving before asking the server.
const DEBOUNCE_MS = 200

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, translateProductName } = useI18n()

    const initialLoading = ref(true)
    const error = ref(null)
    const budget = ref(0)
    const maxBudget = ref(0)
    const recommendation = ref(null)
    const submitting = ref(false)
    const submitError = ref(null)
    const lastOrder = ref(null)

    let debounceTimer = null
    // Incremented per request. Responses from older requests are dropped so a
    // slow response cannot overwrite a newer one while the slider moves.
    let requestSeq = 0

    const sliderMax = computed(() => Math.ceil(maxBudget.value / BUDGET_STEP) * BUDGET_STEP)
    const items = computed(() => recommendation.value?.items ?? [])
    const canPlaceOrder = computed(() => items.value.length > 0 && !submitting.value)

    const formatMoney = (amount) => formatCurrency(amount, currentCurrency.value)
    const formatUnitCost = (amount) => formatCurrencyWithDecimals(amount, currentCurrency.value, 2)

    const fetchRecommendations = async (value) => {
      const seq = ++requestSeq
      try {
        const data = await api.getRestockRecommendations(value)
        if (seq !== requestSeq) return
        recommendation.value = data
        error.value = null
      } catch (err) {
        if (seq !== requestSeq) return
        error.value = `${t('restocking.loadError')}: ${err.message}`
        console.error('Restock recommendations error:', err)
      }
    }

    const init = async () => {
      try {
        // budget=0 is a cheap call whose max_budget sizes the slider.
        const probe = await api.getRestockRecommendations(0)
        maxBudget.value = probe.max_budget
        budget.value = Math.round(sliderMax.value / 2 / BUDGET_STEP) * BUDGET_STEP
        await fetchRecommendations(budget.value)
      } catch (err) {
        error.value = `${t('restocking.loadError')}: ${err.message}`
        console.error('Restock init error:', err)
      } finally {
        initialLoading.value = false
      }
    }

    watch(budget, (value) => {
      // init() sets the starting budget and fetches directly; skip that change.
      if (initialLoading.value) return
      submitError.value = null
      lastOrder.value = null
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => fetchRecommendations(value), DEBOUNCE_MS)
    })

    const placeOrder = async () => {
      if (!canPlaceOrder.value) return
      submitting.value = true
      submitError.value = null
      try {
        lastOrder.value = await api.createRestockOrder({
          // Send the budget the displayed items were computed for, not the
          // live slider value: a debounced refetch may still be pending.
          budget: recommendation.value.budget,
          items: items.value.map(item => ({ sku: item.sku, quantity: item.quantity }))
        })
      } catch (err) {
        // Prefer the server's 400 message; 422 details are arrays, so fall back.
        const detail = err.response?.data?.detail
        submitError.value = typeof detail === 'string' ? detail : err.message
        console.error('Place restock order error:', err)
      } finally {
        submitting.value = false
      }
    }

    onMounted(init)
    onBeforeUnmount(() => clearTimeout(debounceTimer))

    return {
      t,
      BUDGET_STEP,
      initialLoading,
      error,
      budget,
      sliderMax,
      recommendation,
      items,
      submitting,
      submitError,
      lastOrder,
      canPlaceOrder,
      formatMoney,
      formatUnitCost,
      translateProductName,
      placeOrder
    }
  }
}
</script>

<style scoped>
.budget-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #0f172a;
  font-variant-numeric: tabular-nums;
}

.budget-slider {
  width: 100%;
  accent-color: #0f172a;
  cursor: pointer;
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 0.25rem;
  font-size: 0.813rem;
  color: #64748b;
}

.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.empty-state {
  padding: 2rem;
  text-align: center;
  color: #64748b;
}

.order-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 1rem;
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid #e2e8f0;
}

.order-actions .error {
  margin: 0;
  flex: 1;
}

.success-message {
  color: #15803d;
  font-size: 0.875rem;
}

.success-message a {
  margin-left: 0.5rem;
  color: #2563eb;
  font-weight: 600;
}

.place-order-btn {
  background: #0f172a;
  color: #ffffff;
  border: none;
  border-radius: 8px;
  padding: 0.625rem 1.25rem;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
}

.place-order-btn:hover:not(:disabled) {
  background: #1e293b;
}

.place-order-btn:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}
</style>
```

- [ ] **Step 4: Register the route and nav link**

`client/src/main.js`: add `import Restocking from './views/Restocking.vue'` after the `Demand` import, and add `{ path: '/restocking', component: Restocking },` after the `/demand` route.

`client/src/App.vue`: insert this after the `/demand` `router-link` block:

```vue
          <router-link to="/restocking" :class="{ active: $route.path === '/restocking' }">
            {{ t('nav.restocking') }}
          </router-link>
```

- [ ] **Step 5: Verify the build and the page**

Run: `cd client && npm run build`
Expected: the build succeeds with no errors.

Restart the backend first, because it doesn't reload code changes (uvicorn runs without `reload`): `lsof -ti:8001 | xargs kill -9 2>/dev/null || true`, then run `cd server && uv run python main.py` in the background. Vite hot-reloads, so the frontend doesn't need a restart.

Use Playwright MCP to open `http://localhost:3000/restocking` and check:
- the slider starts at $54,000 (half of $107,500, rounded to the $500 step) and the table lists items;
- dragging to $30,000 shows WDG-001, GSK-203, FLT-405, and BRG-102 with "36 / 152" and a Partial badge;
- dragging to 0 shows the empty state and a disabled button;
- the browser console has no errors.

- [ ] **Step 6: Commit**

```bash
git add client/src/api.js client/src/locales/en.js client/src/locales/ja.js client/src/views/Restocking.vue client/src/main.js client/src/App.vue
git commit -F - <<'EOF'
Add Restocking tab with budget slider and order placement

Debounced slider fetches server-side recommendations; Place Order submits
the displayed lines. Adds en/ja strings and nav entry.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018sN4YGcMVKshYmTxnpYchQ
EOF
```

---

### Task 5: Submitted Orders section in the Orders tab

**Delegate to the `vue-expert` agent.** Give it this task's full text plus the Global Constraints.

**Files:**
- Modify: `client/src/views/Orders.vue`
- Modify: `client/src/locales/en.js`, `client/src/locales/ja.js`

**Interfaces:**
- Consumes: `api.getRestockOrders()`, plus the locale keys `restocking.days` and `status.submitted` (all from Task 4).

- [ ] **Step 1: Add the locale strings**

`en.js`: in `orders`, after `allOrders: 'All Orders',` add `submittedOrders: 'Submitted Orders',`. In `orders.table`, after `expectedDelivery: 'Expected Delivery',` add:

```javascript
      submittedAt: 'Submitted',
      totalCost: 'Total Cost',
      leadTime: 'Lead Time',
```

`ja.js`, in the same positions: `submittedOrders: '提出済み注文',`, then `submittedAt: '提出日',`, `totalCost: '合計金額',`, `leadTime: 'リードタイム',`.

- [ ] **Step 2: Update the script in `Orders.vue`**

Add this import: `import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'`.

In `setup()`, after `const orders = ref([])`:

```javascript
    const restockOrders = ref([])

    // Restock orders are not affected by the global filters (they have no
    // warehouse/category), so they load once and failures don't block the page.
    const loadRestockOrders = async () => {
      try {
        restockOrders.value = await api.getRestockOrders()
      } catch (err) {
        console.error('Failed to load submitted restock orders:', err)
      }
    }

    const formatMoney = (amount) => formatCurrency(amount, currentCurrency.value)
    const formatUnitCost = (amount) => formatCurrencyWithDecimals(amount, currentCurrency.value, 2)
```

Replace `onMounted(loadOrders)` with:

```javascript
    onMounted(() => {
      loadOrders()
      loadRestockOrders()
    })
```

Add `restockOrders, formatMoney, formatUnitCost` to the returned object.

- [ ] **Step 3: Add the card to the template**

Insert this immediately before the existing `<div class="card">` that holds "All Orders" (inside the `v-else` block, after `stats-grid`):

```vue
      <div v-if="restockOrders.length > 0" class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('orders.submittedOrders') }} ({{ restockOrders.length }})</h3>
        </div>
        <div class="table-container">
          <table class="orders-table">
            <thead>
              <tr>
                <th class="col-order-number">{{ t('orders.table.orderNumber') }}</th>
                <th class="col-date">{{ t('orders.table.submittedAt') }}</th>
                <th class="col-items">{{ t('orders.table.items') }}</th>
                <th class="col-value">{{ t('orders.table.totalCost') }}</th>
                <th class="col-lead-time">{{ t('orders.table.leadTime') }}</th>
                <th class="col-date">{{ t('orders.table.expectedDelivery') }}</th>
                <th class="col-status">{{ t('orders.table.status') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="order in restockOrders" :key="order.id">
                <td class="col-order-number"><strong>{{ order.order_number }}</strong></td>
                <td class="col-date">{{ formatDate(order.submitted_at) }}</td>
                <td class="col-items">
                  <details class="items-details">
                    <summary class="items-summary">
                      {{ t('orders.itemsCount', { count: order.items.length }) }}
                    </summary>
                    <div class="items-dropdown">
                      <div v-for="item in order.items" :key="item.sku" class="item-entry">
                        <span class="item-name">{{ translateProductName(item.name) }}</span>
                        <span class="item-meta">{{ t('orders.quantity') }}: {{ item.quantity }} @ {{ formatUnitCost(item.unit_cost) }}</span>
                      </div>
                    </div>
                  </details>
                </td>
                <td class="col-value"><strong>{{ formatMoney(order.total_cost) }}</strong></td>
                <td class="col-lead-time">{{ t('restocking.days', { count: order.lead_time_days }) }}</td>
                <td class="col-date">{{ formatDate(order.expected_delivery) }}</td>
                <td class="col-status"><span class="badge info">{{ t('status.submitted') }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
```

Add this to the scoped `<style>`, next to the other column widths:

```css
.col-lead-time {
  width: 110px;
}
```

- [ ] **Step 4: Verify**

Run: `cd client && npm run build`
Expected: the build succeeds.

Use Playwright MCP to check:
1. With no orders, `/orders` shows no Submitted Orders card and looks unchanged.
2. Place an order on `/restocking`, then open `/orders`. The Submitted Orders card shows `RST-0001` with the total, "14 days" for a $30,000 budget, and an expected delivery 14 days after the submitted date.
3. Changing a global filter still filters All Orders and leaves the Submitted Orders card alone.
4. After switching the language to Japanese, the card's labels are in Japanese and amounts show in ¥.

- [ ] **Step 5: Commit**

```bash
git add client/src/views/Orders.vue client/src/locales/en.js client/src/locales/ja.js
git commit -F - <<'EOF'
Show submitted restock orders in the Orders tab

New Submitted Orders card lists restock orders with total, lead time,
and expected delivery; independent of the global filters.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018sN4YGcMVKshYmTxnpYchQ
EOF
```

---

### Task 6: End-to-end verification

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `cd tests && uv run --project ../server pytest backend/ -v`
Expected: all tests PASS (40 existing + the new restocking tests).

- [ ] **Step 2: Restart the servers to load the new code**

Run `/stop`, then `/start`. Alternatively, kill ports 3000 and 8001 and run both start commands from CLAUDE.md.

- [ ] **Step 3: Check the full flow with Playwright MCP**

1. `http://localhost:3000/restocking`: the nav shows the "Restocking" tab as active.
2. Set the slider to $30,000. The table shows 4 items, with BRG-102 as a partial line. The stat cards show Total $29,955, Remaining $45, and Lead Time 14 days.
3. Click **Place Order**. The confirmation shows "Order RST-0001 placed." with a "View in Orders" link.
4. Click the link. On `/orders`, the Submitted Orders card shows RST-0001 with "14 days" and an expected delivery 14 days after today.
5. The browser console shows no errors.
6. Take a screenshot of each page for the summary.

- [ ] **Step 4: Request a code review**

Dispatch the `code-reviewer` agent on the branch diff, following CLAUDE.md: "Use after writing significant code". Fix confirmed issues and re-run Step 1.
