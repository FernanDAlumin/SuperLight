import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from superlight.dashboard import build_dashboard
from superlight.pricing import Prices
from superlight.store import Store
from superlight.usage import parse_otlp
from tests.helpers import batch, running_server


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "usage.sqlite3")
        self.prices = Prices()

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def add(self, date, **fields):
        self.store.add(parse_otlp(batch(int(date.timestamp()) * 10**9, **fields))[0])

    def test_calendar_boundaries_exclude_previous_period_and_future(self):
        tz = ZoneInfo("Asia/Singapore")
        now = datetime(2026, 9, 22, 12, tzinfo=tz)
        for date in ((2026, 8, 31, 23, 59), (2026, 9, 1, 0, 0), (2026, 9, 20, 23, 59),
                     (2026, 9, 21, 0, 0), (2026, 9, 22, 0, 0), (2026, 9, 22, 12, 0),
                     (2026, 9, 23, 0, 0)):
            self.add(datetime(*date, tzinfo=tz))
        result = build_dashboard(self.store, self.prices, now)
        for period, count in (("day", 2), ("week", 3), ("month", 5)):
            with self.subTest(period=period):
                entry = result["periods"][period]
                self.assertEqual(entry["totals"]["responses"], count)
                self.assertEqual(entry["totals"]["total_tokens"], count * 1200)
                self.assertEqual(sum(point["responses"] for point in entry["series"]), count)
        self.assertEqual(result["periods"]["day"]["totals"]["estimated_cost_usd"], "0.00784")

    def test_dst_uses_calendar_midnight_not_last_24_hours(self):
        tz = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 8, 12, tzinfo=tz)
        self.add(datetime(2026, 3, 7, 23, 59, tzinfo=tz))
        self.add(datetime(2026, 3, 8, 0, 0, tzinfo=tz))
        result = build_dashboard(self.store, self.prices, now)
        self.assertEqual(result["periods"]["day"]["totals"]["responses"], 1)
        self.assertEqual(result["periods"]["week"]["totals"]["responses"], 2)
        self.assertEqual(result["timezone"], "EDT")

    def test_unknown_price_is_not_a_zero_estimate(self):
        now = datetime(2026, 9, 22, 12)
        self.add(datetime(2026, 9, 22, 10), model="unknown-model")
        result = build_dashboard(self.store, self.prices, now)["periods"]["day"]
        self.assertIsNone(result["totals"]["estimated_cost_usd"])
        self.assertEqual(result["totals"]["unpriced_responses"], 1)
        self.assertIsNone(result["series"][10]["estimated_cost_usd"])

    def test_empty_period_is_zero_with_no_fabricated_models(self):
        result = build_dashboard(self.store, self.prices)
        self.assertEqual(result["periods"]["day"]["totals"]["estimated_cost_usd"], "0")
        self.assertEqual(result["periods"]["day"]["models"], [])


class DashboardHTTPTests(unittest.TestCase):
    def test_owned_collector_exits_when_parent_is_no_longer_present(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            child = subprocess.Popen([sys.executable, "-m", "superlight", "serve", "--port", str(port),
                                      "--quiet", "--db", str(Path(directory) / "owned.sqlite3"),
                                      "--parent-pid", str(os.getpid() + 1000000)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                _, error = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, error.decode())
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)

    def request(self, server, path, headers=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        connection.request("GET", path, headers=headers or {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_browser_can_load_local_ui_and_same_origin_data(self):
        with running_server() as (server, _, _):
            status, headers, body = self.request(server, "/?compact=1", {"Sec-Fetch-Site": "none"})
            self.assertEqual(status, 200)
            self.assertIn(b"SuperLight", body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            status, _, body = self.request(server, "/api/dashboard", {"Sec-Fetch-Site": "same-origin"})
            self.assertEqual(status, 200)
            self.assertEqual(set(json.loads(body)["periods"]), {"day", "week", "month"})

    def test_foreign_origins_and_dns_rebinding_hosts_are_rejected(self):
        with running_server() as (server, _, _):
            for headers in ({"Origin": "https://example.com"}, {"Sec-Fetch-Site": "cross-site"},
                            {"Host": "example.com:{}".format(server.server_port)}):
                with self.subTest(headers=headers):
                    status, _, _ = self.request(server, "/api/dashboard", headers)
                    self.assertEqual(status, 403)

    def test_new_usage_invalidates_dashboard_cache(self):
        with running_server() as (server, _, _):
            self.assertEqual(json.loads(self.request(server, "/api/dashboard")[2])["recorded_responses"], 0)
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            connection.request("POST", "/v1/logs", json.dumps(batch(time.time_ns() - 1000000000)), {"Content-Type": "application/json"})
            self.assertEqual(connection.getresponse().status, 200)
            connection.close()
            result = json.loads(self.request(server, "/api/dashboard")[2])
            self.assertEqual(result["recorded_responses"], 1)
            self.assertEqual(result["periods"]["day"]["totals"]["total_tokens"], 1200)
