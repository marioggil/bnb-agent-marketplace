# Category Study — BNB Agent Marketplace

**Status**: study for team decision — no implementation yet.
**Date**: 2026-08-26
**Data source**: full production catalog via `GET /api/agents` (paginated, 100/page), 1606 agents, live instance.

---

## 1. Executive summary

- The production catalog has **1606 agents**; the current taxonomy (4 investment categories + `other`) covers **~7%** of it. 93% land in `other`.
- Only **323 agents (20%) carry upstream tags** — the only reliable semantic signal (upstream `categories` is empty on 100% of samples; `supported_protocols` is `Web`/`A2A` transport metadata, not activity).
- A proposed **10-category taxonomy** (4 current + 6 new) covers **319 of 323 tagged agents (99%)** — i.e. **~20% of the whole catalog**. The remaining **1283 agents (80%) have no tags** and are mostly generic/placeholder agents (EvoEvo-style); they stay in `other`.
- **Key product decision**: the index is a noisy general-purpose agent universe, not a DeFi-only market. The taxonomy either reflects that (new categories) or the marketplace curates what it shows.

## 2. Methodology

- Full catalog downloaded from production (`/api/agents?page=N&page_size=100`, 17 requests, 1606 items).
- Analysis: tag frequency, tag clusters, tag coverage; category matching simulated with case-insensitive substring hints against tags (exact signal-to-category mapping; no NLP).
- Two simulations: (a) priority-ordered matching (each agent to its first matching category), (b) pure cluster membership (an agent counts in every cluster it matches) — the latter is used for sizing since real-world agents legitimately span categories.

## 3. Current state — why 93% land in `other`

| Cause | Detail |
|---|---|
| Classifier input is wrong field | `_maybe_enrich_category` passes only `supported_protocols` to `compute_category`. In production that field is always `Web` (1319) / `A2A` (772) / `MCP` (38) / `OASF` (3) / `Email` (2) — transport type, not activity. No hint can match. |
| Tags are never passed | Upstream tags (the semantic signal) are stored (`agent_cache.tags`) but never fed to the classifier. |
| Upstream `categories` is empty | 0/100 sampled agents carry it. |

Only `x402_supported` produces real matches (104 → `rebalancing` via the GENERATED column). All other 1502 agents fall through to `other`.

## 4. Data findings

**Tag coverage**

- 323/1606 agents (20%) have tags; 275 of them have 2+ tags.
- 117 distinct tags; long tail dominated by generic capability labels.
- 1283 agents (80%) have **no tags**. Sample review shows mostly generic/placeholder descriptions ("An EvoEvo AI Agent focused on crypto", "EvoEvo agent #4679492") plus some real ones ("AI-driven multi-chain trading agent", "Gasless stablecoin payment agent"). Without tags, classification would need description NLP — out of MVP scope.

**Source-side signals (the bigger find)**

The full agent definition is already synced into our DB: `agent_cache.raw_metadata.offchain_content` (populated for **1606/1606** agents). It carries signals we never look at:

| Signal | Populated | What it is |
|---|---|---|
| `offchain_content.termix.profile.category` | 696 (43%) | **The source platform already classifies each agent** (Termix taxonomy, below) |
| `offchain_content.description` | 1605 | Full description (the listing `description` is often just "… on Termix Platform") |
| `offchain_content.tags` | 696 | Tags from the off-chain definition (listing `tags` only has 323) |
| `offchain_content.type` | 1601 | EIP-8004 registration type |
| `offchain_content.services` | 1506 | A2A / platform service endpoints |
| `offchain_content.skills` | 4 | Published skills (almost nobody publishes them) |
| `offchain_content.capabilities` | 2 | Published capabilities (same) |

