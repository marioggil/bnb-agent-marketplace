"""Compliance API tests — additive ScoreOut fields + Hire CTA gate.

Spec: `openspec/changes/compliance-flags/spec.md` (Phase 1b, §4.5).

These tests pin the additive `compliance_penalty` and
`displayed_activity_score` fields on `ScoreOut` (Phase 1b API surface)
and the Hire CTA gate behaviour on the page template (Phase 1b UI
surface). They build on the 1a-i schema additions
(`agent_cache.compliance_penalty` column + `agent_compliance_flags`
table) and the 1a-ii orchestrator's row shape — pre-seeded via
`tests/_compliance_fixtures.py::seed_compliance_agent`.

Strict TDD: tests are written before the production code so the RED
phase observes the missing schema field / missing route population /
missing template wiring.
"""

from __future__ import annotations

from tests._compliance_fixtures import seed_compliance_agent


# ---------------------------------------------------------------------------
# T1 — RED: ScoreOut exposes compliance_penalty (additive schema field).
# ---------------------------------------------------------------------------


async def test_score_endpoint_includes_compliance_penalty(client, db) -> None:
    """`GET /api/agents/{chain}/{token}/score` JSON MUST include `compliance_penalty`.

    Phase 1b additive schema field — `ScoreOut` grows by two fields
    (here we pin the first). The endpoint must read the column 1a-i
    added and the row 1a-ii populated; the JSON serializer must
    expose it as a top-level key.
    """
    aid = await seed_compliance_agent(
        db,
        agent_id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1",
        creator_address="0xabc" + "0" * 37,
        owner_address="0xdef" + "0" * 37,
        creator_flagged=True,
        owner_flagged=False,
        compliance_penalty=30.00,
        activity_score=72.50,
    )
    assert aid.startswith("56:")

    resp = client.get("/api/agents/56/1/score")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "compliance_penalty" in body, (
        "ScoreOut must expose the additive `compliance_penalty` field. "
        f"Got keys: {sorted(body.keys())}"
    )
    # Pin the numeric value against the seed (sanity).
    assert body["compliance_penalty"] == 30.0


# ---------------------------------------------------------------------------
# T3 — RED: displayed_activity_score = max(0, activity_score - compliance_penalty).
# ---------------------------------------------------------------------------


async def test_displayed_activity_score_subtracts_penalty(client, db) -> None:
    """`displayed_activity_score` MUST equal `max(0, activity_score - compliance_penalty)`.

    For an agent with `activity_score=72.50` and `compliance_penalty=30.00`
    the displayed value is `42.50`. The canonical `activity_score` stays at
    `72.50` (it is never mutated by Phase 1b — that is the whole point of
    keeping the persisted score immutable and computing a display-side
    adjustment only). `compliance_penalty` on the response mirrors the
    stored column exactly.

    RED evidence: the field defaults to `0.0` from T2 because the route
    handler hasn't been wired yet. After T4 (route populates the field)
    the assertions pass.
    """
    await seed_compliance_agent(
        db,
        agent_id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1",
        creator_address="0xabc" + "0" * 37,
        owner_address="0xdef" + "0" * 37,
        creator_flagged=True,
        owner_flagged=False,
        compliance_penalty=30.00,
        activity_score=72.50,
    )

    body = client.get("/api/agents/56/1/score").json()

    # Sanity: canonical score is untouched.
    assert body["activity_score"] == "72.50", (
        "activity_score is canonical — Phase 1b must NOT mutate it. "
        f"Got {body['activity_score']!r}."
    )
    # Pin the penalty surface.
    assert body["compliance_penalty"] == 30.0
    # The user-facing adjustment: 72.50 - 30.00 = 42.50 (clamped at 0).
    assert body["displayed_activity_score"] == 42.5, (
        "displayed_activity_score = max(0, activity_score - compliance_penalty). "
        f"Expected 42.5, got {body['displayed_activity_score']!r}."
    )


# ---------------------------------------------------------------------------
# T9 — TRIANGULATE: clip-to-zero, clean agent, AgentOut boundary, badge render.
# ---------------------------------------------------------------------------


async def test_score_endpoint_clip_to_zero_when_penalty_exceeds_activity(
    client, db
) -> None:
    """Penalty > activity MUST clip `displayed_activity_score` to `0.0` (never negative).

    Spec R6 / AC-6: "When `compliance_penalty >= activity_score`, the
    displayed value is `0.00` (never negative)."
    """
    await seed_compliance_agent(
        db,
        agent_id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1",
        creator_address="0xabc" + "0" * 37,
        owner_address="0xdef" + "0" * 37,
        creator_flagged=True,
        owner_flagged=False,
        compliance_penalty=30.00,
        activity_score=20.00,
    )

    body = client.get("/api/agents/56/1/score").json()

    # Sanity on the stored values.
    assert body["activity_score"] == "20.00"
    assert body["compliance_penalty"] == 30.0
    # Clip to zero — no negative numbers leak into the JSON.
    assert body["displayed_activity_score"] == 0.0, (
        "When penalty exceeds activity, displayed_activity_score must clip to 0.0. "
        f"Got {body['displayed_activity_score']!r}."
    )


