"""One loopback port for the forward proxy and the local OTLP JSON receiver."""

import gzip
import io
import json
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .dashboard import Dashboard
from .proxy import authority, connect, open_tunnel, parse_authority, relay
from .usage import parse_otlp


MAX_BODY = 8 * 1024 * 1024


class LocalServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True

    def __init__(self, port, store, prices, upstream=None, logger=None):
        self.store = store
        self.prices = prices
        self.dashboard = Dashboard(store, prices)
        self.upstream = upstream
        self.logger = logger or (lambda record: None)
        self.last_telemetry_ns = None
        self._slots = threading.BoundedSemaphore(64)
        self.stopping = threading.Event()
        self._connections = set()
        self._connections_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        with self._connections_lock:
            self._connections.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._connections_lock:
                self._connections.discard(request)
            self._slots.release()

    def handle_error(self, request, client_address):
        # Tracebacks and request paths can contain client data.
        self.logger({"event": "connection_error"})

    def server_close(self):
        self.stopping.set()
        with self._connections_lock:
            for connection in self._connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SuperLight/" + __version__
    sys_version = ""
    # No read-ahead: raw tunnel forwarding must see every byte after the headers.
    rbufsize = 0
    timeout = 30

    def log_message(self, *_args):
        pass

    def _json(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._respond(status, data, "application/json")

    def _respond(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _browser_request(self):
        return self.headers.get("Origin") is not None or self.headers.get("Sec-Fetch-Site") is not None

    def _trusted_local_read(self):
        host = self.headers.get("Host", "").lower()
        allowed = {"127.0.0.1:{}".format(self.server.server_port), "localhost:{}".format(self.server.server_port)}
        if host not in allowed or self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            return False
        return self.headers.get("Origin") in (None, "http://" + host)

    def do_CONNECT(self):
        self.close_connection = True
        if self._browser_request():
            self._json(403, {"error": "browser requests are not supported"})
            return
        try:
            host, port = parse_authority(self.path)
            remote = open_tunnel(host, port, self.server.upstream, self.server.server_port)
        except ValueError:
            self._json(400, {"error": "invalid or recursive CONNECT target"})
            return
        except OSError:
            self._json(502, {"error": "cannot connect to target or upstream proxy"})
            return
        with remote:
            self.send_response(200, "Connection Established")
            self.end_headers()
            self.wfile.flush()
            try:
                relay(self.connection, remote, stop_event=self.server.stopping)
            except OSError:
                pass

    def _local_request(self):
        # Origin-form URLs are local API requests. Absolute-form URLs are proxy traffic.
        if not self.path.startswith("/"):
            return False
        path = urlsplit(self.path).path
        if self.command == "GET" and path in ("/", "/app.js", "/style.css", "/api/dashboard", "/health"):
            if not self._trusted_local_read():
                self._json(403, {"error": "local origin required"})
            elif path == "/api/dashboard":
                self._json(200, dict(self.server.dashboard.snapshot(),
                                     last_telemetry_ns=self.server.last_telemetry_ns, port=self.server.server_port))
            elif path == "/health":
                self._json(200, dict(status="ok", version=__version__, dashboard_api_version=1,
                                     last_telemetry_ns=self.server.last_telemetry_ns, **self.server.store.health()))
            else:
                name, kind = {"/": ("index.html", "text/html; charset=utf-8"),
                              "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                              "/style.css": ("style.css", "text/css; charset=utf-8")}[path]
                self._respond(200, (Path(__file__).parent / "web" / name).read_bytes(), kind)
            return True
        if self._browser_request():
            self._json(403, {"error": "browser requests are not supported"})
            return True
        if self.command == "GET" and self.path == "/health":
            self._json(200, dict(status="ok", version=__version__,
                                 last_telemetry_ns=self.server.last_telemetry_ns,
                                 **self.server.store.health()))
        elif self.command == "POST" and self.path == "/v1/logs":
            self._receive_logs()
        else:
            self._json(404, {"error": "use POST /v1/logs or GET /health"})
        return True

    def _receive_logs(self):
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "set the Codex OTLP exporter protocol to json"})
            return
        if self.headers.get("Transfer-Encoding"):
            self._json(400, {"error": "OTLP requests require Content-Length"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit():
            self._json(411, {"error": "one Content-Length header is required"})
            return
        length = int(lengths[0])
        if length > MAX_BODY:
            self._json(413, {"error": "OTLP batch exceeds 8 MiB"})
            return
        encoding = self.headers.get("Content-Encoding", "identity").lower()
        if encoding not in ("identity", "gzip"):
            self._json(415, {"error": "unsupported Content-Encoding"})
            return
        try:
            body = bytearray()
            while len(body) < length:
                part = self.rfile.read(min(65536, length - len(body)))
                if not part:
                    raise ValueError("incomplete request")
                body.extend(part)
            if encoding == "gzip":
                with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                    body = compressed.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    self._json(413, {"error": "decompressed batch exceeds 8 MiB"})
                    return
            usages, rejected = parse_otlp(json.loads(body))
        except (ValueError, OSError, EOFError, RecursionError):
            self._json(400, {"error": "invalid OTLP JSON batch"})
            return
        try:
            records = self.server.store.add(usages)
        except sqlite3.Error:
            self._json(503, {"error": "usage storage unavailable"})
            return
        self.server.last_telemetry_ns = time.time_ns()
        if records:
            self.server.dashboard.invalidate()
        for record in records:
            self.server.logger(dict(event="usage", **record, **self.server.prices.estimate(record)))
        response = {}
        if rejected:
            response["partialSuccess"] = {
                "rejectedLogRecords": str(rejected),
                "errorMessage": "invalid completion metadata or token counts",
            }
        self._json(200, response)

    def _forward(self):
        if self._local_request():
            return
        self.close_connection = True
        try:
            parsed = urlsplit(self.path)
            if (parsed.scheme != "http" or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.fragment):
                raise ValueError("invalid proxy URL")
            host, port = parsed.hostname, parsed.port or 80
            if port == self.server.server_port and host in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("proxy loop")
            # Ambiguous framing can desynchronize the next hop.
            if (len(self.headers.get_all("Content-Length", [])) > 1
                    or (self.headers.get("Content-Length") and self.headers.get("Transfer-Encoding"))):
                raise ValueError("ambiguous request framing")
            if self.headers.get("Upgrade"):
                self._json(501, {"error": "use HTTPS/WSS via CONNECT for protocol upgrades"})
                return
            destination = self.server.upstream or (host, port)
            remote = connect(*destination, self.server.server_port)
            target = self.path if self.server.upstream else (parsed.path or "/") + (
                "?" + parsed.query if parsed.query else "")
            blocked = {"host", "connection", "proxy-connection", "proxy-authorization", "keep-alive"}
            headers = [(k, v) for k, v in self.headers.items() if k.lower() not in blocked]
            headers.extend((("Host", authority(host, port)), ("Connection", "close")))
            request = "{} {} HTTP/1.1\r\n{}\r\n\r\n".format(
                self.command, target, "\r\n".join("{}: {}".format(k, v) for k, v in headers))
        except (ValueError, UnicodeError):
            self._json(400, {"error": "invalid or recursive HTTP proxy request"})
            return
        except OSError:
            self._json(502, {"error": "cannot connect to target or upstream proxy"})
            return
        with remote:
            try:
                remote.sendall(request.encode("latin-1"))
                relay(self.connection, remote, stop_event=self.server.stopping)
            except OSError:
                pass

    do_GET = _forward
    do_POST = _forward
    do_PUT = _forward
    do_PATCH = _forward
    do_DELETE = _forward
    do_HEAD = _forward
    do_OPTIONS = _forward
