"""Normalize Codex OTLP logs. Never retain raw events, prompts, or headers."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Usage:
    event_key: str
    timestamp_ns: int
    service: str
    model: str
    session_id: Optional[str]
    response_id: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cached_input_tokens: Optional[int]
    reasoning_output_tokens: Optional[int]
    source: str = "codex_otel"

    @property
    def total_tokens(self):
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    def record(self):
        return dict(asdict(self), total_tokens=self.total_tokens,
                    usage_status="reported" if self.total_tokens is not None else "missing")


def _attributes(items):
    if not isinstance(items, list):
        raise ValueError("attributes must be an array")
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise ValueError("invalid attribute")
        value = item.get("value", {})
        if not isinstance(value, dict):
            raise ValueError("invalid attribute value")
        for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if kind in value:
                result[item["key"]] = value[kind]
                break
    return result


def _text(value, default=None):
    return value[:256] if isinstance(value, str) and value else default


def _count(value):
    if value is None or value == "":
        return None
    # OTLP int64 fields are commonly encoded as decimal strings.
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ValueError("invalid token count")
    number = int(value)
    if number > 2**63 - 1:
        raise ValueError("token count out of range")
    return number


def _first(mapping, *keys):
    return next((mapping[k] for k in keys if k in mapping), None)


def _timestamp(log, attrs):
    for key in ("timeUnixNano", "observedTimeUnixNano"):
        timestamp = _count(log.get(key))
        if timestamp:
            return timestamp
    # Codex 0.144.1's tracing bridge can export zero OTLP timestamps while
    # retaining the actual RFC3339 timestamp in the event attributes.
    value = attrs.get("event.timestamp")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})",
        value if isinstance(value, str) else "",
    )
    if not match:
        raise ValueError("completion needs an event timestamp")
    date, fraction, offset = match.groups()
    seconds = int(datetime.fromisoformat(date + ("+00:00" if offset == "Z" else offset)).timestamp())
    timestamp = seconds * 10**9 + int((fraction or "0").ljust(9, "0"))
    if not 0 < timestamp <= 2**63 - 1:
        raise ValueError("event timestamp out of range")
    return timestamp


def _normalize(log, resource):
    if not isinstance(log, dict):
        raise ValueError("log record must be an object")
    attrs = dict(resource, **_attributes(log.get("attributes", [])))
    body = log.get("body", {})
    name = _first(attrs, "event.name", "otel.name") or log.get("eventName")
    if not name and isinstance(body, dict):
        name = body.get("stringValue")
    if name not in {"codex.sse_event", "codex.websocket_event", "codex.websocket.event"}:
        return None
    if _first(attrs, "event.kind", "kind") != "response.completed":
        return None

    timestamp = _timestamp(log, attrs)
    fields = dict(
        timestamp_ns=timestamp,
        service=_text(attrs.get("service.name"), "codex"),
        model=_text(attrs.get("model"), "unknown"),
        session_id=_text(_first(attrs, "conversation.id", "thread.id", "session_id")),
        response_id=_text(_first(attrs, "response.id", "response_id")),
        input_tokens=_count(_first(attrs, "input_token_count", "input_tokens")),
        output_tokens=_count(_first(attrs, "output_token_count", "output_tokens")),
        cached_input_tokens=_count(_first(attrs, "cached_token_count", "cached_input_tokens")),
        reasoning_output_tokens=_count(_first(attrs, "reasoning_token_count", "reasoning_output_tokens")),
    )
    for subset, total in (("cached_input_tokens", "input_tokens"),
                          ("reasoning_output_tokens", "output_tokens")):
        if fields[subset] is not None and fields[total] is not None:
            if fields[subset] > fields[total]:
                raise ValueError("token subset exceeds total")
    # A response ID identifies a logical response, including duplicate exports.
    # Older exporters omit it; then only identical timestamped events deduplicate.
    if fields["response_id"]:
        identity = {key: fields[key] for key in ("service", "session_id", "response_id")}
    else:
        identity = fields
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Usage(event_key=key, **fields)


def parse_otlp(payload):
    """Return (usage events, rejected log count); unrelated logs are discarded."""
    if not isinstance(payload, dict) or not isinstance(payload.get("resourceLogs", []), list):
        raise ValueError("expected an OTLP JSON ExportLogsServiceRequest")
    usages, rejected = [], 0
    for resource_log in payload.get("resourceLogs", []):
        if not isinstance(resource_log, dict):
            raise ValueError("invalid resourceLogs entry")
        resource = resource_log.get("resource", {})
        if not isinstance(resource, dict):
            raise ValueError("invalid resource")
        attrs = _attributes(resource.get("attributes", []))
        scopes = resource_log.get("scopeLogs", [])
        if not isinstance(scopes, list):
            raise ValueError("scopeLogs must be an array")
        for scope in scopes:
            if not isinstance(scope, dict) or not isinstance(scope.get("logRecords", []), list):
                raise ValueError("invalid scopeLogs entry")
            for log in scope.get("logRecords", []):
                try:
                    usage = _normalize(log, attrs)
                    if usage:
                        usages.append(usage)
                except (ValueError, TypeError, OverflowError):
                    rejected += 1
    return usages, rejected
