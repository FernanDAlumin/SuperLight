"""Calendar-period views of the existing ledger. All money stays decimal."""

import calendar
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal


def nanoseconds(value):
    return int(value.timestamp()) * 10**9 + value.microsecond * 1000


def period_starts(now):
    # A naive local datetime uses the OS timezone rules at each boundary, including DST.
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {"day": day, "week": day - timedelta(days=day.weekday()), "month": day.replace(day=1)}


def totals(groups):
    fields = ("responses", "reported_responses", "missing_usage_responses", "input_tokens",
              "cached_input_tokens", "output_tokens", "total_tokens", "priced_responses", "unpriced_responses")
    result = {field: sum(g[field] for g in groups) for field in fields}
    cost = sum((Decimal(g["estimated_cost_usd"]) for g in groups if g["estimated_cost_usd"] is not None), Decimal(0))
    result["estimated_cost_usd"] = format(cost, "f") if result["priced_responses"] or not result["responses"] else None
    return result


def build_dashboard(store, prices, now=None):
    now = now or datetime.now()
    starts = period_starts(now)
    end = nanoseconds(now) + 1
    periods = {}
    for name, start in starts.items():
        groups = prices.summary(store, nanoseconds(start), end)
        groups.sort(key=lambda g: Decimal(g["estimated_cost_usd"] or "0"), reverse=True)
        periods[name] = dict(start=start.isoformat(), totals=totals(groups), models=groups, series=[])
    buckets = {}
    for name, start in starts.items():
        count = 24 if name == "day" else 7 if name == "week" else calendar.monthrange(now.year, now.month)[1]
        buckets[name] = {}
        for index in range(count):
            point = start + (timedelta(hours=index) if name == "day" else timedelta(days=index))
            key = point.strftime("%H" if name == "day" else "%Y-%m-%d")
            buckets[name][key] = dict(label=key, cost=Decimal(0), responses=0, unpriced_responses=0,
                                      future=point > now)
    for record in store.records(min(nanoseconds(s) for s in starts.values()), end):
        moment = datetime.fromtimestamp(record["timestamp_ns"] / 1e9, tz=now.tzinfo)
        estimate = prices.estimate(record)["estimated_cost_usd"]
        for name, start in starts.items():
            if record["timestamp_ns"] < nanoseconds(start):
                continue
            key = moment.strftime("%H" if name == "day" else "%Y-%m-%d")
            bucket = buckets[name][key]
            bucket["responses"] += 1
            if estimate is None:
                bucket["unpriced_responses"] += 1
            else:
                bucket["cost"] += Decimal(estimate)
    for name, entries in buckets.items():
        for bucket in entries.values():
            bucket["estimated_cost_usd"] = (None if bucket["responses"] == bucket["unpriced_responses"]
                                             and bucket["responses"] else format(bucket["cost"], "f"))
            del bucket["cost"]
            periods[name]["series"].append(bucket)
    return dict(currency="USD", generated_at=now.astimezone().isoformat() if now.tzinfo is None else now.isoformat(),
                timezone=now.tzname() if now.tzinfo is not None else now.astimezone().tzname(), periods=periods,
                pricing=dict(as_of=prices.catalog.get("as_of"), basis=prices.catalog.get("basis"),
                             models=prices.models), **store.health())


class Dashboard:
    def __init__(self, store, prices):
        self.store, self.prices = store, prices
        self.lock = threading.Lock()
        self.cached = None
        self.updated = 0
        self.day = None

    def snapshot(self):
        with self.lock:
            today = datetime.now().date()
            if self.cached is None or time.monotonic() - self.updated >= 5 or self.day != today:
                self.cached = build_dashboard(self.store, self.prices)
                self.updated, self.day = time.monotonic(), today
            return self.cached

    def invalidate(self):
        with self.lock:
            self.cached = None
