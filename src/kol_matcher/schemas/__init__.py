import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def stable_hash(data) -> str:
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def now_utc_naive() -> datetime:
    """Current time for DuckDB TIMESTAMP columns, which store naive values: always UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    # Naive timestamps in this project are UTC (DuckDB returns TIMESTAMP columns without a zone).
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Use for every datetime field: JSON Schema "date-time" (RFC 3339) requires an offset, and MCP
# clients such as the Inspector reject timestamps without one.
UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
