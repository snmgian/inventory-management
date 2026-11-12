# Restocking Tab: Design

**Date:** 2026-11-12
**Status:** Approved in brainstorming; awaiting spec review

## Goal

Add a "Restocking" tab where a user sets an available budget, sees which demand-forecast items to restock within that budget, and places a restocking order. Submitted orders appear in a new "Submitted Orders" section of the Orders tab, with their delivery lead time.

## Decisions

| Topic | Decision |
|---|---|
| Unit costs | Add `unit_cost` and `lead_time_days` to `demand_forecasts.json` |
| Recommendation logic | Priority fill, which stops after one partial item |
| Where logic runs | Server-side; the slider calls a recommendations endpoint |
| Order storage | In-memory list on the backend; cleared on server restart |
| Lead time | Per item; the order's lead time is the slowest item's |
| Global filters | Ignored by the Restocking tab and the Submitted Orders section, because forecasts have no warehouse field |

## 1. Data changes

Add two required fields to each record in `server/data/demand_forecasts.json`:

| SKU | Name | unit_cost | lead_time_days | Price source |
|---|---|---:|---:|---|
| WDG-001 | Industrial Widget Type A | 24.99 | 7 | generate_data.py catalog |
| BRG-102 | Steel Bearing Assembly | 89.50 | 14 | generate_data.py catalog |
| GSK-203 | High-Temperature Gasket | 12.75 | 5 | generate_data.py catalog |
| MTR-304 | Electric Motor 5HP | 445.00 | 21 | generate_data.py catalog |
| FLT-405 | Oil Filter Cartridge | 8.25 | 5 | generate_data.py catalog |
| VLV-506 | Pressure Relief Valve | 156.00 | 18 | generate_data.py catalog |
| PSU-501 | 5V 10A Switching Power Supply | 18.99 | 10 | inventory.json |
| SNR-420 | Temperature Sensor Module | 38.50 | 12 | new value |
| CTL-330 | Logic Controller Board | 215.00 | 15 | new value |

Add `unit_cost: float` and `lead_time_days: int` to the `DemandForecast` Pydantic model in `server/main.py`. `GET /api/demand` will then return the new fields, which is a backwards-compatible addition.

Restocking every item in full (`max_budget`) costs **$107,220.48**.

## 2. Recommendation algorithm

`recommend_restock(forecasts, budget) -> dict` is a pure function with no I/O and no global state, so it can be tested directly.

1. **Rank** the forecasts by:
   1. trend: `increasing`, then `stable`, then `decreasing`;
   2. growth %, `(forecasted - current) / current`, highest first;
   3. `item_sku`, ascending, so that ties always resolve the same way.
2. **Fill.** Set `remaining = budget`. For each ranked item, with `line_cost = forecasted_demand * unit_cost`:
   - If `line_cost <= remaining`, add the item at full quantity and subtract its cost.
   - Otherwise, set `qty = floor(remaining / unit_cost)`. If `qty >= 1`, add the item with `partial: true`. **Stop** either way.
3. **Return:**
   ```json
   {
     "budget": 30000.0,
     "max_budget": 107220.48,
     "total_cost": 29955.0,
     "remaining_budget": 45.0,
     "lead_time_days": 14,
     "items": [
       {"sku": "WDG-001", "name": "...", "trend": "increasing",
        "quantity": 450, "forecasted_demand": 450, "unit_cost": 24.99,
        "line_total": 11245.5, "lead_time_days": 7, "partial": false}
     ]
   }
   ```
   Round money values to 2 decimals. `lead_time_days` is the largest item lead time, or 0 when no items are recommended.

**Resulting ranking:** WDG-001, GSK-203, FLT-405 (increasing); BRG-102, SNR-420, CTL-330, VLV-506, PSU-501 (stable); MTR-304 (decreasing).

**Example with a $30,000 budget:**
- WDG-001: 450 units, $11,245.50
- GSK-203: 600 units, $7,650.00
- FLT-405: 950 units, $7,837.50
- BRG-102: partial, 36 of 152 units, $3,222.00

The total is $29,955.00, $45.00 remains, and the lead time is 14 days.

## 3. API

### `GET /api/restock/recommendations?budget={float}`
- `budget` is declared as `Query(..., ge=0)`. A negative or missing budget returns 422 (FastAPI validation).
- Returns the algorithm's output using a `RestockRecommendation` response model.

### `POST /api/restock-orders` returns 201
Request (`CreateRestockOrderRequest`):
```json
{"budget": 30000, "items": [{"sku": "WDG-001", "quantity": 450}]}
```
The server re-prices every line from its own forecast data and never trusts prices sent by the client. It returns **400** with a clear `detail` message when:
- `items` is empty;
- a SKU is unknown;
- a SKU appears more than once;
- `quantity < 1` or `quantity > forecasted_demand`;
- the re-priced total is greater than `budget`.

