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
