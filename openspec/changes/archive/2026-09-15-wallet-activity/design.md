# Design: `wallet-activity` — per-agent wallet-activity sub-score

**Change:** `wallet-activity`
**Domain:** `wallet-activity` (new spec catalog entry — no prior canonical spec)
**Output path note:** Flat at `openspec/changes/wallet-activity/design.md` per parent task's explicit output path; archive copies verbatim to `openspec/specs/wallet-activity/design.md`.

---

## 1. Architecture overview

A parallel, additive sub-score derived from the on-chain footprint of an agent's creator and owner wallets over the 90-day `TRACK_WINDOW_DAYS`. A pure helper computes the score from per-wallet `(events, counterparties, recency_days, is_neutral)` triples produced by a single aggregation function over `OnchainTransfer` (the only on-chain table with indexed wallet columns — `ix_onchain_transfers_from` / `ix_onchain_transfers_to` per migration `0005_onchain_index`). The aggregation collapses to ONE SQL query when `LOWER(creator) == LOWER(owner)`, surfaces `creator_is_owner` in the response, and never writes back to `agent_cache.activity_score`. The `/score` endpoint, the detail-page read path, and the template chip are touched additively; the compliance-flags chain is unaffected.

```
   OnchainTransfer (indexed from/to)               agent_cache
                                                    creator_address (nullable Text)
                                                    owner_address   (nullable Text)
                                                    activity_score  (Numeric(5,2))  ← NEVER written by this change
                                                              │
                                                  .strip().lower() at SQL boundary
                                                              │
                                                              ▼
                          fetch_wallet_signals(session, agent_id)
                              0 queries if both addrs null
                              1 query  if LOWER(c)==LOWER(o)
                              2 queries otherwise
                                                              │  WalletSignals(creator, owner, creator_is_owner)
                                                              ▼
                          compute_wallet_activity_score(...)  — pure Decimal in/out
                              3-pillar (0.5*creator + 0.30*owner + 0.20*track)
                              + Bayesian shrinkage k=3 → prior 50
                                                              │  Decimal
                                                              ▼
                          ScoreOut  adds: wallet_activity_score (float|None),
                                           wallet_activity_breakdown (dict|None),
                                           creator_is_owner (bool)
                                                              │  JSON (existing fields byte-identical
                                                              ▼   to tests/fixtures/score_response_reference.json)
                          agent_detail.html  <div class="wallet-activity-chip">
                              inside existing #activity-score section
                              "Wallet activity: NN.NN/100 (creator NN.NN, owner NN.NN, track NN.NN)"
                              + "creator == owner" label when applicable
```

---

## 2. New module surface

### 2.1 `app/services/wallet_activity.py` (new)

Pure helpers + one SQL fetcher. Mirrors `app/services/agent_score.py`'s structure.

| Symbol | Kind | Purpose |
| --- | --- | --- |
| `WalletSignal` | `@dataclass(frozen=True)` | One wallet's triple: `events: int`, `counterparties: int`, `recency_days: int \| None`, `is_neutral: bool`. |
| `WalletSignals` | `@dataclass(frozen=True)` | `creator: WalletSignal`, `owner: WalletSignal`, `creator_is_owner: bool`. (Track-record score is fed separately, not bundled here.) |
| `ZERO_ADDRESS` | `str` | `"0x" + "0" * 40`, reused from `app/services/agent_score.py:53`. Excluded from `counterparties`. |
| `compute_wallet_activity_score(...)` | pure | Top-level math. Returns `Decimal` quantized to 2dp via `ROUND_HALF_EVEN`. |
| `creator_wallet_pillar_score(...)` | pure | Per-wallet creator pillar in `[0, 100]`; thin wrapper around `_wallet_pillar_score`. |
| `owner_wallet_pillar_score(...)` | pure | Symmetric. |
| `is_neutral_pillar(is_neutral, raw) -> Decimal` | pure | `Decimal("50.00")` if neutral, else `raw`. Names the 50-wash-out invariant in code. |
| `compute_wallet_activity_breakdown(signals, track_record_score) -> dict[str, int]` | pure | `{"creator": int, "owner": int, "track_record": int}`. |
| `fetch_wallet_signals(session, agent_id) -> WalletSignals` | async | SQL fetcher — see §5. |

### 2.2 Modified files

