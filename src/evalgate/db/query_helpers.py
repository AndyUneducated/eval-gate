"""Small dialect-agnostic query helpers shared across repositories."""

from __future__ import annotations

from collections.abc import Callable, Iterable


def latest_by[R, K, V](
    rows: Iterable[R],
    *,
    key: Callable[[R], K],
    value: Callable[[R], V],
) -> dict[K, V]:
    """Collapse rows ordered oldest->newest into ``{key: value}``, newest wins.

    Several repositories need a "latest row per group" without a SQL window
    function (kept out so the same code path runs on SQLite test fixtures and
    Postgres alike): the caller selects rows ``ORDER BY created_at`` ascending,
    and this keeps the last (newest) ``value`` seen for each ``key``.

    Used by ``adversarial`` (latest score per case) and ``calibration`` (latest
    human label per result); centralizing keeps the two consistent.
    """
    out: dict[K, V] = {}
    for row in rows:
        out[key(row)] = value(row)
    return out
