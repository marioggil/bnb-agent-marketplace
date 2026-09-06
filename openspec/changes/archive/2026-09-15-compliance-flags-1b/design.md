# Design: compliance-flags (Phase 1 split into 1a-i + 1a-ii + 1b)

**Change:** `compliance-flags`
**Phase:** Design (sdd-design) — REWRITE #2.
**Inputs:** `openspec/changes/compliance-flags/{explore.md, proposal.md, spec.md}`; previous `design.md` (over budget).
**Output:** this file only. `tasks.md` is produced by `sdd-tasks`.

This is the THIRD design pass. The original single-PR Phase 1 was 770 lines; the 1a/1b split landed 1a at 607 lines — still over budget. The user has now decided on a **3-way split** so every sub-PR fits inside 400 lines. Architecture, data flow, and hard-constrained decisions are unchanged across all three rewrites; the splits are purely about review surface.

`proposal.md`, `spec.md` remain unchanged. `sdd-tasks` will produce `tasks-1a-i.md` / `tasks-1a-ii.md` / `tasks-1b.md`.

---

## Header — 3-way split, all stacked to main

| Sub-PR | Branch | Scope (own files only) | Stack order | Depends on |
| --- | --- | --- | --- | --- |
| **Phase 1a-i** | `feat/compliance-flags-db-schema` | migration `0012_*`; `agent_cache.compliance_penalty` column + CHECK; `agent_compliance_flags` table; `AgentComplianceFlag` model; `compute_penalty` pure helper; `tests/test_compliance_penalty.py`. **No orchestrator. No admin router. No DB-touching service code.** | 1st PR | — |
| **Phase 1a-ii** | `feat/compliance-flags-db-service` | `compliance_refresh.py` EXTENSION (`flagged_data_stale`, `refresh_agent_compliance_flags`, `run_compliance_refresh`, `status_summary`); `app/routers/admin.py` (two endpoints); `app/main.py` mount; `tests/test_compliance_refresh.py`; `tests/test_admin_compliance.py`. | 2nd PR | 1a-i merged |
| **Phase 1b** | `feat/compliance-flags-ui` | `pages.py::agent_detail` read-path edit; `agent_detail.html` template edit; `schemas/score.py` additive fields; `routers/agents.py` populate fields; `tests/test_pages.py` extension; `tests/test_compliance_api.py`. | 3rd PR | 1a-ii merged |

**Stack order is fixed: 1a-i → main → 1a-ii → main → 1b → main.** Stack order is enforced because 1a-ii's service reads the column 1a-i added and writes the rows 1a-i modeled; 1b's UI reads the populated rows + calls `compute_penalty` from the service module.

### Line budget per sub-PR (each ≤ 400)

| Sub-PR | Estimated changed lines | Headroom |
| --- | --- | --- |
| **1a-i** | **~186** | 53% |
| **1a-ii** | **~382** | 4% — tight |
| **1b** | **~159** | 60% |

> ⚠️ **1a-ii is tight (382/400).** If apply-time TDD growth pushes 1a-ii over 400, the next split is documented in §6.R-1: drop `test_admin_compliance.py` to auth-only (≤200 lines) + spin body-shape assertions into a separate `test_admin_compliance_refresh.py` (≤200 lines). No further split is recommended pre-apply.

---

## 1. Architecture overview

The OFAC mirror (`flagged_addresses`) already exists and is refreshed by `app/services/flagged_sync.py::refresh_flagged_addresses()`. This change adds a **second** refresh phase that derives a per-agent compliance signal from that mirror, materializes it into a dedicated column + table, and surfaces it on the agent detail page and `/score` endpoint. The display layer never mutates `activity_score`; the persisted `compliance_penalty` is the only score-adjacent write.

```
                                           operator / external cron
                                                    │
                                                    ▼
                        POST /api/admin/compliance/refresh  (X-API-Key)    [Phase 1a-ii]
                                                    │
                                                    ▼
                               run_compliance_refresh()                     [Phase 1a-ii]
                               ├──► refresh_flagged_addresses()             [existing, 0xB10C mirror]
                               │      └── writes flagged_addresses
                               │
                               └──► refresh_agent_compliance_flags(session) [Phase 1a-ii]
                                      ├── SELECT address FROM flagged_addresses (one query)
                                      ├── for each agent_cache row:
                                      │     creator_flagged = lower(creator_address) IN mirror_set
                                      │     owner_flagged   = lower(owner_address)   IN mirror_set
                                      │     compliance_penalty = compute_penalty(...)  [Phase 1a-i helper]
                                      ├── UPSERT agent_compliance_flags (...)
                                      └── UPDATE agent_cache.compliance_penalty = compute_penalty(...)

                               flagged_data_stale() = max(updated_at) > 24h ago
                                                    │
                                                    ▼
                                      GET /api/admin/compliance/status        [Phase 1a-ii]
                                      { last_refreshed_at, flagged_data_stale, row_count }
```

**Read paths (Phase 1b):** `agent_detail(request, ...)` adds a single-row `SELECT * FROM agent_compliance_flags WHERE agent_id = :id` (one query, **not** N+1) and computes `displayed_activity_score = max(0, activity_score - compliance_penalty)` for the template. `get_agent_score(...)` populates two additive `ScoreOut` fields: `compliance_penalty` + `displayed_activity_score`. The template renders an OFAC banner when both flags are true, a warning when only one, and `disabled` + `aria-disabled="true"` on `#hire-cta` when both are true.

`payment.js` reads `cta.disabled` and short-circuits — **no JS change in any sub-PR** (see §5.8 and §6.R-3).

---

## 2. Phase 1a-i — DB schema + pure helper (`feat/compliance-flags-db-schema`)

