# UI Smoke Test Findings

**Date:** 2026-11-12
**Branch:** `new_features`
**Method:** Playwright MCP against a local dev setup (frontend `http://localhost:3000`, API `http://localhost:8001`), clicking through every main navigation tab with default filters.

## Page load results

| Tab | Route | Result |
|---|---|---|
| Overview | `/` | Loads: 5 KPI cards, order-health donut, inventory value by category, 4 shortages, top products table |
| Inventory | `/inventory` | Loads: stock table with 32 SKUs |
| Orders | `/orders` | Loads: all 250 orders |
| Finance | `/spending` | Loads: KPI cards, revenue/cost charts, spending by category, transactions |
| Demand Forecast | `/demand` | Loads: trend cards and a table of 9 forecasts |
| Restocking | `/restocking` | Loads: $54K budget gives 6 recommended items; line totals add up to the $53,794.00 total |
| Reports | `/reports` | Loads: quarterly table, monthly trend, month-over-month table, summary cards |

All 7 pages load. The problems below were found along the way.

## Problems found

### Bugs

1. **The "Create PO" buttons on the Overview page probably don't work.**
   `client/src/views/Dashboard.vue:289` uses a `<PurchaseOrderModal>` component that doesn't exist in `client/src/components/` and isn't imported or registered. Vue warns on every Overview load:
   `[Vue warn]: Failed to resolve component: PurchaseOrderModal`.
   The buttons themselves weren't clicked during this test.

2. **`GET /api/tasks` returns 404 on every page load.**
   `client/src/api.js:77` (`getTasks`) is called from `App.vue` on mount, but the backend has no `/api/tasks` endpoint. The console shows `Failed to load tasks: AxiosError: Request failed with status code 404`.

### Display and cleanup issues

3. **Reports cuts off cents instead of rounding them.**
   Values with floating-point noise lose their last cent:
   - Total Revenue (YTD): raw `31166853.089999996` shows as `$31,166,853.08` (should be `.09`)
   - Apr 2025 change: raw `-794529.2899999998` shows as `-$794,529.28` (should be `.29`)
   - Nov 2025 revenue: raw `2668438.9299999997` shows as `$2,668,438.92` (should be `.93`)

4. **Reports floods the console.**
   `client/src/views/Reports.vue` has 13 `console.log` calls (in `formatNumber`, `formatMonth`, bar-height calculation, the data-loading steps, and so on), which print about 90 debug lines each time the page opens.

5. **Demand Forecast shows a doubled plus sign.**
   The "Increasing Demand" card shows `++50.0%`, `++20.0%` and `++18.8%`. The forecast table on the same page shows them correctly (`+50.0%`).

6. **Finance drops trailing zeros in transaction amounts.**
   The Recent Transactions table shows `$10,837.5`, `$2,150.5`, `$5,497.8` and `$6,872.25` next to whole-dollar values like `$13,425`. Amounts should use one consistent 2-decimal currency format.

7. **Finance "Spending by Category" percentages add up to 123.6%.**
   Raw Materials 42.5% + Components 38.8% + Equipment 19% + Consumables 23.3% = 123.6%. Either the percentages or the category amounts are wrong.

8. **There's no favicon.**
   `GET /favicon.ico` returns 404. This doesn't affect anything.