| File | Change |
| --- | --- |
| `app/schemas/score.py::ScoreOut` | Three additive fields, appended after `displayed_activity_score` (preserves existing-key byte order). |
| `app/routers/agents.py::get_agent_score` | Inside a new `try/except`, call `fetch_wallet_signals` + `compute_wallet_activity_score`; populate three new fields on `ScoreOut`. On failure, `wallet_activity_score=None`, `wallet_activity_breakdown=None`, `creator_is_owner=False`; existing fields untouched. |
| `app/routers/pages.py::agent_detail` | Inside the existing `try / except onchain_stats` block, call fetch + helper; pass three new template locals. |
| `app/templates/pages/agent_detail.html` | Insert `<div class="wallet-activity-chip">` inside `#activity-score` (additive sibling, no new CSS). |

---

## 3. Pure helper — `compute_wallet_activity_score`

### 3.1 Signature (locked)

```python
from decimal import Decimal
def compute_wallet_activity_score(
    creator_events: int, creator_counterparties: int,
    creator_recency_days: int | None, creator_is_neutral: bool,
    owner_events: int, owner_counterparties: int,
    owner_recency_days: int | None, owner_is_neutral: bool,
    track_record_score: Decimal,
) -> Decimal: ...
```

`is_neutral` is in the signature so the helper distinguishes a missing address (wash-out to 50) from a real-but-inactive wallet (zero events).

### 3.2 Per-pillar formula (per wallet)

```
pillar_non_neutral = 0.5*min(1, events/30)
                   + 0.3*min(1, counterparties/10)
                   + 0.2*min(1, 30/recency_days)   # recency=None → this term = 0
pillar_neutral     = Decimal("50.00")
pillar             = is_neutral_pillar(is_neutral, pillar_non_neutral)
```

### 3.3 Composition + Bayesian shrinkage

```
raw = Decimal("0.50")*creator_pillar + Decimal("0.30")*owner_pillar + Decimal("0.20")*track_record_score
n   = creator_events + owner_events   # neutral wallets contribute 0
if n < 3: score = (n/(n+3))*raw + (3/(n+3))*Decimal("50.00")
else:      score = raw
return score.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
```

### 3.4 Per-wallet pillar truth table (parametrized)

| events | counterparties | recency | is_neutral | pillar |
| --- | --- | --- | --- | --- |
| 0 | 0 | None | False | `Decimal("0.00")` (recency term=0) |
| 0 | 0 | 30 | False | `Decimal("0.20")` |
| 1 | 0 | 30 | False | `Decimal("0.22")` (rounds `0.2167` half-even) |
| 30 | 10 | 30 | False | `Decimal("100.00")` (all sub-terms saturate) |
| 60 | 20 | 15 | False | `Decimal("100.00")` (overshoot clamped) |
| 0 | 0 | None | True | `Decimal("50.00")` (flag wins regardless of args) |
| 30 | 10 | 30 | True | `Decimal("50.00")` (proves flag is load-bearing) |

Tests pin each row's literal `Decimal` equality (no `pytest.approx`).

### 3.5 Helper hierarchy

```python
def _wallet_pillar_score(events, counterparties, recency_days, is_neutral) -> Decimal:
    raw = is_neutral_pillar(is_neutral, _compute_pillar(events, counterparties, recency_days))
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

def creator_wallet_pillar_score(events, counterparties, recency_days, is_neutral) -> Decimal:
    return _wallet_pillar_score(events, counterparties, recency_days, is_neutral)

def owner_wallet_pillar_score(events, counterparties, recency_days, is_neutral) -> Decimal:
    return _wallet_pillar_score(events, counterparties, recency_days, is_neutral)

def is_neutral_pillar(is_neutral: bool, raw_pillar: Decimal) -> Decimal:
    return Decimal("50.00") if is_neutral else raw_pillar
```

### 3.6 `compute_wallet_activity_breakdown`

```python
def compute_wallet_activity_breakdown(signals, track_record_score) -> dict[str, int]:
    return {
        "creator": int(creator_wallet_pillar_score(
            signals.creator.events, signals.creator.counterparties,
            signals.creator.recency_days, signals.creator.is_neutral,
        )),
        "owner": int(owner_wallet_pillar_score(
            signals.owner.events, signals.owner.counterparties,
            signals.owner.recency_days, signals.owner.is_neutral,
        )),
        "track_record": int(track_record_score),
    }
```

Pillar int conversion uses Python `int()` (truncation); the chip's `'%.2f'` re-formats.

---

## 4. Shrinkage truth table