**Scope:** schema additions + the single pure helper + the model. No service-level DB-touching code, no orchestrator, no admin surface, no router.

### 2.1 Migration — `migrations/versions/0012_compliance_penalty.py`

Filename is the next slot after `0011_fix_onchain_null_array` (current head, verified). **The apply phase MUST `ls migrations/versions/` immediately before branching.** On collision with a concurrent PR claiming `0012_*`, renumber to `0013_*` and update `down_revision`. Invariant §5.5.

Single migration adds the column, the CHECK, the table, and two indexes:

```python
revision = "0012_compliance_penalty"
down_revision = "0011_fix_onchain_null_array"

def upgrade() -> None:
    op.add_column(
        "agent_cache",
        sa.Column("compliance_penalty", sa.Numeric(5, 2),
                  nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        "compliance_penalty_nonneg", "agent_cache", "compliance_penalty >= 0",
    )
    op.create_table(
        "agent_compliance_flags",
        sa.Column("agent_id", sa.String(255), primary_key=True),
        sa.Column("creator_flagged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("creator_flag_sources", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("owner_flagged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("owner_flag_sources", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("creator_is_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("flagged_data_stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_compliance_flags_creator_flagged", "agent_compliance_flags", ["creator_flagged"])
    op.create_index("ix_agent_compliance_flags_owner_flagged", "agent_compliance_flags", ["owner_flagged"])

def downgrade() -> None:  # symmetric reverse
    ...
```

`activity_score` and `wallet_score` are untouched. Existing rows backfill to `compliance_penalty = 0.00` via the server default.

### 2.2 New model — `app/db/models/agent_compliance.py::AgentComplianceFlag`

Single SQLAlchemy declarative mapping mirroring the migration's table 1:1. Imports follow `app/db/models/agent.py`. The model is **not** imported into `app/db/models/__init__.py` if the codebase uses lazy discovery — 1a-ii imports it directly in the service.

### 2.3 Modified — `app/db/models/agent.py`

Add `compliance_penalty` after `metadata_completeness_score` (~line 199) and one `CheckConstraint` to `__table_args__`:

```python
compliance_penalty: Mapped[Decimal] = mapped_column(
    Numeric(5, 2), nullable=False, server_default=text("0"), default=Decimal("0"),
)
CheckConstraint("compliance_penalty >= 0", name="compliance_penalty_nonneg"),
```

Net new lines: ~6 (column + check; imports already present).

### 2.4 New service skeleton — `app/services/compliance_refresh.py`

Phase 1a-i ships the **minimum** viable module: constants, the `ComplianceRefreshReport` dataclass, and the pure `compute_penalty` helper. **No DB-touching functions** in 1a-i — those arrive in 1a-ii. Defining the dataclass here lets 1a-ii extend the module without re-exporting types.

```python
"""Compliance refresh service (OFAC penalty signal, agent-compliance domain)."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

_PENALTY_PER_FLAG: int = 30
_PENALTY_CAP: Decimal = Decimal("50")
_STALE_THRESHOLD_HOURS: int = 24


@dataclass(slots=True)
class ComplianceRefreshReport:
    """Outcome of one run_compliance_refresh() invocation."""
    sources_total_fetched: int = 0
    sources_total_inserted: int = 0
    agents_matched: int = 0
    agents_upserted: int = 0
    penalty_distribution: dict[str, int] = field(default_factory=dict)
    last_refreshed_at: str | None = None  # ISO-8601 UTC; None when mirror empty

    def as_dict(self) -> dict[str, Any]:
        return {
            "sources_total_fetched": self.sources_total_fetched,
            "sources_total_inserted": self.sources_total_inserted,
            "agents_matched": self.agents_matched,
            "agents_upserted": self.agents_upserted,
            "penalty_distribution": dict(self.penalty_distribution),
            "last_refreshed_at": self.last_refreshed_at,
        }


def compute_penalty(creator_flagged: bool, owner_flagged: bool) -> Decimal:
    """Pure: 30 points per flag, capped at 50. Returns Decimal (not float)."""
    raw = _PENALTY_PER_FLAG * int(creator_flagged) + _PENALTY_PER_FLAG * int(owner_flagged)
    return min(Decimal(raw), _PENALTY_CAP)
```

### 2.5 Tests — `tests/test_compliance_penalty.py`

Single new file, no DB. Truth table (§5.1) is the only behavioral spec.

| Case | Asserts |
| --- | --- |
| `test_compute_penalty_truth_table` (parametrized, 4 cases) | `(F,F)→Decimal("0.00")`, `(T,F)→Decimal("30.00")`, `(F,T)→Decimal("30.00")`, `(T,T)→Decimal("50.00")` (cap, **not** `60.00`) — literal `==`, never `pytest.approx` |
| `test_compute_penalty_cap_negative_assertion` | Explicit `assert compute_penalty(True, True) != Decimal("60.00")` |
| `test_compute_penalty_deterministic` | Two invocations with same args return equal `Decimal` |
| `test_compute_penalty_returns_decimal_not_float` | `isinstance(compute_penalty(False, False), Decimal)` and **not** `bool(isinstance(..., float))` |
| `test_compute_penalty_side_effect_free` | Helper does not read/write module globals, does not log, does no I/O |

Strict TDD: tests written first; RED phase asserts `ImportError`/`NameError` on `from app.services.compliance_refresh import compute_penalty`; GREEN writes the helper; REFACTOR trims to §2.4.

### 2.6 Estimated changed lines — Phase 1a-i

