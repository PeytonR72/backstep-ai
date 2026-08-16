"""Reversible JSON codec for values the journal stores: DB-native Python
types (Decimal, datetime, UUID, bytes, ...) that plain `json` can't encode
and that `json.dumps(default=str)` previously flattened irreversibly.

Both the journal's write/read paths and the Postgres adapter's fingerprint
canonicalization use `encode`/`decode` so a round trip through either is
lossless, and so two independently-serialized snapshots of the same typed
value always produce byte-identical JSON (required for fingerprint
stability — see backstep.adapters.postgres._fingerprint_state).

Collision note: a dict that legitimately contains a key literally named
"__t" would collide with this scheme's tagged-object convention. Since the
values passed through here are DB row data (column names as keys), a column
literally named "__t" is exceedingly unlikely; no escaping is implemented
for this edge case (YAGNI).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

try:
    from asyncpg import Range as _AsyncpgRange
except ImportError:  # pragma: no cover
    _AsyncpgRange = None


def encode(value: Any) -> Any:
    """Recursively convert a Python value (possibly containing Decimal,
    datetime/date/time, UUID, bytes, timedelta, asyncpg.Range) into a
    JSON-serializable structure using tagged objects for non-JSON-native
    types. Safe to pass directly to json.dumps with no `default=`.
    """
    if isinstance(value, Decimal):
        return {"__t": "decimal", "v": str(value)}
    if isinstance(value, datetime):
        return {"__t": "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {"__t": "date", "v": value.isoformat()}
    if isinstance(value, time):
        return {"__t": "time", "v": value.isoformat()}
    if isinstance(value, UUID):
        return {"__t": "uuid", "v": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__t": "bytes", "v": bytes(value).hex()}
    if isinstance(value, timedelta):
        return {
            "__t": "interval",
            "v": f"{value.days}:{value.seconds}:{value.microseconds}",
        }
    if _AsyncpgRange is not None and isinstance(value, _AsyncpgRange):
        return {
            "__t": "range",
            "v": {
                "lower": encode(value.lower),
                "upper": encode(value.upper),
                "lower_inc": value.lower_inc,
                "upper_inc": value.upper_inc,
                "empty": value.isempty,
            },
        }
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value: Any) -> Any:
    """Inverse of encode: recursively walk a JSON-decoded structure and
    reconstruct tagged objects back into their original Python types.
    """
    if isinstance(value, dict):
        tag = value.get("__t")
        if tag == "decimal":
            return Decimal(value["v"])
        if tag == "datetime":
            return datetime.fromisoformat(value["v"])
        if tag == "date":
            return date.fromisoformat(value["v"])
        if tag == "time":
            return time.fromisoformat(value["v"])
        if tag == "uuid":
            return UUID(value["v"])
        if tag == "bytes":
            return bytes.fromhex(value["v"])
        if tag == "interval":
            days_s, seconds_s, micros_s = value["v"].split(":")
            return timedelta(
                days=int(days_s), seconds=int(seconds_s), microseconds=int(micros_s)
            )
        if tag == "range":
            if _AsyncpgRange is None:  # pragma: no cover
                raise RuntimeError("asyncpg is required to decode a range value")
            v = value["v"]
            return _AsyncpgRange(
                lower=decode(v["lower"]),
                upper=decode(v["upper"]),
                lower_inc=v["lower_inc"],
                upper_inc=v["upper_inc"],
                empty=v["empty"],
            )
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def dumps(value: Any, *, sort_keys: bool = False) -> str:
    """json.dumps(encode(value), sort_keys=sort_keys, separators=(",", ":"))"""
    return json.dumps(encode(value), sort_keys=sort_keys, separators=(",", ":"))


def loads(text: str) -> Any:
    """decode(json.loads(text))"""
    return decode(json.loads(text))