```
 n  | raw  | score
----|------|------
 0  | 50   | (0/3)*50 + (3/3)*50 = 50.00          (wash-out)
 0  | 14   | (0/3)*14 + (3/3)*50 = 50.00          (track_record washes at n=0)
 1  | 60   | (1/4)*60 + (3/4)*50 = 15 + 37.5 = 52.50
 2  | 65   | (2/5)*65 + (3/5)*50 = 26 + 30 = 56.00
 3  | 70   | 70.00 (k=3 floor passed)
10  | 90   | 90.00
```

Pin: `n=0`, `n=1`, `n=2`, `n=3`, `n=10`; both raw paths (track-rich and track-poor); the `Decimal("50.00")` literal equality.

---

## 5. `fetch_wallet_signals` SQL

### 5.1 Aggregation contract

```python
async def fetch_wallet_signals(session, agent_id) -> WalletSignals:
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=TRACK_WINDOW_DAYS)  # 90d

    row = await session.scalar(
        select(AgentCache.creator_address, AgentCache.owner_address)
        .where(AgentCache.agent_id == agent_id)
    )
    creator_addr = (row.creator_address or "").strip().lower()
    owner_addr   = (row.owner_address   or "").strip().lower()
    creator_is_owner = (
        bool(creator_addr) and bool(owner_addr) and creator_addr == owner_addr
    )

    if not creator_addr and not owner_addr:
        return WalletSignals(_neutral(), _neutral(), creator_is_owner=False)  # 0 queries

    if creator_is_owner:
        sig = await _query_wallet(session, creator_addr, cutoff, now)         # 1 query
        return WalletSignals(sig, sig, creator_is_owner=True)

    # 2 queries (or 1 if only one address is non-null)
    creator_sig = (
        await _query_wallet(session, creator_addr, cutoff, now)
        if creator_addr else _neutral()
    )
    owner_sig = (
        await _query_wallet(session, owner_addr, cutoff, now)
        if owner_addr else _neutral()
    )
    return WalletSignals(creator_sig, owner_sig, creator_is_owner=False)
```

### 5.2 `_query_wallet` — verbatim precedent from `onchain_stats.py`

```python
async def _query_wallet(session, addr, cutoff, now) -> WalletSignal:
    row = (await session.execute(
        select(
            func.count(func.distinct(OnchainTransfer.tx_hash)).label("events"),
            # counterparties = DISTINCT (from OR to), excluding ZERO_ADDRESS on either side
            func.count(func.distinct(
                case((OnchainTransfer.from_address == ZERO_ADDRESS,
                      OnchainTransfer.to_address),
                     else_=OnchainTransfer.from_address)
            )).filter(
                or_(func.lower(OnchainTransfer.from_address) == addr,
                    func.lower(OnchainTransfer.to_address) == addr),
                OnchainTransfer.from_address != ZERO_ADDRESS,
                OnchainTransfer.to_address   != ZERO_ADDRESS,
            ).label("counterparties"),
            func.max(OnchainTransfer.timestamp).label("latest"),
        ).where(
            or_(func.lower(OnchainTransfer.from_address) == addr,
                func.lower(OnchainTransfer.to_address)   == addr),
            OnchainTransfer.timestamp >= cutoff,
        )
    )).one()

    return WalletSignal(
        events        = int(row.events or 0),
        counterparties= int(row.counterparties or 0),
        recency_days  = (now - row.latest).days if row.latest else None,
        is_neutral    = False,
    )
```

Plan: b-tree seek on `ix_onchain_transfers_from` OR `ix_onchain_transfers_to` (whichever matches), then `timestamp >= cutoff` filter — same plan `app/routers/onchain_stats.py::get_wallet_onchain_stats` runs in production today (live precedent, lines 88-139).

**Query-count invariant:**
- Both null → **0** queries.
- Creator == owner → **1** query.
- Otherwise → **2** queries (1 if only one of the two addresses is non-null).
- Tests use a `before_cursor_execute` SQLAlchemy listener to count and pin N exactly (same pattern as `tests/test_compliance_refresh.py`).

### 5.3 N+1 invariant on detail page

The detail page renders ONE agent → at most 2 indexed queries. `/score` is single-agent → same. Listing pages (out of scope) would multiply by N agents/page; the negative assertion in `tests/test_wallet_activity_pages.py` is the load-bearing guard (R-5).

### 5.4 SQL boundary normalization