| File | Type | Est. lines |
| --- | --- | --- |
| `migrations/versions/0012_compliance_penalty.py` | new | ~60 |
| `app/db/models/agent_compliance.py` | new | ~40 |
| `app/db/models/agent.py` | mod | ~6 |
| `app/services/compliance_refresh.py` | new (skeleton + pure helper) | ~50 |
| `tests/test_compliance_penalty.py` | new | ~30 |
| **Phase 1a-i total** | | **~186** |

---

## 3. Phase 1a-ii — Service orchestrator + admin router (`feat/compliance-flags-db-service`)

**Scope:** extend `compliance_refresh.py` with DB-touching functions; ship admin router + mount; add two test files. **No UI, no template, no schema read-path, no `compute_penalty` tests (1a-i owns those).**

### 3.1 Modified — `app/services/compliance_refresh.py` (extension)

The 1a-ii PR **extends** the module additively — 1a-i's `compute_penalty` + `ComplianceRefreshReport` stay byte-identical. Additions (in order, after 1a-i symbols):

- `flagged_data_stale(session, threshold_hours=_STALE_THRESHOLD_HOURS) -> bool`. Single SQL: `SELECT max(updated_at) FROM flagged_addresses`. Empty mirror → `True`. `(now - max_ts).total_seconds() > threshold_hours*3600` → `True`.
- `async refresh_agent_compliance_flags(session) -> ComplianceRefreshReport`. Reads `flagged_addresses` once into `address_sources: dict[str, list[str]]` keyed by lowercased address. Iterates every `AgentCache` row, computes `creator_flagged` / `owner_flagged` / `creator_flag_sources` / `owner_flag_sources` / `creator_is_owner` / `compute_penalty(...)`, and UPSERTs `agent_compliance_flags`. Postgres: `pg_insert(AgentComplianceFlag).values(upserts).on_conflict_do_update(index_elements=[agent_id], set_={...})`. SQLite (tests): per-row delete-then-insert (test DB is small). Then bulk-updates `agent_cache.compliance_penalty = compute_penalty(...)` per agent. Returns counts + `last_refreshed_at = max(flagged_addresses.updated_at).isoformat()`.
- `async run_compliance_refresh() -> ComplianceRefreshReport`. Orchestrator. Calls `refresh_flagged_addresses()` first (existing), then `refresh_agent_compliance_flags(session)` second. If phase 1 raises, phase 2 does NOT run and the original exception propagates (no swallowed `Exception`). Copies `total_fetched` / `total_inserted` from the mirror report into the combined report.
- `async status_summary(session) -> tuple[datetime | None, int]`. `(max(flagged_addresses.updated_at), count(agent_compliance_flags))` for the status endpoint.

### 3.2 New router — `app/routers/admin.py`

`require_sync_key` is reused from `app/routers/sync.py` (existing pattern; uses `hmac.compare_digest`).

```python
"""Admin surface for the OFAC compliance pipeline (Phase 1a-ii)."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from app.db.session import AsyncSessionLocal
from app.routers.sync import require_sync_key
from app.services import compliance_refresh

router = APIRouter(prefix="/api/admin/compliance", tags=["admin-compliance"])


@router.post("/refresh", dependencies=[Depends(require_sync_key)])
async def refresh_compliance() -> dict[str, object]:
    async with AsyncSessionLocal() as session:
        report = await compliance_refresh.run_compliance_refresh()
    return report.as_dict()


@router.get("/status", dependencies=[Depends(require_sync_key)])
async def compliance_status() -> dict[str, object]:
    async with AsyncSessionLocal() as session:
        last_refreshed_at, row_count = await compliance_refresh.status_summary(session)
        stale = await compliance_refresh.flagged_data_stale(session)
    return {
        "last_refreshed_at": last_refreshed_at.isoformat() if last_refreshed_at else None,
        "flagged_data_stale": stale,
        "row_count": row_count,
    }
```

### 3.3 Router mount — `app/main.py`

Two lines: import `admin_router`, `app.include_router(admin_router)` inside `create_app()`, immediately after the `sync.router` mount.

### 3.4 Tests — 2 new files

#### `tests/test_compliance_refresh.py`

DB-backed (sqlite test DB) + a couple of mocks for the orchestrator chain.

| Case | Asserts |
| --- | --- |
| `test_refresh_writes_flagged_row_for_creator_match` | Seed mirror `(0xabc, ofac-bsc)`, seed agent `(creator=0xabc, owner=0xdef)` → one `agent_compliance_flags` row, `creator_flagged=true`, `creator_flag_sources=['ofac-bsc']`, `owner_flagged=false`, `creator_is_owner=false`, `compliance_penalty=Decimal('30.00')`. |
| `test_refresh_writes_flagged_row_for_owner_match` | Mirror case for owner. |
| `test_refresh_creator_is_owner_derivation` | `creator_address == owner_address == 0xabc`, mirror contains `0xabc` → `creator_is_owner=true`, both flags true, `compliance_penalty=Decimal('50.00')` (cap). |
| `test_refresh_clean_agent_writes_zero_penalty` | Neither address in mirror → all flags false, `compliance_penalty=Decimal('0.00')`. |
| `test_refresh_case_insensitive_match` | Mirror row stored as lowercase; agent `creator_address` mixed-case → still matched. `flagged_addresses.address` row is **not** mutated (re-read before/after). |
| `test_refresh_idempotent` | Run twice on a stable fixture → byte-equivalent JSON via `json.dumps(..., sort_keys=True)` (DB-agnostic; §5.3). Assert `compliance_penalty` change-count = 0 on second run via `WHERE compliance_penalty != :prev`. |
| `test_refresh_one_char_difference_does_not_match` | Agent `creator=0xabc…`, mirror `0xabd…` → `creator_flagged=false` (no prefix/suffix/substring promotion). |
| `test_flagged_data_stale_fresh_mirror` | `updated_at=now()` → `False`. |
| `test_flagged_data_stale_backdated_mirror` | Backdate 25h → `True`. |
| `test_flagged_data_stale_empty_mirror` | Zero rows → `True`. |
| `test_run_compliance_refresh_chains_both_phases` | Mock both phases; assert `mock.call_args_list` shows mirror invoked first, agent flags second, exactly once each. |
| `test_run_compliance_refresh_mirror_failure_short_circuits` | Mirror raises → agent phase not invoked, original exception propagates, DB state unchanged. |
| `test_run_compliance_refresh_returns_combined_report` | Both phases succeed → combined `ComplianceRefreshReport` carries mirror totals + agent counts + `last_refreshed_at`. |

