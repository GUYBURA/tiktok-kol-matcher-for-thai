import pytest


@pytest.fixture
def as_batch():
    """Wrap a one-key fake `fn(con, settings, key) -> items` (it may raise) in the batched fetcher
    signature `(con, settings, keys) -> (items per key, error per key)`."""
    def wrap(fn):
        def batched(con, settings, keys):
            results, errors = {}, {}
            for k in keys:
                try:
                    results[k] = fn(con, settings, k)
                except Exception as e:  # noqa: BLE001 - mirrors a failed Apify run
                    errors[k] = f"{type(e).__name__}: {e}"
            return results, errors
        return batched
    return wrap