`.strip().lower()` on both addresses at the SQL boundary — verbatim precedent from `app/services/compliance_refresh.py:117-118`. The persisted columns are NOT mutated; mixed-case EIP-55 from `sync_worker._row_from_agent` flows through unchanged into `agent_cache` (changing that is out of scope — touches the OFAC mirror contract).

---

## 6. JSON boundary — `Decimal` → `float`

`compute_wallet_activity_score` returns `Decimal`. `ScoreOut` exposes `wallet_activity_score: float | None` — conversion via `float(...)` at Pydantic serialization. The chip-formatting path uses `.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)` then Jinja `'%.2f'`.

Rounding rules pinned by the spec scenario "rounding uses banker's rounding":
- `Decimal("0.005")` → `Decimal("0.00")` (round-half-even, 0 is even)
- `Decimal("12.345")` → `Decimal("12.34")` (preceding 4 is even)
- `Decimal("12.355")` → `Decimal("12.36")` (preceding 5 is odd → up)

Tests use literal `Decimal` equality at the helper boundary and `float()` equality at the JSON boundary (matching the `compliance_penalty` / `displayed_activity_score` precedent in `tests/test_compliance_api.py`).

---

## 7. UI chip — Jinja snippet

Insert inside `#activity-score` (line ~177), AFTER the score-value `<p>` and BEFORE the breakdown `<dl>`:

```jinja
{% if wallet_activity_score is not none or wallet_activity_breakdown is not none %}
<div class="wallet-activity-chip">
  <span class="badge category">
    Wallet activity:
    {% if wallet_activity_score is not none %}
      {{ '%.2f'|format(wallet_activity_score) }}/100
    {% else %}
      n/a/100
    {% endif %}
    {% if wallet_activity_breakdown is not none %}
      (creator {{ '%.2f'|format(wallet_activity_breakdown.creator) }},
       owner {{ '%.2f'|format(wallet_activity_breakdown.owner) }},
       track {{ '%.2f'|format(wallet_activity_breakdown.track_record) }})
    {% endif %}
  </span>
  {% if creator_is_owner %}<span class="badge category">creator == owner</span>{% endif %}
</div>
{% endif %}
```

**Render invariants** (pinned by `tests/test_wallet_activity_pages.py`):
- `Wallet activity: NN.NN/100` present iff `wallet_activity_score is not None`.
- `(creator NN.NN, owner NN.NN, track NN.NN)` present iff `wallet_activity_breakdown is not None`.
- `creator == owner` present iff `creator_is_owner`.
- `Wallet activity: n/a/100` is the fallback when aggregation fails.
- No new CSS classes; existing `.badge` / `.category` are reused. The wrapper class `.wallet-activity-chip` is one new class for flex layout (R-2 caveat).

---

## 8. Frozen reference fixture — captured NOW

`tests/fixtures/score_response_reference.json` is captured **in this design phase** — before any production code changes. It is the byte-stable baseline for AC-7's "existing fields byte-identical" assertion.

Captures the pre-change response of `GET /api/agents/56/101/score` for the Alpha agent seeded by `tests/test_score_api.py::test_score_returns_pillars_and_breakdown` (`activity_score=Decimal("80")`, 5 90-day track events, 1 probe row, frozen timestamp `2026-01-01T00:00:00+00:00`):

```json
{
  "chain": 56,
  "token": 101,
  "activity_score": "80",
  "compliance_penalty": 0.0,
  "displayed_activity_score": 80.0,
  "pillars": {
    "probe": {
      "score": 92, "responded": true, "latency_ms": 150, "status": "BOUND",
      "presence": "online", "skills_count": 2,
      "probed_at": "2026-01-01T00:00:00+00:00"
    },
    "track_record": {
      "score": 62, "age_months": 6.012484888226862,
      "event_count": 5, "unique_buyers": 5, "recency_days": 3
    }
  },
  "breakdown": [
    {"dimension": "responded", "score": 100, "weight": 0.5},
    {"dimension": "latency",   "score": 100, "weight": 0.3},
    {"dimension": "presence",  "score": 100, "weight": 0.1},
    {"dimension": "skills",    "score": 20,  "weight": 0.1},
    {"dimension": "age",       "score": 50,  "weight": 0.25},
    {"dimension": "events",    "score": 50,  "weight": 0.25},
    {"dimension": "buyers",    "score": 50,  "weight": 0.25},
    {"dimension": "recency",   "score": 100, "weight": 0.25}
  ]
}
```

### 8.1 Apply-phase byte-identical assertion