async def test_score_endpoint_clean_agent_zero_penalty(client, db) -> None:
    """Clean agent (no flags, zero penalty) MUST show displayed == stored.

    Spec R6 / AC-6: clean agent — displayed equals stored.
    """
    await seed_compliance_agent(
        db,
        agent_id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1",
        creator_address="0xabc" + "0" * 37,
        owner_address="0xdef" + "0" * 37,
        creator_flagged=False,
        owner_flagged=False,
        compliance_penalty=0.00,
        activity_score=85.00,
    )

    body = client.get("/api/agents/56/1/score").json()

    assert body["compliance_penalty"] == 0.0
    # Decimal '85.00' on the wire (Pydantic preserves Decimal serialization
    # for `activity_score`); the displayed formula reduces to identity.
    assert body["displayed_activity_score"] == float(body["activity_score"]), (
        "Clean agent must have displayed == stored activity score. "
        f"Got stored={body['activity_score']!r}, displayed={body['displayed_activity_score']!r}."
    )


async def test_agent_out_does_not_expose_compliance_penalty(client, db) -> None:
    """Contract test — `compliance_penalty` lives on `ScoreOut` only.

    `GET /api/agents/{chain}/{token}` returns the `AgentOut` shape, which
    is intentionally narrower than `ScoreOut`. The compliance penalty
    surface is reserved for the additive `ScoreOut` fields (1b adds two
    fields there). Pinning this boundary prevents silent schema drift
    where the column accidentally leaks into the listing/detail JSON.
    """
    await seed_compliance_agent(
        db,
        agent_id="56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1",
        creator_address="0xabc" + "0" * 37,
        owner_address="0xdef" + "0" * 37,
        creator_flagged=True,
        owner_flagged=False,
        compliance_penalty=30.00,
        activity_score=72.50,
    )

    resp = client.get("/api/agents/56/1")
    assert resp.status_code == 200, resp.text
    agent_body = resp.json()

    # AgentOut intentionally does not include the additive compliance fields.
    assert "compliance_penalty" not in agent_body, (
        "AgentOut boundary must NOT leak compliance_penalty. "
        f"Got keys: {sorted(agent_body.keys())}"
    )
    assert "displayed_activity_score" not in agent_body, (
        "AgentOut boundary must NOT leak displayed_activity_score. "
        f"Got keys: {sorted(agent_body.keys())}"
    )

    # And the /score endpoint MUST include them (paired assertion).
    score_body = client.get("/api/agents/56/1/score").json()
    assert "compliance_penalty" in score_body
    assert "displayed_activity_score" in score_body


async def test_agent_detail_compliance_badge_renders_with_negative_value(
    client, db
) -> None:
    """Page renders both the displayed score value AND the `Compliance: -30.00 pts` badge.

    T9 triangulation of the badge substring against the displayed-score
    substring on the activity score card.
    """
    aid = "56:0x8004A169FB4a3325136EB29fA0ceB6D2e539a432:1"
    from sqlalchemy import select

    from app.db.models.agent import AgentCache
    from tests._compliance_fixtures import seed_compliance_agent

    from tests.test_pages import _seed_one

    await _seed_one(db, 1, name="Alpha")
    async with db.begin():
        row = await db.scalar(select(AgentCache).where(AgentCache.agent_id == aid))
        assert row is not None
        row.agent_wallet = "0x" + "77" * 20
        row.x402_supported = True
    await seed_compliance_agent(
        db,
        agent_id=aid,
        creator_address="0x" + "aa" * 20,
        owner_address="0x" + "bb" * 20,
        creator_flagged=True,
        owner_flagged=False,
        compliance_penalty=30.00,
        activity_score=72.50,
    )

    body = client.get("/agents/56/1").text

    # Locate the activity-score section so the assertions don't false-positive
    # against other sections that happen to mention the same numbers.
    section = body.split('id="activity-score"', 1)[1].split("</section>", 1)[0]
    # The displayed activity score: 72.50 - 30.00 = 42.50.
    assert "42.50" in section, (
        f"Activity score card must render the displayed value 42.50. Section: {section!r}"
    )
    # The compliance badge substring (Unicode minus `−`, format %.2f -> 30.00).
    assert "Compliance: −30.00 pts" in section, (
        f"Activity score card must render the Compliance badge substring. Section: {section!r}"
    )