#### `tests/test_admin_compliance.py`

| Case | Asserts |
| --- | --- |
| `test_refresh_endpoint_missing_api_key_returns_401` | No `X-API-Key` → `401`. |
| `test_refresh_endpoint_wrong_api_key_returns_401` | Wrong key → `401`. |
| `test_refresh_endpoint_unconfigured_key_returns_503` | Clear `SYNC_API_KEY` env → `503`. |
| `test_refresh_endpoint_happy_path_returns_200_and_json` | Valid key + mock `run_compliance_refresh` → `200`, body matches `ComplianceRefreshReport.as_dict()`, `Content-Type: application/json`. |
| `test_status_endpoint_returns_last_refreshed_and_stale_and_count` | Seed `agent_compliance_flags` rows → `GET .../status` returns `{last_refreshed_at: ISO-8601 or null, flagged_data_stale: bool, row_count: int}`. |
| `test_status_endpoint_auth_parity` | Missing/wrong key → `401`; unconfigured → `503`. |

### 3.5 Test pre-seeding fixtures strategy (1a-ii runs before 1a-i is merged)

If 1a-ii lands on a branch that hasn't yet pulled 1a-i, the test DB will not have the new column or table. 1a-ii's tests therefore **pre-seed** both via raw SQL via an autouse fixture:

```python
@pytest.fixture(autouse=True)
def _ensure_compliance_schema(db):
    """Pre-seed column + table for 1a-ii tests when 1a-i hasn't merged yet.

    Raw SQL mirrors the 1a-i migration DDL. IF NOT EXISTS / try-except make
    this a no-op once 1a-i lands.
    """
    from sqlalchemy import text
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS agent_compliance_flags (
            agent_id VARCHAR(255) PRIMARY KEY,
            creator_flagged BOOLEAN NOT NULL DEFAULT 0,
            creator_flag_sources JSON NOT NULL DEFAULT '[]',
            owner_flagged BOOLEAN NOT NULL DEFAULT 0,
            owner_flag_sources JSON NOT NULL DEFAULT '[]',
            creator_is_owner BOOLEAN NOT NULL DEFAULT 0,
            flagged_data_stale BOOLEAN NOT NULL DEFAULT 0,
            refreshed_at DATETIME NOT NULL
        )
    """))
    try:
        db.execute(text(
            "ALTER TABLE agent_cache ADD COLUMN compliance_penalty NUMERIC(5,2) NOT NULL DEFAULT 0"
        ))
    except Exception:
        pass  # Column already exists (1a-i merged).
    db.commit()
```

A focused helper `_seed_flagged_addresses(db, [(addr, source), ...])` and `_seed_agent_cache(db, agent_id, creator=..., owner=...)` lives at the top of `tests/test_compliance_refresh.py` and is reused by `tests/test_admin_compliance.py` via module import.

### 3.6 Estimated changed lines — Phase 1a-ii

| File | Type | Est. lines |
| --- | --- | --- |
| `app/services/compliance_refresh.py` | mod (extension) | ~145 |
| `app/routers/admin.py` | new | ~50 |
| `app/main.py` | mod | ~2 |
| `tests/test_compliance_refresh.py` | new | ~120 |
| `tests/test_admin_compliance.py` | new | ~65 |
| **Phase 1a-ii total** | | **~382** |

---

## 4. Phase 1b — UI + API additive fields (`feat/compliance-flags-ui`)

**Scope:** read-path UI on the agent detail page; additive `ScoreOut` fields; one new test file; one test file extension. **No service, no admin, no model, no schema, no migration.**

### 4.1 Modified — `app/routers/pages.py::agent_detail`

Read path additions, immediately after the existing `_flagged_addresses_set()` fetch (~line 856):

```python
from app.db.models.agent_compliance import AgentComplianceFlag
from app.services import compliance_refresh

compliance_row = (
    await db.execute(
        select(AgentComplianceFlag).where(AgentComplianceFlag.agent_id == row.agent_id)
    )
).scalar_one_or_none()

creator_flagged = bool(compliance_row and compliance_row.creator_flagged)
owner_flagged = bool(compliance_row and compliance_row.owner_flagged)
# Re-derive from flags so the page stays correct even if the stored column
# hasn't been written yet (un-refreshed agent).
compliance_penalty = compliance_refresh.compute_penalty(creator_flagged, owner_flagged)
base_score = Decimal(str(local_score)) if local_score is not None else Decimal("0")
displayed_activity_score = max(Decimal("0.00"), base_score - compliance_penalty)
```

Pass `creator_flagged`, `owner_flagged`, `compliance_penalty`, `displayed_activity_score` into the existing `_render(..., "pages/agent_detail.html", {...})` context dict. Net new lines in `agent_detail`: ~22.

### 4.2 Modified — `app/templates/pages/agent_detail.html`

Three localized edits near existing surfaces:

