"""Configurable API-equivalent estimates, not subscription billing."""

import json
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path


class Prices:
    def __init__(self, path=None):
        path = Path(path) if path else Path(__file__).with_name("prices.json")
        with path.open(encoding="utf-8") as source:
            self.catalog = json.load(source)
        if not isinstance(self.catalog, dict) or self.catalog.get("currency") != "USD":
            raise ValueError("price catalog must use currency USD")
        self.models = self.catalog.get("models")
        if not isinstance(self.models, dict):
            raise ValueError("price catalog needs a models object")
        for model, rates in self.models.items():
            if not isinstance(model, str) or not isinstance(rates, dict):
                raise ValueError("invalid model price entry")
            for tier in (rates, rates.get("long_context")):
                if tier is None:
                    continue
                if not isinstance(tier, dict):
                    raise ValueError("invalid price tier")
                for name in ("input", "cached_input", "output"):
                    try:
                        value = Decimal(str(tier[name]))
                    except (KeyError, InvalidOperation):
                        raise ValueError("each price tier needs numeric input, cached_input, output rates") from None
                    if not value.is_finite() or value < 0 or value > 1000000:
                        raise ValueError("price rates must be finite, nonnegative USD per million tokens")
                if tier is not rates:
                    threshold = tier.get("above_input_tokens")
                    if type(threshold) is not int or threshold < 0:
                        raise ValueError("long_context needs a nonnegative above_input_tokens integer")

    def estimate(self, record):
        result = dict(estimated_cost_usd=None, price_status="unknown_model",
                      price_as_of=self.catalog.get("as_of"), price_tier=None)
        rates = self.models.get(record["model"])
        if rates is None:
            return result
        if record["input_tokens"] is None or record["output_tokens"] is None:
            result["price_status"] = "missing_usage"
            return result
        cached = record["cached_input_tokens"]
        if cached is None:
            result["price_status"] = "missing_cache_detail"
            return result
        result["price_tier"] = "standard"
        if rates.get("long_context") and record["input_tokens"] > rates["long_context"]["above_input_tokens"]:
            rates = rates["long_context"]
            result["price_tier"] = "long_context"
        with localcontext() as context:
            context.prec = 50
            cost = ((record["input_tokens"] - cached) * Decimal(str(rates["input"]))
                    + cached * Decimal(str(rates["cached_input"]))
                    + record["output_tokens"] * Decimal(str(rates["output"]))) / 1000000
            result.update(estimated_cost_usd=format(cost, "f"), price_status="estimated")
        return result

    def summary(self, store, since_ns=0, until_ns=2**63 - 1):
        groups = {}
        for record in store.records(since_ns, until_ns):
            key = record["model"]
            group = groups.setdefault(key, dict(
                model=key, responses=0, reported_responses=0,
                missing_usage_responses=0, input_tokens=0, output_tokens=0,
                total_tokens=0, cached_input_tokens=0, reasoning_output_tokens=0,
                missing_cached_detail=0, missing_reasoning_detail=0,
                estimated_cost_usd=Decimal(0), priced_responses=0, unpriced_responses=0,
            ))
            group["responses"] += 1
            if record["usage_status"] == "reported":
                group["reported_responses"] += 1
                for field in ("input_tokens", "output_tokens", "total_tokens"):
                    group[field] += record[field]
                for field, missing in (("cached_input_tokens", "missing_cached_detail"),
                                       ("reasoning_output_tokens", "missing_reasoning_detail")):
                    if record[field] is None:
                        group[missing] += 1
                    else:
                        group[field] += record[field]
            else:
                group["missing_usage_responses"] += 1
            estimate = self.estimate(record)
            if estimate["estimated_cost_usd"] is None:
                group["unpriced_responses"] += 1
            else:
                group["priced_responses"] += 1
                group["estimated_cost_usd"] += Decimal(estimate["estimated_cost_usd"])
        for group in groups.values():
            group["estimated_cost_usd"] = (
                format(group["estimated_cost_usd"], "f") if group["priced_responses"] else None)
        return [groups[key] for key in sorted(groups)]
