"""Negative-assertion tests — ranking endpoint MUST NOT aggregate wallet signals.

Spec: `openspec/changes/score-integration/spec.md` AC-4.
Design: `openspec/changes/score-integration/design.md` §6 (Phase 2 contract preserved).

Pins the load-bearing invariant: during one full request to
`GET /api/agents/score`, `fetch_wallet_signals` is invoked zero times
and no SQL statement issued by the request reads from the
`onchain_transfers` table.

The listener pattern is lifted verbatim from
`tests/test_wallet_activity_pages.py::test_listing_pages_issue_zero_wallet_queries`
(Phase 2 contract). The monkey-patch on `app.routers.agents.fetch_wallet_signals`
is the new contract: a counter wrapper asserts the function is never
called even if the handler accidentally imports it.
"""

from __future__ import annotations

import importlib

from sqlalchemy import event


async def test_score_endpoint_does_not_call_fetch_wallet_signals(client, db, monkeypatch) -> None:
    """AC-4: monkey-patched `fetch_wallet_signals` counter must equal 0 after the request.

    Strategy:
      1. Lazy-import `app.routers.agents` and read its `fetch_wallet_signals` attribute.
      2. Wrap it with a counter; if the handler imports the symbol (lazily or
         eagerly) and calls it, the counter increments.
      3. Issue `GET /api/agents/score?category=other&sort=displayed_activity_score&limit=10`.
      4. Assert `counter == 0` and response status == 200.

    RED evidence: if the handler ever starts calling `fetch_wallet_signals`,
    this test surfaces the regression before the SQL listener does.
    """
    from app.routers import agents as agents_module

    # Re-read the module each time so monkeypatch targets the live binding.
    agents_module = importlib.reload(agents_module)
    counter = {"calls": 0}

    def _tracking(*args, **kwargs):  # noqa: ANN001, ARG001
        counter["calls"] += 1
        raise AssertionError(
            "fetch_wallet_signals MUST NOT be called by rank_agents_score "
            "(spec score-integration AC-4)"
        )

    # Patch BOTH the routers module AND the services module — the handler
    # may import via either path. If neither symbol exists in this module,
    # `setattr` creates a tracking stand-in that fails fast if invoked.
    monkeypatch.setattr(agents_module, "fetch_wallet_signals", _tracking, raising=False)
    from app.services import wallet_activity as wallet_activity_mod

    monkeypatch.setattr(
        wallet_activity_mod, "fetch_wallet_signals", _tracking, raising=True
    )

    resp = client.get(
        "/api/agents/score",
        params={
            "category": "other",
            "sort": "displayed_activity_score",
            "limit": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    assert counter["calls"] == 0, (
        f"fetch_wallet_signals called {counter['calls']} times — AC-4 contract violated"
    )


async def test_score_endpoint_no_sql_touches_onchain_transfers(client, db) -> None:
    """AC-4: SQLAlchemy `before_cursor_execute` listener captures zero `onchain_transfers` hits.

    Listener pattern lifted verbatim from
    `tests/test_wallet_activity_pages.py::test_listing_pages_issue_zero_wallet_queries`
    (lines 172-186) — Phase 2 contract preserved. The handler is
    `GET /api/agents/score`, the assertion target is the same substring
    `onchain_transfers`.
    """
    from app.db import session as session_module

    counter = {"count": 0}

    def _listener(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
        if "onchain_transfers" in statement.lower():
            counter["count"] += 1

    engine = session_module.engine.sync_engine
    event.listen(engine, "before_cursor_execute", _listener)
    try:
        resp = client.get(
            "/api/agents/score",
            params={
                "category": "other",
                "sort": "displayed_activity_score",
                "limit": 10,
            },
        )
    finally:
        event.remove(engine, "before_cursor_execute", _listener)

    assert resp.status_code == 200, resp.text
    assert counter["count"] == 0, (
        f"ranking endpoint issued {counter['count']} onchain_transfers queries — AC-4 violated"
    )


async def test_score_endpoint_router_does_not_import_wallet_activity_at_top_level() -> None:
    """T8 / spec §6: the new handler MUST NOT eagerly import `wallet_activity`.

    A top-level `from app.services import wallet_activity` would break the
    negative-assertion contract because the monkey-patch on
    `app.routers.agents.fetch_wallet_signals` would target the routers'
    module-level binding while the handler calls the services module's
    binding. This test fails if a future change accidentally moves the
    import to module scope (and would surface as an import-time assertion).
    """
    from app.routers import agents as agents_module

    src = importlib.util.find_spec(agents_module.__name__)
    assert src is not None and src.origin is not None
    with open(src.origin) as fh:
        text = fh.read()
    # Allow only comment / docstring references to `wallet_activity` in the
    # module body; flag any executable top-level import. Module-level imports
    # are lines with zero leading whitespace (function-body imports use ≥8
    # spaces and are intentional lazy imports for AC-4 preservation).
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.startswith((" ", "\t")):
            # Inside a function/class — lazy import, intentionally scoped.
            continue
        stripped = line.strip()
        # Skip blanks, comments, docstrings.
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("import ", "from ")) and "wallet_activity" in stripped:
            raise AssertionError(
                f"agents.py:{line_no} imports wallet_activity at module scope — "
                "AC-4 contract broken (must stay lazy or absent)"
            )