Response (`RestockOrder`):
```json
{
  "id": "1",
  "order_number": "RST-0001",
  "submitted_at": "2026-11-12T14:03:00",
  "status": "Submitted",
  "budget": 30000.0,
  "total_cost": 29955.0,
  "lead_time_days": 14,
  "expected_delivery": "2026-11-26T14:03:00",
  "items": [{"sku": "...", "name": "...", "quantity": 450, "unit_cost": 24.99,
             "line_total": 11245.5, "lead_time_days": 7}]
}
```
- `expected_delivery` is `submitted_at + lead_time_days`, in ISO 8601 without microseconds.
- Order numbers count up from `RST-0001` within each server run.

### `GET /api/restock-orders`
Returns `List[RestockOrder]`, newest first.

### State
A module-level `restock_orders: list` lives in `server/main.py`. It is never written to disk.

## 4. Frontend

All `.vue` work is delegated to the **vue-expert** agent, as CLAUDE.md requires.

### `client/src/api.js`
Add `getRestockRecommendations(budget)`, `createRestockOrder(payload)` and `getRestockOrders()`.

### `client/src/views/Restocking.vue` (new)
- **Route and nav:** add `/restocking` to `main.js`, and add a nav link in `App.vue` after Demand.
- **On load:** fetch recommendations with `budget=0` to learn `max_budget`, then set the slider to 50% of `max_budget` (rounded to the slider step) and fetch again.
- **Budget slider:** a `<input type="range">` from 0 to `max_budget` rounded **up** to the next $500 ($107,500), step $500, so the slider can always reach a full restock. It shows the current value through `formatCurrency`. Refetch recommendations 200 ms after the slider stops moving, and discard any response that is out of date.
- **Recommendations table:** SKU, name, trend badge, quantity (shown as "36 / 152" with a "Partial" badge for partial rows), unit cost, line total, and lead time. If nothing fits the budget, show an empty-state message.
- **Summary:** total cost, remaining budget, and order lead time in days.
- **Place Order button:**
  - It is disabled when there are no items or while the request is in flight.
  - On success it shows a confirmation with the order number and a link to `/orders`.
  - On failure it shows the server's `detail` message inline.
- Follow the existing design system: slate colors, the card and stat-card classes, and no emojis.

### `client/src/views/Orders.vue`
- Fetch `getRestockOrders()` alongside the existing orders.
- Show a **Submitted Orders** card above "All Orders" only when at least one restock order exists. Its columns are order number, submitted date, items (in an expandable `details` element), total cost, lead time in days, expected delivery, and a status badge.
- The global filters don't apply to this card.

### Translations
Add the new strings to both `client/src/locales/en.js` and `ja.js`, under `nav.restocking`, `restocking.*`, and `orders.submittedOrders*`. Money is displayed with `formatCurrency`, which converts to JPY in the Japanese locale. The budget itself stays in USD.

## 5. Error handling

| Case | Behaviour |
|---|---|
| Recommendations request fails | Inline error in the view; the slider stays usable |
| POST returns 400 | Show `detail` inline; keep the recommendations on screen |
| Budget is 0 | Empty state; Place Order is disabled |
| Server restarted | Submitted orders are gone (expected behaviour) |

## 6. Testing

### Backend: `tests/backend/test_restocking.py`
Written following the backend-api-test skill. An autouse fixture clears `restock_orders` before each test.

- **Ranking:** the order matches section 2.
- **Budget edge cases:**
  - 0 returns no items;
  - an exact fit has no partial item;
  - $30,000 matches the worked example;
  - `max_budget` or more returns every item in full;
  - a negative budget returns 422.
- **POST success:** returns 201, prices are recomputed by the server, lead time is the largest, `expected_delivery` is correct, and numbering is sequential.
- **POST validation:** each 400 case in section 3.
- **GET list:** newest first; empty at the start.
- **`GET /api/demand`:** returns `unit_cost` and `lead_time_days`.
- The existing 40 tests must still pass.

### End to end with Playwright MCP
1. Open `/restocking`, move the slider, and check that the table updates.
2. Place an order and check the confirmation.
3. Go to `/orders` and check that the Submitted Orders card shows the order with its lead time.

## Out of scope
- Persisting orders across restarts
- Editing quantities in the UI
- Warehouse-specific restocking
- Updating inventory stock levels when an order arrives
- Fixing the unrelated `/api/tasks` and `/api/purchase-orders` gaps
