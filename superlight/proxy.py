"""Streaming HTTP/CONNECT forwarding. TLS stays end-to-end encrypted."""

import ipaddress
import select
import socket
import time
from contextlib import suppress
from urllib.parse import urlsplit


def authority(host, port):
    return "[{}]:{}".format(host, port) if ":" in host else "{}:{}".format(host, port)


def parse_authority(value):
    parsed = urlsplit("//" + value)
    if (not parsed.hostname or not parsed.port or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("CONNECT requires host:port")
    return parsed.hostname, parsed.port


def parse_upstream(value):
    if not value:
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("upstream must be an HTTP proxy URL without credentials, e.g. http://127.0.0.1:7897")
    return parsed.hostname, parsed.port or 80


def connect(host, port, own_port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if port == own_port and any(ipaddress.ip_address(a[4][0]).is_loopback for a in addresses):
        raise ValueError("proxy loop: target resolves to SuperLight")
    last_error = None
    for family, kind, protocol, _, address in addresses:
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(10)
            sock.connect(address)
            return sock
        except OSError as error:
            last_error = error
            sock.close()
    raise last_error or OSError("no target address")


def open_tunnel(host, port, upstream, own_port):
    # Check the destination even when an upstream proxy performs the actual dial.
    if port == own_port and (host.lower() == "localhost" or host in ("127.0.0.1", "::1")):
        raise ValueError("proxy loop: CONNECT targets SuperLight")
    if not upstream:
        return connect(host, port, own_port)
    sock = connect(*upstream, own_port)
    try:
        target = authority(host, port)
        sock.sendall(("CONNECT {0} HTTP/1.1\r\nHost: {0}\r\n\r\n".format(target)).encode("ascii"))
        # Do not buffer past the header: the following bytes belong to the tunnel.
        header = bytearray()
        while not header.endswith(b"\r\n\r\n"):
            byte = sock.recv(1)
            if not byte or len(header) >= 65536:
                raise OSError("invalid upstream CONNECT response")
            header.extend(byte)
        status = bytes(header).split(b"\r\n", 1)[0].split()
        if len(status) < 2 or not status[0].startswith(b"HTTP/") or status[1] != b"200":
            raise OSError("upstream proxy rejected CONNECT")
        return sock
    except BaseException:
        sock.close()
        raise


def relay(client, remote, idle_timeout=900, stop_event=None):
    """Bounded, bidirectional relay with backpressure and TCP half-close support."""
    peers = {client: remote, remote: client}
    reading = set(peers)
    pending = {client: bytearray(), remote: bytearray()}
    shut = set()
    last_activity = time.monotonic()
    for sock in peers:
        sock.setblocking(False)
    while reading or any(pending.values()):
        if stop_event is not None and stop_event.is_set():
            return
        for source, destination in peers.items():
            if source not in reading and not pending[destination] and destination not in shut:
                with suppress(OSError):
                    destination.shutdown(socket.SHUT_WR)
                shut.add(destination)
        remaining = idle_timeout - (time.monotonic() - last_activity)
        if remaining <= 0:
            return
        readers = [s for s in reading if len(pending[peers[s]]) < 262144]
        writers = [s for s in peers if pending[s]]
        ready_read, ready_write, errors = select.select(readers, writers, list(peers), min(remaining, 1))
        if errors:
            return
        for source in ready_read:
            try:
                data = source.recv(65536)
            except BlockingIOError:
                continue
            if data:
                pending[peers[source]].extend(data)
                last_activity = time.monotonic()
            else:
                reading.remove(source)
        for destination in ready_write:
            try:
                sent = destination.send(pending[destination])
            except BlockingIOError:
                continue
            if sent == 0:
                return
            del pending[destination][:sent]
            last_activity = time.monotonic()
