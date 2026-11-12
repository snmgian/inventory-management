# Reports Page Bug Investigation

**Date:** 2026-11-12
**Branch:** `main` at commit `08046f5`
**Method:** Playwright MCP against a local dev setup (frontend `http://localhost:3000`, API `http://localhost:8001`). The steps were: switch the language, navigate every page, set the Time Period and Location filters, and watch the browser console and network requests. Each symptom was then traced to source. No code was changed.

## Summary

The Reports page (`/reports`) has 3 categories of bugs visible in the browser, plus a number-formatting error found in the source:

| # | Category | Symptom | Root cause |
|---|---|---|---|
| 1 | Translation | Page and nav link stay in English, amounts stay in `$` after switching to Japanese | Hardcoded strings and `$`; `useI18n` and currency helpers not used |
| 2 | Filters | Time Period and Location filters have no effect | Filters not read by the view, API calls bypass `api.js`, backend endpoints take no filter parameters |
| 3 | Console | About 100 debug log lines on every visit | Leftover `console.log` calls in lifecycle hooks and helper functions |
| 4 | Number formatting | Some amounts are off by one cent | `formatNumber` cuts decimals off instead of rounding |

All four come from the same place: `client/src/views/Reports.vue` was written outside the project's conventions. It uses the Options API, calls axios directly, builds its own number formatter, and has no translation or filter wiring. The other pages follow those conventions and work correctly.

---

## 1. Translation: Reports doesn't respond to the language switcher

### Reproduce

1. Open any page and pick **日本語** in the language switcher (top right).
2. Visit Inventory, Orders and Finance: headings, filters, table headers and currency (`¥`) are all translated.
3. Visit Reports: the nav link still says "Reports", and all page content stays in English with `$` amounts.

### Observed

| Element | Other pages in Japanese | Reports in Japanese |
|---|---|---|
| Nav link | 概要, 在庫, 注文, 財務, 需要予測, 補充 | `Reports` |
| Page heading | e.g. 財務ダッシュボード | `Performance Reports` |
| Table headers | e.g. 説明, ベンダー, 日付, 金額 | `Quarter`, `Total Orders`, `Total Revenue`, ... |
| Currency | `¥4,675,027,964` | `$31,166,853.08` |

### Root cause

- **Hardcoded strings.** `client/src/views/Reports.vue` has every label as literal English text: the page heading (line 4), card titles (lines 14, 47, 68), table headers (lines 20–24, 74–78) and summary card labels. It never uses `useI18n`.
- **Hardcoded currency.** Amounts get a literal `$` in front instead of going through the currency formatter in `client/src/utils/currency.js` (lines 31, 32, 56, 85, 108, 112).
- **Hardcoded nav label.** `client/src/App.vue:29` has `Reports` as plain text while the other nav links are translated.
- **Missing translation keys.** There are no `reports` keys in `client/src/locales/en.js` or `client/src/locales/ja.js`.
- **Untranslated month labels.** Labels like "Jan 2025" come from a custom month formatter (`Reports.vue:116`) that doesn't use the active language.

---

## 2. Filters: Reports ignores all of them

### Reproduce

1. Set **Time Period = January** and **Location = San Francisco**.
2. Visit Orders: the heading shows 12 orders. The Overview order-health total also shows 12.
3. Visit Reports: it still shows all 12 months, 250 orders and $31,166,853.08 total revenue. Navigating away and back doesn't change this.

### Observed network requests

Other pages pass the filters to the API:

```
GET /api/orders?warehouse=San+Francisco&month=2025-01
GET /api/dashboard/summary?warehouse=San+Francisco&month=2025-01
GET /api/inventory?warehouse=San+Francisco
```

Reports always sends the same unfiltered requests:

```
GET /api/reports/quarterly
GET /api/reports/monthly-trends
```

### Root cause

The break happens at three layers:

| Layer | Problem | Location |
|---|---|---|
| View | Never imports `useFilters`, so it can't see the selected filters, and nothing reloads its data when a filter changes | `client/src/views/Reports.vue` |
| API client | Calls axios directly with a hardcoded `http://localhost:8001` URL instead of using `client/src/api.js`, which builds the filter query string | `Reports.vue:156`, `Reports.vue:162` |
| Backend | `get_quarterly_reports()` and `get_monthly_trends()` don't accept `warehouse`, `category`, `status` or `month` parameters | `server/main.py:285`, `server/main.py:331` |