1. **OFAC banner / warning** (insert immediately before `#hire-cta` block, ~line 510):
   ```jinja
   {% if creator_flagged and owner_flagged %}
     <p class="ofac-block" role="alert">OFAC: hiring blocked — Hiring is disabled while OFAC compliance is unresolved for this agent.</p>
   {% elif creator_flagged %}
     <p class="ofac-warn" role="status">OFAC warning: creator address flagged. Hiring remains enabled.</p>
   {% elif owner_flagged %}
     <p class="ofac-warn" role="status">OFAC warning: owner address flagged. Hiring remains enabled.</p>
   {% endif %}
   ```

2. **`#hire-cta` `disabled` + `aria-disabled="true"`** (toggled only when both flags are true):
   ```jinja
   <button id="hire-cta" … {% if creator_flagged and owner_flagged %}disabled aria-disabled="true"{% endif %}>Hire …</button>
   ```

3. **Compliance badge on Activity score card** (near lines 287–304):
   ```jinja
   <p class="activity-score-value">{{ displayed_activity_score or 'n/a' }}/100</p>
   {% if compliance_penalty > 0 %}
     <span class="badge risk">⚠ Compliance: −{{ '%.2f'|format(compliance_penalty) }} pts</span>
   {% endif %}
   ```

Net new lines: ~18.

### 4.3 Modified — `app/schemas/score.py::ScoreOut`

Two additive fields. Existing fields (including `activity_score`) stay canonical:

```python
class ScoreOut(BaseModel):
    # ...existing fields unchanged...
    compliance_penalty: float = 0.0
    displayed_activity_score: float = 0.0
```

Net new lines: ~2.

### 4.4 Modified — `app/routers/agents.py::get_agent_score`

In the existing `get_agent_score` route handler (lines 234–273), populate the two additive fields on `ScoreOut(...)`:

```python
penalty = float(row.compliance_penalty or 0)
score = float(row.activity_score) if row.activity_score is not None else 0.0
return ScoreOut(
    # ...existing fields unchanged...
    compliance_penalty=penalty,
    displayed_activity_score=max(0.0, score - penalty),
)
```

Net new lines: ~4.

### 4.5 Tests — 1 extended file, 1 new file

#### `tests/test_pages.py` (extension, +~30 lines)

Three new cases appended:

| Case | Asserts |
| --- | --- |
| `test_agent_detail_renders_compliance_badge_on_score_card` | Seed agent with `compliance_penalty=30`, `creator_flagged=true` → body contains `42.50` (for `activity_score=72.50`), `Compliance: -30.00 pts` badge substring. |
| `test_agent_detail_ofac_block_banner_when_both_flags` | Both flags true → `disabled` on `#hire-cta`, `aria-disabled="true"`, banner copy `Hiring is disabled while OFAC compliance is unresolved for this agent`. |
| `test_agent_detail_ofac_warning_when_single_flag` | Only `creator_flagged=true` → warning copy present, no `disabled` substring on `#hire-cta`. |

#### `tests/test_compliance_api.py` (new, ~80 lines)

| Case | Asserts |
| --- | --- |
| `test_score_endpoint_returns_compliance_fields` | `activity_score=80.00`, `compliance_penalty=30.00` → JSON includes `compliance_penalty: 30.0`, `displayed_activity_score: 50.0`, `activity_score: 80.0` (canonical, unchanged). |
| `test_score_endpoint_clip_to_zero_when_penalty_exceeds_activity` | `activity_score=20.00`, `compliance_penalty=30.00` → `displayed_activity_score: 0.0` (never negative). |
| `test_score_endpoint_clean_agent_zero_penalty` | `compliance_penalty=0.00` → `displayed_activity_score == activity_score`. |
| `test_score_endpoint_additive_fields_do_not_break_existing_clients` | `activity_score`, `breakdown`, `pillars` keys unchanged in shape; clients ignoring unknown fields still see the same response. |
| `test_agent_endpoint_exposes_compliance_penalty` | `GET /api/agents/{chain}/{token}` returns `compliance_penalty` on the JSON (additive on existing `AgentOut` shape). |

### 4.6 Test pre-seeding fixtures strategy (1b may land before 1a-ii is merged)

If 1b lands on a branch that hasn't yet pulled 1a-i + 1a-ii, the test DB will lack the `agent_compliance_flags` rows and the `compliance_penalty` column. 1b's tests pre-seed both via a session-scoped fixture at the top of `tests/test_compliance_api.py`:

```python
@pytest.fixture
def compliance_seed(db):
    """Insert one agent_cache row + one agent_compliance_flags row via raw SQL.

    Mirrors the production schema (1a-i migration) and the production UPSERT
    payload (1a-ii orchestrator). Single source of truth — drift between this
    fixture and production is caught by the test suite.
    """
    from sqlalchemy import text
    db.execute(text("""
        INSERT INTO agent_cache (agent_id, chain_id, token_id, activity_score, compliance_penalty)
        VALUES ('56:0xreg:1', 56, 1, 72.50, 30.00)
    """))
    db.execute(text("""
        INSERT INTO agent_compliance_flags
            (agent_id, creator_flagged, owner_flagged, creator_is_owner,
             flagged_data_stale, refreshed_at, creator_flag_sources, owner_flag_sources)
        VALUES ('56:0xreg:1', 1, 0, 0, 0, :now, '[]', '[]')
    """), {"now": datetime.now(timezone.utc)})
    db.commit()
    return "56:0xreg:1"
```

`tests/test_pages.py` extensions reuse the same fixture via a co-located `_seed_compliance_agent` helper exported from a small shared `tests/_compliance_fixtures.py` (avoids cross-importing test modules).

### 4.7 Estimated changed lines — Phase 1b

