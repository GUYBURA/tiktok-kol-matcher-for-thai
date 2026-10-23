import json
from datetime import datetime, timedelta

from kol_matcher.schemas import now_utc_naive, stable_hash


def put(con, actor: str, input: dict, payload) -> None:
    con.execute(
        "INSERT INTO raw_responses VALUES (?, ?, ?, ?)",
        [actor, stable_hash(input), now_utc_naive(), json.dumps(payload, ensure_ascii=False)],
    )

def get(con, actor: str, input: dict, max_age_hours: float | None):
    cutoff = datetime.min if max_age_hours is None else now_utc_naive() - timedelta(hours=max_age_hours)
    row = con.execute(
        """SELECT payload FROM raw_responses
           WHERE actor = ? AND input_hash = ? AND fetched_at > ?
           ORDER BY fetched_at DESC LIMIT 1""",
        [actor, stable_hash(input), cutoff],
    ).fetchone()
    return json.loads(row[0]) if row else None