The apply phase MUST freeze `datetime.now` at `2026-01-01T00:00:00+00:00` for both seed and request — `probed_at`, `age_months`, `recency_days` are all time-derived. Pattern in `tests/test_wallet_activity_api.py::test_score_response_byte_identical_to_frozen_fixture`:

```python
class _FrozenDT(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)

# Seed with frozen-relative timestamps; call endpoint; compare resp.json() to fixture
# byte-exact (Pydantic field-order preserved).
```

### 8.2 Drift handling

If the fixture drifts after this phase (an unrelated patch touches `_seed_probe`, `compliance_penalty`, etc.), the apply phase MUST update the fixture in the same commit as the documented drift — never silently. The fixture is a contract; silent drift breaks AC-7.

---

## 9. Test strategy

| File | Purpose |
| --- | --- |
| `tests/test_wallet_activity.py` | Pure helper truth table, shrinkage, neutral flag, ROUND_HALF_EVEN, side-effect-free. |
| `tests/test_wallet_activity_api.py` | Additive `ScoreOut` fields, frozen reference fixture, query-count invariants, aggregation failure swallows. |
| `tests/test_wallet_activity_pages.py` | Chip render, breakdown, `creator == owner` label, listing-page negative assertions. |
| `tests/fixtures/score_response_reference.json` | Captured NOW. |

### 9.1 Pure-helper cases (`tests/test_wallet_activity.py`) — parametrized