| File | Type | Est. lines |
| --- | --- | --- |
| `app/routers/pages.py` | mod | ~22 |
| `app/templates/pages/agent_detail.html` | mod | ~18 |
| `app/schemas/score.py` | mod | ~2 |
| `app/routers/agents.py` | mod | ~4 |
| `tests/test_pages.py` | mod (extend) | ~30 |
| `tests/test_compliance_api.py` | new | ~80 |
| **Phase 1b total** | | **~156** |

---

## 5. Shared invariants (apply to all 3 sub-PRs)

Any sub-PR that violates one of these is wrong. Centralized here so they are auditable in one place.

### 5.1 Penalty truth table

`compute_penalty(creator_flagged, owner_flagged) -> Decimal`:

| `creator_flagged` | `owner_flagged` | raw | returned |
| --- | --- | --- | --- |
| `False` | `False` | `0` | `Decimal("0.00")` |
| `True` | `False` | `30` | `Decimal("30.00")` |
| `False` | `True` | `30` | `Decimal("30.00")` |
| `True` | `True` | `60` | `Decimal("50.00")` ← cap, not 60 |

Tests assert `result == Decimal("30.00")`, never `pytest.approx`.

### 5.2 N+1 strategy = Option A

- **Detail page** (`agent_detail` in `pages.py`, 1b): one **single-row** `SELECT * FROM agent_compliance_flags WHERE agent_id = :id`. One query for one agent. **Not N+1.**
- **Listing page** (`home`): no UI change in this slice. No per-agent compliance query required.

Reuse `_flagged_addresses_set()` (`app/routers/pages.py:357–371`) for the **per-address** OFAC badges on owner + payment wallet — unchanged. The compliance row is **per-agent state** in its own keyed table. Total: two queries on the detail page (one mirror set, one compliance row), zero N+1 risk.

### 5.3 JSONB idempotence test = DB-agnostic `json.dumps(..., sort_keys=True)`

`tests/test_compliance_refresh.py` runs `refresh_agent_compliance_flags()` twice, then asserts:

```python
import json
assert json.dumps(row_before.creator_flag_sources, sort_keys=True) == json.dumps(row_after.creator_flag_sources, sort_keys=True)
```

`JSONB.equals()` is Postgres-only and would force the idempotence test into the 8-skipped group, inflating the skip count beyond the baseline. `json.dumps(sort_keys=True)` round-trips on both SQLite and Postgres. The `compliance_penalty` change-count uses dialect-agnostic SQL (`WHERE compliance_penalty != :prev_value` count = 0).

### 5.4 `compute_penalty` returns `Decimal`, not `float`

The `compliance_penalty` column is `Numeric(5, 2)`. Float return introduces IEEE-754 rounding. `Decimal` matches the column type so 1b's `displayed_activity_score = max(0, score - penalty)` stays in exact arithmetic without implicit float conversion.

### 5.5 Migration filename re-check directive

The apply phase MUST `ls migrations/versions/` immediately before branching **1a-i** (the only sub-PR that adds a migration). On `0012_*` collision, renumber to `0013_*` and update `down_revision` to point at the new head.

### 5.6 `activity_score` stays canonical

No sub-PR writes `activity_score`. `materialize_score()` in `app/services/agent_score.py` is untouched in 1a-i, 1a-ii, and 1b. `compliance_penalty` is the only score-adjacent write; `displayed_activity_score` is computed at read time. This is what lets Phase 2 (`wallet-activity-pillar`) extend penalty coverage to `agent_wallet` without rewriting materialized values.

### 5.7 Match strategy: exact, case-insensitive

Comparison lowercases both operands at compare time. No prefix, suffix, substring, Levenshtein, or heuristic "related address" interpretation. `flagged_addresses.address` is stored as it arrived from the mirror and is never mutated. Implemented in `refresh_agent_compliance_flags` (1a-ii); consumed by 1b only via the precomputed booleans.

### 5.8 `payment.js` short-circuit — no JS change in any sub-PR

`app/static/js/payment.js:60` already does `if (!cta || cta.disabled) return;`. Phase 1b's server-rendered `disabled` is sufficient. **None of 1a-i, 1a-ii, or 1b creates or modifies any file under `app/static/`.**

---

## 6. Risks

