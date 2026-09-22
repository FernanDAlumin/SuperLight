import gzip
import http.client
import json
import socket
import socketserver
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from superlight.cli import codex_command
from tests.helpers import batch, read_header, running_server


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(65536)
            if not data:
                self.request.sendall(b"END")
                return
            self.request.sendall(data)


@contextmanager
def echo_server():
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield server.server_address
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ServerTests(unittest.TestCase):
    def test_otlp_gzip_json_and_duplicate_delivery(self):
        with running_server() as (server, store, logs):
            body = gzip.compress(json.dumps(batch(prompt="do-not-store-me")).encode())
            for _ in range(2):
                conn = http.client.HTTPConnection(*server.server_address, timeout=3)
                conn.request("POST", "/v1/logs", body, {
                    "Content-Type": "application/json", "Content-Encoding": "gzip"})
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), {})
                conn.close()
            self.assertEqual(store.health()["recorded_responses"], 1)
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["estimated_cost_usd"], "0.00392")
            self.assertNotIn("do-not-store-me", json.dumps(logs))

    def test_bad_batches_do_not_break_receiver(self):
        with running_server() as (server, store, _):
            for body, headers, status in (
                (b"broken", {"Content-Type": "application/json"}, 400),
                (b"{}", {"Content-Type": "application/x-protobuf"}, 415),
                (b"{}", {"Content-Type": "application/json", "Origin": "https://example.com"}, 403),
                (b"{}", {"Content-Type": "application/json", "Content-Length": "9000000"}, 413),
            ):
                conn = http.client.HTTPConnection(*server.server_address, timeout=3)
                conn.request("POST", "/v1/logs", body, headers)
                self.assertEqual(conn.getresponse().status, status)
                conn.close()
            self.assertEqual(store.health()["recorded_responses"], 0)

    def test_partial_success_reports_rejected_logs(self):
        with running_server() as (server, store, _):
            payload = batch()
            bad = batch(input_token_count=-1)["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
            payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"].append(bad)
            conn = http.client.HTTPConnection(*server.server_address, timeout=3)
            conn.request("POST", "/v1/logs", json.dumps(payload), {"Content-Type": "application/json"})
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["partialSuccess"]["rejectedLogRecords"], "1")
            self.assertEqual(store.health()["recorded_responses"], 1)
            conn.close()

    def _exercise_tunnel(self, proxy, destination):
        with socket.create_connection(proxy, timeout=3) as connection:
            host = "{}:{}".format(*destination)
            # Coalesce CONNECT headers and payload to catch read-ahead data loss.
            payload = b"encrypted-like-data\x00\xff" * 1024
            connection.sendall(("CONNECT {0} HTTP/1.1\r\nHost: {0}\r\n\r\n".format(host)).encode() + payload)
            self.assertIn(b"200 Connection Established", read_header(connection))
            connection.shutdown(socket.SHUT_WR)
            received = bytearray()
            while True:
                part = connection.recv(65536)
                if not part:
                    break
                received.extend(part)
            self.assertEqual(bytes(received), payload + b"END")

    def test_direct_connect_preserves_bytes_and_half_close(self):
        with echo_server() as destination, running_server() as (server, store, _):
            self._exercise_tunnel(server.server_address, destination)
            # Encrypted traffic alone must never create invented token usage.
            self.assertEqual(store.health()["recorded_responses"], 0)

    def test_connect_through_an_upstream_http_proxy(self):
        with echo_server() as destination, running_server() as (upstream, _, _):
            with running_server(upstream.server_address) as (server, _, _):
                self._exercise_tunnel(server.server_address, destination)

    def test_recursive_connect_is_rejected(self):
        with running_server() as (server, _, _):
            with socket.create_connection(server.server_address, timeout=3) as connection:
                connection.sendall(("CONNECT localhost:{} HTTP/1.1\r\n\r\n".format(server.server_port)).encode())
                self.assertIn(b"400", read_header(connection))

    def test_plain_http_stream_forwarding(self):
        class Origin(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        thread = threading.Thread(target=origin.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            with running_server() as (server, _, _):
                connection = http.client.HTTPConnection(*server.server_address, timeout=3)
                connection.request("POST", "http://127.0.0.1:{}/echo?a=b".format(origin.server_port), b"request-body")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"request-body")
                connection.close()
        finally:
            origin.shutdown()
            origin.server_close()
            thread.join(timeout=5)

    def test_launcher_preserves_login_and_existing_bypass_entries(self):
        original = {"NO_PROXY": "internal.example", "KEEP": "yes"}
        command, env = codex_command(["--", "exec", "hello"], 12618, "/bin/codex", original)
        self.assertEqual(command[-2:], ["exec", "hello"])
        self.assertIn("protocol=\"json\"", command[2])
        self.assertIn("otel.log_user_prompt=false", command)
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:12618")
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertIn("internal.example", env["NO_PROXY"])
        self.assertEqual(env["KEEP"], "yes")
        self.assertNotIn("HTTPS_PROXY", original)