**Termix taxonomy (source's own classification)** — 696 classified agents:

| Termix category | Agents | Maps to (proposed) |
|---|---|---|
| Code & Smart Contracts | 488 (30.4%) | dev_automation |
| Data & Research | 54 | data_analytics |
| Writing & Content | 39 | marketing_content |
| Design & Brand | 35 | creative_design |
| Security & Verification | 27 | security_compliance |
| Market & Protocol Research | 23 | grid_trading |
| Automation & Ops | 18 | admin_ops |
| Model & Dataset Ops | 8 | data_analytics |
| general | 4 | — (fall through) |

**Agent "code" availability**: the registry stores each agent's *definition* (off-chain metadata above), not executable code. Agents are live services: `a2a_endpoint` on 703, `mcp_server` on 32, web endpoints on most. Only 4 agents publish `skills` and 2 publish `capabilities` in their metadata; the rest would require querying each A2A endpoint to learn its actual capabilities — out of MVP scope.

**Combined coverage (source category > x402 > tags)**

| Assignment | Agents | Share |
|---|---|---|
| `other` (nothing available) | 810 | 50.4% |
| dev_automation | 488 | 30.4% |
| rebalancing (x402) | 104 | 6.5% |
| data_analytics | 62 | 3.9% |
| marketing_content | 39 | 2.4% |
| creative_design | 35 | 2.2% |
| security_compliance | 27 | 1.7% |
| grid_trading | 23 | 1.4% |
| admin_ops | 18 | 1.1% |

The source category alone covers 43%; tags add nothing beyond it (every tagged agent already carries a Termix category or x402). The remaining 50% is signal-less placeholder inventory.

**Top tags** (count): 3D Modeling 123 · AI Automation 85 · AI Trading 82 · Agent Orchestration 78 · Ad Campaign 75 · AI Agent Development 69 · Alpha Hunter 58 · Arbitrage Bot 48 · API Development 45 · Backend Development 36 · Anti-Phishing 31 · Blog Writing 29 · Bookkeeping 27 · Crypto KOL 25 · Data Analysis 18 · Branding 16 · Data Labeling 16 · Contract Review 15 · Data Engineering 15 · Backtesting 14 · DeFi Yield Optimizer 14 · Code Review 14 · Smart Contract Audit 12 …

**Natural clusters** (tagged agents matching each cluster; an agent can match several):

| Cluster | Tagged agents | Share of tagged |
|---|---|---|
| Dev & Automation | 223 | 69% |
| Creative & Design | 153 | 47% |
| Marketing & Content | 143 | 44% |
| Trading (grid/arbitrage) | 113 | 35% |
| Security & Compliance | 70 | 22% |
| Data & Analytics | 64 | 20% |
| Admin & Ops | 47 | 15% |
| Yield Optimization | 14 | 4% |
| Rebalancing (by tags) | 2 | 1% |
| Health Factor | 0 | 0% |

**Multi-category overlap**: 240/323 tagged agents match 2+ proposed categories (65 match 3+, 8 match 6+) — real agents are multi-capability; matching priority must be explicit.

**Coverage ceiling**: tags cover only 20% of the catalog. Even a perfect classifier cannot move the other 80% without NLP or curation.

## 5. Proposed taxonomy (10 categories + `other`)

Display names, taglines and examples follow DESIGN.md style (novice-first, concrete, no return promises). Sizes = pure cluster membership on tagged agents (floor; real totals after x402/GENERATED are higher for `rebalancing`).

| Slug | Display | Tagline | Example | Signals (tag substrings) | Tagged agents |
|---|---|---|---|---|---|
| `rebalancing` | Rebalancing | Keeps your portfolio balanced | Buys near $10, sells near $12 | rebalanc, portfolio management + x402 (104 more via GENERATED) | 2 (+104 x402) |
| `grid_trading` | Grid Trading | Trades price ranges automatically | Places orders in a band | grid, arbitrage, dca, scalping, backtest, quant, trading bot, technical analysis, alpha hunter, smart money, ai trading | ~113 (+AI Trading 82 if folded in) |
| `yield_optimisation` | Yield Optimization | Finds better yields for your assets | Moves funds to the best vault | yield, defi, farm, staking, vault, lend, liquidity | 14 |
| `health_factor_monitoring` | Health Factor | Protects your collateral | Warns before your position drops | health factor, liquidation, collateral, risk management | 0 (product category, no data yet) |
| `dev_automation` | Dev & Automation | Builds, connects and automates | Turns an API into a workflow | agent development, automation, orchestration, api, backend, bot, browser, workflow, software, mobile, web, llm, chatbot, function calling, multi-agent, prompt engineering, computer vision, node, rpc | 223 |
| `creative_design` | Creative & Design | Makes images, video and brands | Turns a sketch into a logo | 3d modeling, image, video, graphic design, illustration, logo, nft art, animation, ui/ux, branding, design system | 153 |
| `marketing_content` | Marketing & Content | Writes, publishes and grows | Turns an idea into a campaign | ad campaign, kol, influencer, seo, social media, email, blog, copywriting, content, translation, pr, growth | 143 |
| `data_analytics` | Data & Analytics | Cleans, studies and explains data | Turns raw data into a report | data analysis/labeling/engineering/entry/extraction/visualization, web research, market research, business analysis, sql, embeddings | 64 |
| `security_compliance` | Security & Compliance | Audits and protects on-chain | Finds the hole before the hacker | anti-phishing, wallet security, contract review, smart contract audit, security review, bug bounty, forensics, compliance, due diligence, legal | 70 |
| `admin_ops` | Admin & Ops | Runs the back office | Keeps the books in order | bookkeeping, customer support, virtual assistant, email management, project/product management, report writing | 47 |
| `other` | Other | — | — | no matching signal | 1283 (no tags) + ~4 |

**Coverage simulation (priority-ordered)**: grid_trading 113 · creative_design 96 · marketing_content 47 · dev_automation 35 · data_analytics 14 · yield_optimisation 9 · security_compliance 4 · admin_ops ~0–47 (depends on priority) · rebalancing 1 · health_factor 0 · `other` 1287. Priority order matters: Dev & Automation would absorb most multi-tag agents if placed first.

## 6. Open decisions for the team

1. **The 80% untagged**: keep them in `other` (recommended — honest bucket, no NLP in MVP), hide them from category views, or curate manually. Recommendation: keep `other`; revisit with a curation or description-NLP pass later.
2. **Hero redesign**: 4 category cards → 10. Requires a DESIGN.md update (grid 2×2 → 2×5 or scrollable; or reduce to 8 by merging Admin & Ops into Data, and Security into Dev). Team decision on how many categories the 30-second jury should see.
3. **Matching priority**: define a canonical category order for multi-tag agents (recommended: trading → yield → security → dev → creative → marketing → data → admin; strongest, most-specific signal first).
4. **Fold-in questions**: `AI Trading` (82) into Grid Trading? `Health Factor` keeps a card with zero inventory (product bet) or is hidden until data exists?
5. **Implementation shape** (if approved): pass `tags` into `compute_category` + refined hints (low risk, worker-only), then UI (filter options + hero) — two separate changes, each reviewable.

## 7. Recommendation

Phase 1 — **classifier**: consume the **source-side category** (`termix.profile.category`, mapped to the proposed taxonomy) as the primary signal — it already classifies 43% of the catalog with zero ML — then tags, then x402, then `other`. Pure backend change in `_maybe_enrich_category` (data is already in `agent_cache.raw_metadata`); no product-surface change. ~50% of the catalog becomes browseable by category.

Phase 2 — **surface** (team decision first): DESIGN.md taxonomy update (10 categories, hero layout, filter labels) and UI implementation.

The study deliberately does not implement anything: taxonomy is a product decision.

## 8. Field recommendations: classification and agent scoring

Measured against the full 1606-agent catalog (population, discriminative power, verifiability).

### 8.1 Classification fields (what the agent does) — priority order

| # | Field | Populated | Verdict |
|---|---|---|---|
| 1 | `raw_metadata.offchain_content.termix.profile.category` | 696 (43%) | **Primary** — source-assigned category, zero ML, maps 1:1 to our taxonomy |
| 2 | `raw_metadata.offchain_content.tags` | 696 (43%) | **Secondary** — definition tags; strictly better than the listing `tags` (323) |
| 3 | `x402_supported` | 104 | Keep: current `rebalancing` mapping |
| 4 | `raw_metadata.offchain_content.description` | 352 rich (>100 chars) | **Future** — description NLP for the ~50% with no tags/category |
| 5 | `services.a2a` | 703 | Weak signal: A2A ≈ real agentic service vs plain web endpoint (778 web / 97 none) |
| 6 | `raw_metadata.onchain.platform` | 777 EvoEvo + 696 Termix + 133 unknown | Groups by origin platform, **not** activity — do not classify on it, but useful as a filter |
| 7 | `skills` / `capabilities` | 4 / 2 | Negligible — ignore until an A2A enricher exists |
| 8 | `supported_protocols` | 1606 | Transport metadata (Web/A2A/MCP) — **not** a classification signal |

### 8.2 Agent scoring fields (best agents) — what discriminates today

| # | Field | Populated | Non-zero | Verdict |
|---|---|---|---|---|
| 1 | `health_score` / `health_status` | 654 | 561 (mean 19.8, max 76.67) | **Primary quality** — upstream-measured endpoint health (latency, domain_verified, verification_status per service) |
| 2 | `metadata_completeness_score` | 1606 | 239 (max 68) | **Primary quality** — how complete the declared definition is |
| 3 | `scores.breakdown` (v5_leaderboard_policy) | 239 | compliance 239, service 45, publisher 8, momentum 1 | **Secondary** — upstream's own weighted scoring |
| 4 | `parse_status` | 1606 | 0 errors, 4 warnings | **Gate** — malformed metadata disqualifies |
| 5 | `is_verified` / `is_endpoint_verified` | 0 / 0 | — | **Future** — trust signal once the ecosystem verifies |
| 6 | `x402_supported` + `agent_wallet` | 104 / 1606 | — | **Hireability bonus** — a payable agent is a real one |
| 7 | `built_with` (BNB Chain SDK) | 2 | 2 | **Ecosystem bonus** — official SDK provenance |
| 8 | `wallet_score` | 1606 | 8 (max 17.78) | Weak — include when it grows |
| 9 | `freshness_score` | 1606 | 1 | Negligible today |
| 10 | usage fields: `activity_score`, `popularity_score`, `quality_score`, `total_feedbacks`, `total_validations`, `successful_validations`, `star_count`, `watch_count`, `rank`, `network_rank` | all 0 | — | **Future usage score** — the whole catalog is green; these become the ranking signal once real usage exists |

### 8.3 Proposed scoring model (two scores)

**A. Agent Quality Score (verifiable, works today)** — replaces the all-zero `average_score` in cards:

```
quality_mvp = 0.45 · health_score
            + 0.25 · metadata_completeness_score
            + 0.15 · parse_quality            # 100 if 0 errors; −20/error, −5/warning
            + 0.10 · verification             # is_verified or is_endpoint_verified: 100 else 0 (0 today, activates later)
            + 0.05 · hireability              # x402_supported and agent_wallet: 100 else 0
            + bonus: built_with (BNB SDK) +5
```

Verifiable signals only — no self-reported behavior. Ties to DESIGN.md D7: this is the "objective base layer" of the score.

**B. Agent Usage Score (future)** — activates when the ecosystem produces data:

```
usage = 0.40 · activity_score
      + 0.25 · popularity_score
      + 0.20 · (feedbacks + validations normalized)
      + 0.15 · hires (our own paid hires)
```

DESIGN.md D7's "social layer" (star ratings) plugs into the same slot later.

### 8.4 Notes

- Classification and quality scoring are **independent axes**: category says *what it is*, quality says *how good the artifact is*. Do not mix them.
- `platform` (EvoEvo/Termix) is a filter, not a score — Termix agents happen to have richer metadata (696 with category), but that is a data-quality artifact, not merit.
- Recompute quality on every sync (cheap, from stored fields); usage score only after real usage exists.