| # | Risk | Severity | Mitigation |
| --- | --- | --- | --- |
| **R-1** | **1a-ii is tight (382/400).** If apply-time TDD growth pushes 1a-ii over 400, the next split: drop `test_admin_compliance.py` to auth-only (≤200 lines) + spin body-shape assertions into a separate `test_admin_compliance_refresh.py` (≤200 lines). | medium | Apply phase tracks the running line count after each commit. If the projected count exceeds 380 before the last commit, split `test_admin_compliance.py` per the recipe above. |
| **R-2** | Migration filename collision. `0012_*` is the next slot after `0011_fix_onchain_null_array`; a concurrent PR claiming `0012_*` forces renumbering to `0013_*`. | low | Apply phase MUST `ls migrations/versions/` immediately before branching **1a-i**. On collision, renumber. §5.5. |
| **R-3** | `payment.js` short-circuit is the entire client-side gate — confirmation that no JS change is needed. | info | Verified at `app/static/js/payment.js:60`. §5.8. **No sub-PR creates or modifies any file under `app/static/`.** |
| **R-4** | **Phase 1a-ii requires Phase 1a-i merged.** `refresh_agent_compliance_flags` reads/writes the column 1a-i added and the table 1a-i modeled. | low | Stack order is fixed: 1a-i first, 1a-ii second. 1a-ii's pre-seeding fixture (§3.5) makes the alternative (apply in one branch but merge separately) work too. |
| **R-5** | **Phase 1b requires Phase 1a-ii merged.** The Hire gate logic depends on `agent_cache.compliance_penalty` being populated; `pages.py::agent_detail` depends on `agent_compliance_flags` rows existing. | medium | Stack order is fixed. 1b's `compliance_seed` fixture (§4.6) pre-seeds the rows so the tests work even without the orchestrator. |
| **R-6** | Test pre-seeding fragility. The `_ensure_compliance_schema` (1a-ii) and `compliance_seed` (1b) fixtures must mirror what 1a-i's migration + 1a-ii's orchestrator would have inserted. Drift → silent test green / prod red. | medium | Fixtures use the **same** DDL strings as the migration (§2.1) and the **same** UPSERT payload as `refresh_agent_compliance_flags` (§3.1). A test in `tests/test_compliance_refresh.py` asserts the production UPSERT payload matches the fixture's INSERT shape (single source of truth). When 1a-i lands, the `IF NOT EXISTS` / try-except branches become no-ops. |
| **R-7** | Phase 2 collision. Phase 2 (`wallet-activity-pillar`) modifies scoring math. 1b MUST NOT touch `activity_score` or `wallet_score`. | low | Both columns are owned by `materialize_score()`. 1a-ii writes only `compliance_penalty`. 1b reads `compliance_penalty` but writes neither score column. `?sort=activity_score` uses the stored canonical value. Phase 2 can land in either order. |
| **R-8** | Spec is written flat at `openspec/changes/compliance-flags/spec.md`. Archive copies verbatim to `openspec/specs/agent-compliance/spec.md`. | info | Followed as written. New catalog entry on archive. |
| **R-9** | `tests/test_pages_x402.py` pins `#hire-cta` HTML; adding `disabled` as a new possible state requires new cases. | low | 1b's new cases are additive; existing `test_cta_disabled_without_wallet` continues to pass unchanged. The new compliance-disabled cases live in `tests/test_pages.py` (per §4.5) — the spec's hint about `test_pages_x402.py` is overridden in favor of co-locating all hire-CTA rendering tests in `test_pages.py`. |
| **R-10** | Decimal/float boundary on `compute_penalty` and the displayed-score formula. | low | Helper returns `Decimal` (1a-i); 1b's route handler converts to `float` only at the JSON boundary (`float(row.compliance_penalty or 0)`, `float(row.activity_score)`). Tests assert literal `Decimal` for `compute_penalty`; JSON serialization is `float` by Pydantic default. |

---

## 7. Verification checklist

The apply phase runs three separate checklists, one per sub-PR. Common invariants checked in every sub-PR:

- Baseline `285 passed, 8 skipped` preserved (no pre-existing test regresses).
- `app/static/js/payment.js`, `app/services/flagged_sync.py`, `app/services/agent_score.py::materialize_score` byte-for-byte unchanged across all 3 sub-PRs.

### 7.1 Phase 1a-i (`feat/compliance-flags-db-schema`)

- [ ] `ls migrations/versions/` re-checked immediately before branching; `0012_compliance_penalty.py` is the next slot, or renumbered to `0013_*` with `down_revision` updated.
- [ ] `uv run alembic upgrade head` succeeds on fresh sqlite test DB and Postgres.
- [ ] `git diff -- app/` shows 3 expected hunks: `agent.py` column + check, new `agent_compliance.py` model, new `compliance_refresh.py` skeleton.
- [ ] `uv run pytest tests/test_compliance_penalty.py` — 0 failed (RED-first).
- [ ] `uv run pytest tests/test_flagged.py` — 0 failed, baseline preserved.
- [ ] `uv run pytest` (full suite) — same pass/skip count as baseline.
- [ ] `git diff -- app/templates/` → empty (no template changes in 1a-i).
- [ ] `git diff -- app/routers/{pages,agents}.py` → empty.
- [ ] `git diff -- app/routers/admin.py` → 404 (no admin surface in 1a-i).
- [ ] `git diff -- app/schemas/` → empty.
- [ ] No tests reference `agent_compliance_flags` rows (1a-i is schema + model + pure helper only).

### 7.2 Phase 1a-ii (`feat/compliance-flags-db-service`)

- [ ] Phase 1a-i merged to main; branch rebased on post-1a-i head.
- [ ] `git diff -- migrations/` → empty (1a-i owns the migration).
- [ ] `git diff -- app/db/models/` → empty (1a-i owns the model).
- [ ] `uv run pytest tests/test_compliance_penalty.py` — 0 failed (1a-ii does not regress 1a-i).
- [ ] `uv run pytest tests/test_compliance_refresh.py` — 0 failed (orchestrator + idempotence + stale + chains + mirror short-circuit + creator_is_owner + case-insensitive match).
- [ ] `uv run pytest tests/test_admin_compliance.py` — 0 failed (auth: 401/401/503; 200 happy path on both endpoints; status endpoint shape).
- [ ] `uv run pytest` — baseline `285 passed, 8 skipped` preserved.
- [ ] `git diff -- app/` shows 3 expected hunks: `compliance_refresh.py` extension, new `admin.py` router, `main.py` mount.
- [ ] `git diff -- app/templates/` → empty. `git diff -- app/routers/{pages,agents}.py` → empty. `git diff -- app/schemas/` → empty.

### 7.3 Phase 1b (`feat/compliance-flags-ui`)

