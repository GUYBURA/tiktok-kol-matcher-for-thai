import threading
from contextlib import contextmanager

from fastmcp.exceptions import ToolError

from kol_matcher.config import Settings
from kol_matcher.storage.db import open_db

# One settings object and one DuckDB connection per server process, created on first use.
# Each tool call gets its own cursor because FastMCP runs sync tools in worker threads.
_lock = threading.Lock()
_state: dict = {}


def configure(con, settings: Settings) -> None:
    """Use a given connection and settings (tests, or a host that manages its own)."""
    with _lock:
        _state.update(con=con, settings=settings)


def _get():
    with _lock:
        if "settings" not in _state:
            _state["settings"] = Settings()
        if "con" not in _state:
            _state["con"] = open_db(_state["settings"])
        return _state["con"], _state["settings"]


@contextmanager
def tool_call():
    """Yield (cursor, settings); turn expected failures into clean tool errors for the AI client."""
    con, settings = _get()
    with con.cursor() as cur:
        try:
            yield cur, settings
        except (ValueError, RuntimeError) as e:
            raise ToolError(str(e)) from e
