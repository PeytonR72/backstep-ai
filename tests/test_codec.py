from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from backstep.codec import decode, dumps, encode, loads


def test_decimal_scale_preserved_distinctly() -> None:
    assert encode(Decimal("10.50")) != encode(Decimal("10.5"))
    assert dumps(Decimal("10.50")) != dumps(Decimal("10.5"))
    assert decode(encode(Decimal("10.50"))) == Decimal("10.50")
    assert str(decode(encode(Decimal("10.50")))) == "10.50"  # scale, not just value equality


def test_datetime_roundtrip_with_tz() -> None:
    dt = datetime(2024, 3, 1, 12, 30, 45, 123456, tzinfo=UTC)
    assert decode(encode(dt)) == dt


def test_datetime_roundtrip_naive() -> None:
    dt = datetime(2024, 3, 1, 12, 30, 45, 123456)
    assert decode(encode(dt)) == dt


def test_date_roundtrip() -> None:
    assert decode(encode(date(2024, 3, 1))) == date(2024, 3, 1)


def test_time_roundtrip_with_tz() -> None:
    t = time(12, 30, 45, 1, tzinfo=UTC)
    assert decode(encode(t)) == t


def test_uuid_roundtrip() -> None:
    u = uuid4()
    assert decode(encode(u)) == u


def test_bytes_roundtrip() -> None:
    b = b"\x00\x01\xff\xfe hello"
    assert decode(encode(b)) == b


def test_interval_roundtrip() -> None:
    td = timedelta(days=3, seconds=45, microseconds=999)
    assert decode(encode(td)) == td


def test_interval_roundtrip_negative() -> None:
    td = timedelta(days=-1, seconds=5)
    assert decode(encode(td)) == td


def test_asyncpg_range_roundtrip() -> None:
    r = asyncpg.Range(lower=1, upper=10, lower_inc=True, upper_inc=False)
    decoded = decode(encode(r))
    assert decoded.lower == r.lower
    assert decoded.upper == r.upper
    assert decoded.lower_inc == r.lower_inc
    assert decoded.upper_inc == r.upper_inc
    assert decoded.isempty == r.isempty


def test_asyncpg_range_with_datetime_bounds_roundtrip() -> None:
    lower = datetime(2024, 1, 1, tzinfo=UTC)
    upper = datetime(2024, 12, 31, tzinfo=UTC)
    r = asyncpg.Range(lower=lower, upper=upper, lower_inc=True, upper_inc=False)
    decoded = decode(encode(r))
    assert decoded.lower == lower
    assert decoded.upper == upper


def test_asyncpg_range_empty_roundtrip() -> None:
    r = asyncpg.Range(empty=True)
    decoded = decode(encode(r))
    assert decoded.isempty is True


def test_nested_dict_and_list() -> None:
    original = {"a": [Decimal("1.10"), {"b": uuid4()}], "c": None, "d": 42, "e": "text"}
    assert decode(encode(original)) == original


def test_dumps_loads_full_roundtrip() -> None:
    original = {"amount": Decimal("10.50"), "id": uuid4(), "seen": datetime.now(UTC)}
    assert loads(dumps(original)) == original


def test_none_and_plain_json_types_passthrough() -> None:
    assert encode(None) is None
    assert encode(42) == 42
    assert encode("text") == "text"
    assert encode(True) is True


def test_dumps_is_deterministic_for_identical_values() -> None:
    value = {"amount": Decimal("10.50"), "id": UUID("12345678-1234-5678-1234-567812345678")}
    assert dumps(value) == dumps(value)


def test_dumps_sort_keys() -> None:
    assert dumps({"b": 1, "a": 2}, sort_keys=True) == '{"a":2,"b":1}'
