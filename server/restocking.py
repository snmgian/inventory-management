"""
Restocking recommendation and order-building logic.

Pure functions only (no I/O, no global state) so they can be unit-tested
directly. main.py owns the HTTP layer and the in-memory order list.

Money is handled in integer cents internally: summing float dollar amounts
(e.g. 450 * 24.99) accumulates rounding error, which would make "does this
line fit in the remaining budget?" comparisons flaky at exact-fit budgets.
"""
from datetime import datetime, timedelta
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

        # Quantity is capped at forecasted demand; ordering more than is forecast is rejected.
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