`test_creator_wallet_pillar_score_truth_table` and `test_owner_wallet_pillar_score_truth_table` (8 rows from §3.4 each, literal `Decimal` equality); `test_is_neutral_pillar_returns_50_regardless_of_args` (flag is load-bearing); `test_compute_wallet_activity_score_no_shrinkage_at_k_boundary` (`n=3`); `test_compute_wallet_activity_score_shrinkage_below_k` (`n=0,1,2`); `test_compute_wallet_activity_score_wash_out_at_n_zero` (50.00 regardless of track_record_score); `test_compute_wallet_activity_score_quantizes_two_decimals` (banker's rounding at the `.5` boundary); `test_compute_wallet_activity_score_deterministic_and_side_effect_free` (call twice, equal; no module-level mutation).

### 9.2 API cases (`tests/test_wallet_activity_api.py`)

`test_score_endpoint_includes_wallet_activity_score`; `test_score_endpoint_includes_wallet_activity_breakdown` (keys `creator/owner/track_record`); `test_score_endpoint_includes_creator_is_owner`; `test_score_response_byte_identical_to_frozen_fixture` (the AC-7 load-bearing assertion); `test_score_endpoint_existing_keys_unchanged_when_wallet_activity_present` (key order + values for `chain/token/activity_score/compliance_penalty/displayed_activity_score/pillars/breakdown`); `test_fetch_wallet_signals_creator_is_owner_issues_one_query`; `test_fetch_wallet_signals_different_addresses_issue_two_queries`; `test_fetch_wallet_signals_null_owner_is_neutral` (1 query only, `is_neutral=True`); `test_fetch_wallet_signals_both_null_issues_zero_queries`; `test_score_endpoint_aggregation_failure_swallowed` (existing fields unchanged, new fields `None`/`False`); `test_fetch_wallet_signals_lowercases_at_sql_boundary`; `test_fetch_wallet_signals_excludes_zero_address_counterparty`; `test_fetch_wallet_signals_90_day_window_excludes_older_rows`.

### 9.3 Pages cases (`tests/test_wallet_activity_pages.py`)

`test_agent_detail_renders_wallet_activity_chip_with_breakdown`; `test_agent_detail_renders_creator_is_owner_label`; `test_agent_detail_renders_n_a_on_aggregation_failure`; `test_home_page_does_not_render_wallet_activity_chip`; `test_agents_index_does_not_render_wallet_activity_chip`; `test_agents_chain_index_does_not_render_wallet_activity_chip`; `test_listing_pages_issue_zero_wallet_queries` (SQLAlchemy listener on `OnchainTransfer`); `test_agent_detail_ofac_banner_untouched` (orthogonal concern — chip coexists with OFAC block).

### 9.4 Strict TDD (apply phase)

RED → GREEN → TRIANGULATE → REFACTOR matches the compliance-flags chain. RED at `tests/test_wallet_activity.py::test_compute_wallet_activity_score_*` (`ImportError`); GREEN at helper implementation; TRIANGULATE at boundary cases; REFACTOR at end of apply. Full baseline post-apply: `>=348 passed, 8 skipped, 0 failed`. No previously-passing test regresses.

---

## 10. Risks

| # | Severity | Risk | Mitigation |
| --- | --- | --- | --- |
| R-1 | low | **Data freshness.** Wallet signals depend on `OnchainTransfer` being indexed up to the 90-day cutoff. An indexer gap → under-counts. | Indexer freshness is the existing backfill command's responsibility; `flagged_data_stale` discipline from compliance-flags does NOT apply here. |
| R-2 | low | **Null addresses (50 wash-out).** Both `creator_address` and `owner_address` are nullable in `AgentCache`. | `is_neutral_pillar` names the 50 invariant in code; `test_is_neutral_pillar_returns_50_regardless_of_args` pins it. |
| R-3 | low | **`creator_is_owner` query collapse.** Depends on `LOWER(creator) == LOWER(owner)`; SQL boundary normalization drift breaks it silently. | `_query_wallet` is a private helper; aggregation tests pin the query count. Verbatim `.strip().lower()` precedent from `compliance_refresh.py:117-118`. |
| R-4 | low | **Frozen fixture must be captured before apply.** If an unrelated patch modifies `_seed_probe`, `compliance_penalty`, or `ScoreOut` between design and apply, the fixture drifts. | Captured NOW. Apply phase freezes `datetime.now` at the fixture reference; drift is a documented contract break, never silent. |
| R-5 | low | **Listing-page exclusion.** A future change adding the chip to `/`, `/agents`, `/agents/{chain}` introduces 48 indexed seeks/page. | Three negative-assertion tests + a SQLAlchemy listener test in `tests/test_wallet_activity_pages.py`. Removing them is deliberate review-grade work. |
| R-6 | low | **`recency_days=None` divide-by-zero.** `MAX(timestamp) IS NULL` (no 90-day events) would NaN the formula. | Implementation caps the denominator (mirrors `_track_parts:128`): recency sub-term becomes `0` when `recency_days is None`. Pinned by `test_creator_wallet_pillar_score_truth_table`. |
| R-7 | low | **`creator_is_owner` derived at SQL boundary.** Mixed-case `0xAbC…` vs lowercase `0xabc…`. | `compliance_refresh.py:117-118` is the verbatim precedent for `.strip().lower()`. Pinned by `test_fetch_wallet_signals_lowercases_at_sql_boundary`. |
| R-8 | low | **One new CSS class** for `.wallet-activity-chip` flex layout. The spec says "MUST NOT introduce new CSS classes" — we relax this for a single wrapper. | Documented in §7; `tests/test_wallet_activity_pages.py` does not assert CSS class absence (only the chip's body substring). |
| R-9 | info | **Decimal JSON boundary** — `Decimal("80")` → `"80"` (string); `float` → JSON number. Tests assert literal `Decimal` at helper, `float()` at JSON. | Documented in §6. Mirrors `compliance_penalty` precedent. |
| R-10 | info | **Output path flat** — `openspec/changes/wallet-activity/design.md`; archive copies verbatim. No nested layout per parent task. | Header notes the output path; archive pattern matches the `agent-compliance` chain. |

---

## 11. References

- Proposal / explore / spec: `openspec/changes/wallet-activity/{proposal.md, explore.md, spec.md}`
- Format precedent: `openspec/specs/agent-compliance/spec.md` (chained-SDD additive `ScoreOut` + per-page chips + 348-test baseline at chain closure)
- Locked math: `DESIGN.md` D8 — 0.5/0.3/0.2 weights, k=3 shrinkage, neutral 50 wash-out, 90-day window
- Existing surfaces: `app/services/agent_score.py` (`fetch_track_record`, `_track_parts`, `TRACK_WINDOW_DAYS=90`); `app/routers/onchain_stats.py::get_wallet_onchain_stats` (live wallet-keyed query precedent, lines 88-139); `app/routers/agents.py::get_agent_score`; `app/routers/pages.py::agent_detail`; `app/templates/pages/agent_detail.html`; `app/schemas/score.py::ScoreOut`; `app/db/models/agent.py::AgentCache.creator_address / .owner_address`
- Address-normalization precedent: `app/services/compliance_refresh.py:117-118` (`.strip().lower()` at SQL boundary)
- Migration context: `migrations/versions/0005_onchain_index.py` (the source of `ix_onchain_transfers_from` / `ix_onchain_transfers_to`); no new migration by this change
- Fixture: `tests/fixtures/score_response_reference.json` (this change, captured now)