- [ ] Phase 1a-ii merged to main; branch rebased on post-1a-ii head.
- [ ] `git diff -- migrations/` → empty (1b makes no schema changes).
- [ ] `git diff -- app/db/models/` → empty. `git diff -- app/services/` → empty. `git diff -- app/routers/admin.py` → empty.
- [ ] `uv run pytest tests/test_compliance_penalty.py tests/test_compliance_refresh.py tests/test_admin_compliance.py` — 0 failed (1a-i + 1a-ii still green).
- [ ] `uv run pytest tests/test_pages.py` — 0 failed (extended with 3 new OFAC scenarios).
- [ ] `uv run pytest tests/test_compliance_api.py` — 0 failed (additive `ScoreOut` + `AgentOut` fields).
- [ ] `uv run pytest tests/test_score_api.py` — 0 failed (existing `activity_score` field unchanged).
- [ ] `uv run pytest` — baseline `285 passed, 8 skipped` preserved.
- [ ] `git diff -- app/` shows 4 expected hunks: `pages.py` route, `agent_detail.html` template, `score.py` schema, `agents.py` ScoreOut build.
- [ ] `app/static/js/payment.js` byte-for-byte unchanged.
- [ ] Manual: `GET /agents/{chain}/{token}` for dual-flag agent renders `#hire-cta` with `disabled` + block banner; single-flag renders warning copy only and CTA stays enabled; clean agent renders neither.

---

## 8. Stack order + test pre-seeding matrix

| Sub-PR's test file | Pre-PR state assumed | Pre-seeding required | Pre-seeding mechanism |
| --- | --- | --- | --- |
| `tests/test_compliance_penalty.py` (1a-i) | None (pure unit test) | None | N/A — helper has no DB calls |
| `tests/test_compliance_refresh.py` (1a-ii) | 1a-i merged **OR** raw DDL pre-seeded | Yes (if 1a-i not yet merged) | `_ensure_compliance_schema` autouse fixture (§3.5): raw SQL with `IF NOT EXISTS` / try-except |
| `tests/test_admin_compliance.py` (1a-ii) | Same as above | Same | Same fixture; admin tests mock `run_compliance_refresh` so DB content doesn't matter |
| `tests/test_pages.py` extension (1b) | 1a-i + 1a-ii merged **OR** raw DDL + row pre-seeded | Yes (if earlier sub-PRs not yet merged) | `compliance_seed` fixture (§4.6) inserts one `agent_cache` + one `agent_compliance_flags` row via raw SQL |
| `tests/test_compliance_api.py` (1b) | Same | Same | Same fixture |

**Stack-order implication:** sub-PRs land in the order **1a-i → main → 1a-ii → main → 1b → main**. Tests tolerate out-of-order apply-time rebasing via the pre-seeding fixtures above.

---

## 9. Out-of-scope (unchanged from spec)

No mutation of `activity_score` / `wallet_score` / any other score column. No scheduler work inside the repo (cron, APScheduler, n8n, Celery beat). No heuristic / cluster / prefix / suffix OFAC matching. No `agent_wallet` (payment-wallet) coverage. No home or listing-page UI changes. No changes to `flagged_sync.py`. No compliance-audit log beyond `refreshed_at`. No PayTo / x402 protocol change.

---

## 10. References

- Inputs: `openspec/changes/compliance-flags/{explore.md, proposal.md, spec.md}`
- Format precedent: `openspec/changes/archive/2026-09-15-test-copy-fix/design.md`
- Existing surfaces: `app/services/flagged_sync.py` (mirror); `app/services/agent_score.py` (`materialize_score`, untouched); `app/routers/pages.py` (`_flagged_addresses_set`); `app/routers/sync.py` (`require_sync_key`); `app/routers/agents.py` (`/score`); `app/db/models/agent.py:194–199`; `app/schemas/score.py` (`ScoreOut`); `app/static/js/payment.js` (unchanged).
- Existing tests: `tests/test_flagged.py`, `tests/test_pages.py`, `tests/test_pages_x402.py`, `tests/test_agent_score.py`, `tests/test_score_api.py`.
- Migrations: `0007_flagged_addresses.py` (no change); `0011_fix_onchain_null_array.py` (current head — next slot is `0012_*`).
- Chain context: `DESIGN.md` D8; Phase 2 `wallet-activity-pillar`; Phase 3 `agent-score-integration`.

---

## 11. Key Learnings (design phase)

1. **The 3-way split only fits because 1a-i stops at "schema + model + pure helper."** The discipline: **1a-i = nothing that runs against the DB.** Any attempt to ship the orchestrator or admin router in 1a-i re-introduces the 607-line overage. 1a-ii is the first PR that touches the DB beyond the schema. Without that boundary, the split does not fit.
2. **Pre-seeding fixtures are the cost of the split.** 1a-ii's `_ensure_compliance_schema` fixture re-runs the 1a-i DDL with `IF NOT EXISTS` / try-except; 1b's `compliance_seed` fixture inserts the rows that 1a-ii's orchestrator would have inserted. Both fixtures must use **byte-identical** SQL strings to the production migration / UPSERT — drift here is silent test green / prod red (§6.R-6).
3. **`compute_penalty` returning `Decimal` is load-bearing.** The cap-at-50 assertion (`== Decimal("50.00")`, not `60.00`) is the only test that catches an accidental `int` or `float` return. `Decimal` keeps the boundary clean all the way to `displayed_activity_score = max(0, Decimal_score - Decimal_penalty)` — the read-path math at 1b's route layer never touches IEEE-754.
4. **The split makes the JSONB source-list fields orthogonal to test brittleness.** 1a-i ships the table; 1a-ii populates `creator_flag_sources` / `owner_flag_sources`. If 1a-ii's payload shape ever drifts from 1a-i's column shape, the DB-agnostic idempotence assertion in §5.3 (`json.dumps(..., sort_keys=True)` round-trip) catches it without forcing the test into the Postgres-only skip bucket.
5. **The `payment.js` short-circuit invariant is what makes 1b small.** The entire Hire-CTA gate is server-rendered `disabled` + `aria-disabled="true"` in the template. No new JS file, no new handler, no new event listener. The previous design repeated this invariant; the 3-way split makes it a single source of truth (§5.8).
