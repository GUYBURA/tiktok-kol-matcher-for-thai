import json
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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


# In-flight slow tool calls, keyed by tool name + arguments. A job stays here until a caller has
# received its result, so a repeat call after a client timeout joins it instead of scraping again.
_jobs: dict[str, Future] = {}
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="kol-tool")


def _run[T](fn: Callable[..., T]) -> T:
    with tool_call() as (cur, settings):
        return fn(cur, settings)


def slow_call[T](tool: str, args: dict, fn: Callable[..., T]) -> T:
    """Run fn(cursor, settings) in the background and wait up to tool_wait_seconds for it.

    If it is not done in time, raise a ToolError that tells the client to call again with the same
    arguments; the work keeps running and its results are cached either way.
    """
    _, settings = _get()
    key = f"{tool}:{json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)}"
    with _lock:
        job = _jobs.get(key)
        if job is None:
            job = _jobs[key] = _pool.submit(_run, fn)
    try:
        result = job.result(timeout=settings.tool_wait_seconds)
    except FutureTimeout:
        raise ToolError(
            f"{tool} is still working (scraping takes a few minutes on a cold cache) and continues in "
            "the background. Call it again with exactly the same arguments in about 30 seconds: that "
            "waits for this same job and does not scrape again. Do not change the arguments meanwhile."
        ) from None
    except BaseException:
        _forget(key, job)
        raise
    _forget(key, job)
    return result


def _forget(key: str, job: Future) -> None:
    with _lock:
        if _jobs.get(key) is job:
            del _jobs[key]