Fixing only the frontend isn't enough: the backend endpoints also need filter parameters, filtered the same way as `/api/orders`.

### Related code problems

- `Reports.vue` uses the Options API (`data()`, `mounted()`, `methods`), while `client/CLAUDE.md` makes the Composition API the project standard.
- Lists are keyed by array index (`:key="index"`, lines 28, 51, 82). The project docs say to use unique keys such as `quarter` or `month`.
- Summary totals are worked out in a method and stored in separate values instead of computed properties, so they wouldn't update on their own once filtering is added.

---

## 3. Console: the page floods it with debug logging

### Reproduce

Open DevTools → Console, then visit Reports. Every visit, including coming back from another page, prints about 100 `[LOG]` lines.

### Observed (excerpt)

```
[LOG] Reports component mounted                         Reports.vue:18
[LOG] Loading reports data...                           Reports.vue:23
[LOG] Fetching quarterly data...                        Reports.vue:28
[LOG] Quarterly data: Proxy(Array)                      Reports.vue:31
[LOG] Fetching monthly data...                          Reports.vue:34
[LOG] Monthly data: Proxy(Array)                        Reports.vue:37
[LOG] Calculating summary stats...                      Reports.vue:40
[LOG] Summary stats calculated                          Reports.vue:42
[LOG] Loading complete                                  Reports.vue:49
[LOG] Formatting number: 8653365                        Reports.vue:88
[LOG] Calculating bar height for revenue: 1993655.74    Reports.vue:129
[LOG] Formatting month: 2025-01                         Reports.vue:116
... (repeated for every number and every month shown)
```

The line numbers above are as reported by the browser, which points into Vite's compiled output; they don't match the source file.

### Root cause

- Leftover `console.log` calls in `mounted()` and `loadData()` (source lines 145–176).
- One log call inside each of `formatNumber`, `formatMonth` and `getBarHeight`. These run once per value shown, so a single visit produces dozens of lines.
- The error handler uses `console.log('Error loading reports:', err)` (line 172) instead of `console.error`, so real failures look like the debug noise.
- The page also has no error state: if loading fails, the user sees nothing explaining why.

---

## 4. Number formatting: decimals are cut off instead of rounded

### Observed

| Field | Real value | Displayed | Correct |
|---|---|---|---|
| Avg Monthly Revenue | 2,597,237.7575 | $2,597,237.75 | $2,597,237.76 |
| Apr 2025 change | −794,529.29 (stored as 794529.2899999998) | −$794,529.28 | −$794,529.29 |
| May 2025 change | −30,082.23 (stored as 30082.22999999975) | −$30,082.22 | −$30,082.23 |

### Root cause

`formatNumber` (`Reports.vue`, around line 214) converts the number to a string and keeps the first two characters after the decimal point (`decPart.substring(0, 2)`). That cuts the value off instead of rounding it. Small floating-point errors from adding up revenue (for example 794529.2899999998) then show up as one cent too low.

The fix is to round properly, for example with `toLocaleString` / `Intl.NumberFormat` with two decimal places, or with the shared currency formatter, which also fixes the currency symbol from finding 1.

---

## Seen on other pages, not Reports

Noted during the investigation; not part of the Reports page bugs:

- `[Vue warn] Failed to resolve component: PurchaseOrderModal` on the Overview page.
- `GET /api/tasks` returns 404 on app load, logged as `Failed to load tasks: AxiosError`.
- The **Reset all filters** button label isn't translated on any page.

## Suggested fix direction

1. Rewrite `client/src/views/Reports.vue` with the Composition API, following the other views. It should use `useFilters` (and watch for filter changes), `useI18n`, the currency formatter in `utils/currency.js`, and new `getQuarterlyReports(filters)` / `getMonthlyTrends(filters)` methods in `api.js`.
2. Add `reports` keys to `locales/en.js` and `locales/ja.js`, and translate the nav label in `App.vue`.
3. Add `warehouse`, `category`, `status` and `month` query parameters to `/api/reports/quarterly` and `/api/reports/monthly-trends` in `server/main.py`, reusing the existing order filtering. Add backend tests in `tests/backend`.
4. Remove the debug `console.log` calls, log errors with `console.error`, and add loading and error states.
5. Key lists by `quarter` and `month` instead of array index, and turn the summary totals into computed properties.
