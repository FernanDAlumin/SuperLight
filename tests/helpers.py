import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from superlight.pricing import Prices
from superlight.server import LocalServer
from superlight.store import Store


def batch(timestamp=1790064000000000000, **overrides):
    # Synthetic OTLP fixture following Codex's exported event field names.
    fields = {
        "event.name": "codex.sse_event", "event.kind": "response.completed",
        "conversation.id": "test-session", "model": "gpt-5.3-codex",
        "input_token_count": 1000, "output_token_count": 200,
        "cached_token_count": 400, "reasoning_token_count": 50,
    }
    fields.update(overrides)
    attributes = []
    for key, value in fields.items():
        if value is not None:
            kind = "intValue" if type(value) is int else "stringValue"
            attributes.append({"key": key, "value": {kind: str(value)}})
    return {"resourceLogs": [{
        "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "codex_cli_rs"}}]},
        "scopeLogs": [{"logRecords": [{"timeUnixNano": str(timestamp), "attributes": attributes}]}],
    }]}


@contextmanager
def running_server(upstream=None):
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "usage.sqlite3")
        logs = []
        server = LocalServer(0, store, Prices(), upstream, logs.append)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            yield server, store, logs
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            store.close()


def read_header(sock):
    data = bytearray()
    while not data.endswith(b"\r\n\r\n"):
        byte = sock.recv(1)
        if not byte:
            raise AssertionError("connection ended before response headers")
        data.extend(byte)
    return bytes(data)
