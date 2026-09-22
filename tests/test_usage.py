import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from superlight.cli import main
from superlight.pricing import Prices
from superlight.store import Store
from superlight.usage import parse_otlp
from tests.helpers import batch


class UsageTests(unittest.TestCase):
    def test_codex_zero_otlp_timestamps_use_rfc3339_event_timestamp(self):
        for timestamp in ("1970-01-01T00:00:01.123456789Z",
                          "1970-01-01T08:00:01.123456789+08:00"):
            with self.subTest(timestamp=timestamp):
                payload = batch(timestamp=0, **{"event.timestamp": timestamp})
                record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
                record["observedTimeUnixNano"] = "0"
                usages, rejected = parse_otlp(payload)
                self.assertEqual(rejected, 0)
                self.assertEqual(usages[0].timestamp_ns, 1123456789)

    def test_zero_event_timestamp_can_use_observed_time(self):
        payload = batch(timestamp=0)
        record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
        record["observedTimeUnixNano"] = "1234567890"
        self.assertEqual(parse_otlp(payload)[0][0].timestamp_ns, 1234567890)

    def test_missing_timestamps_are_rejected_without_inventing_a_time(self):
        self.assertEqual(parse_otlp(batch(timestamp=0)), ([], 1))

    def test_tokens_are_subsets_not_additional_usage(self):
        usages, rejected = parse_otlp(batch())
        self.assertEqual(rejected, 0)
        self.assertEqual(usages[0].total_tokens, 1200)
        self.assertEqual(Prices().estimate(usages[0].record())["estimated_cost_usd"], "0.00392")

    def test_zero_is_known_missing_is_unknown(self):
        usages, _ = parse_otlp(batch(input_token_count=0, output_token_count=0,
                                     cached_token_count=0, reasoning_token_count=0))
        self.assertEqual(usages[0].total_tokens, 0)
        self.assertEqual(Prices().estimate(usages[0].record())["price_status"], "estimated")
        usages, _ = parse_otlp(batch(input_token_count=None, output_token_count=None))
        self.assertIsNone(usages[0].total_tokens)
        self.assertIsNone(Prices().estimate(usages[0].record())["estimated_cost_usd"])

    def test_private_attributes_and_unrelated_events_are_discarded(self):
        usages, _ = parse_otlp(batch(prompt="secret-prompt", **{
            "user.email": "secret-email", "authorization": "secret-key"}))
        self.assertNotIn("secret", json.dumps(usages[0].record()))
        usages, rejected = parse_otlp(batch(**{"event.name": "codex.user_prompt"}))
        self.assertEqual((usages, rejected), ([], 0))

    def test_invalid_counts_are_rejected(self):
        for value in (-1, 1.5, "NaN", 2**64, True):
            with self.subTest(value=value):
                usages, rejected = parse_otlp(batch(input_token_count=value))
                self.assertEqual((usages, rejected), ([], 1))
        usages, rejected = parse_otlp(batch(cached_token_count=1001))
        self.assertEqual((usages, rejected), ([], 1))

    def test_only_complete_response_events_count(self):
        self.assertEqual(parse_otlp(batch(**{"event.kind": "response.output_text.delta"})), ([], 0))
        self.assertEqual(len(parse_otlp(batch(**{"event.name": "codex.websocket_event"}))[0]), 1)

    def test_unknown_model_or_cache_detail_has_no_price(self):
        for overrides, status in (({"model": "unpriced-model"}, "unknown_model"),
                                  ({"cached_token_count": None}, "missing_cache_detail")):
            record = parse_otlp(batch(**overrides))[0][0].record()
            estimate = Prices().estimate(record)
            self.assertIsNone(estimate["estimated_cost_usd"])
            self.assertEqual(estimate["price_status"], status)

    def test_long_context_priced_per_response_and_threshold_is_exclusive(self):
        short = parse_otlp(batch(model="gpt-6-astra", input_token_count=272000,
                                 cached_token_count=0, output_token_count=100,
                                 reasoning_token_count=0))[0][0].record()
        long = dict(short, input_tokens=272001)
        self.assertEqual(Prices().estimate(short)["estimated_cost_usd"], "2.725")
        self.assertEqual(Prices().estimate(long)["estimated_cost_usd"], "5.44752")

    def test_store_survives_restart_and_deduplicates_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            usage = parse_otlp(batch(**{"response.id": "resp-1"}))[0][0]
            store = Store(path)
            self.assertEqual(len(store.add([usage, usage])), 1)
            store.close()
            store = Store(path)
            try:
                self.assertEqual(store.add([usage]), [])
                replay = parse_otlp(batch(timestamp=1790064000000000001,
                                          **{"response.id": "resp-1"}))[0][0]
                self.assertEqual(store.add([replay]), [])
                distinct = parse_otlp(batch(timestamp=1790064000000000002))[0][0]
                self.assertEqual(len(store.add([distinct])), 1)
                groups = Prices().summary(store)
                self.assertEqual(groups[0]["total_tokens"], 2400)
                self.assertEqual(groups[0]["estimated_cost_usd"], "0.00784")
            finally:
                store.close()

    def test_partial_summary_and_timestamp_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "usage.sqlite3")
            try:
                for timestamp, fields in ((1, {}), (2, {"cached_token_count": None}),
                                          (3, {"input_token_count": None, "output_token_count": None})):
                    store.add(parse_otlp(batch(timestamp, **fields))[0])
                group = Prices().summary(store, since_ns=2)[0]
                self.assertEqual(group["responses"], 2)
                self.assertEqual(group["total_tokens"], 1200)
                self.assertEqual(group["missing_usage_responses"], 1)
                self.assertEqual(group["unpriced_responses"], 2)
                self.assertIsNone(group["estimated_cost_usd"])
            finally:
                store.close()

    def test_reject_invalid_price_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            for value in ("NaN", "Infinity", "-2"):
                path.write_text(json.dumps({"currency": "USD", "models": {
                    "test": {"input": value, "cached_input": "0", "output": "1"}}}))
                with self.assertRaises(ValueError):
                    Prices(path)

    def test_summary_combines_same_model_from_multiple_clients(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.sqlite3"
            store = Store(path)
            try:
                store.add(parse_otlp(batch(timestamp=1))[0])
                payload = batch(timestamp=2)
                payload["resourceLogs"][0]["resource"]["attributes"][0]["value"]["stringValue"] = "codex_desktop"
                store.add(parse_otlp(payload)[0])
                groups = Prices().summary(store)
                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0]["total_tokens"], 2400)
                self.assertEqual(groups[0]["estimated_cost_usd"], "0.00784")
            finally:
                store.close()
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["summary", "--db", str(path), "--json"]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["currency"], "USD")
            self.assertEqual(result["models"][0]["total_tokens"], 2400)

    def test_empty_summary_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "absent.sqlite3"
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["summary", "--db", str(path), "--json"]), 0)
            self.assertEqual(json.loads(output.getvalue())["models"], [])
            self.assertFalse(path.exists())
