# DESIGN.md — BNB Agent Marketplace

Design system for the BNB Agent Marketplace: the single source of truth for visual identity, UI components, copy tone, and aesthetic rules. AI agents generating pages, components, or copy MUST read this file and follow it; humans use it to keep the product consistent.

It reflects the current implementation (`app/static/css/site.css`, Jinja2 templates) and the design direction agreed in the team chat (mockups by Katerin, product strategy by the team). All open design decisions have been resolved by the team — see the [Decisions](#decisions) table. Roadmap items (T2/T3 trust features, marketing landing) are explicitly marked and must not be built silently.

## Design principles

1. **Brutally simple UX — the jury understands the product in 30 seconds.** Every page answers: what is this, what can I do, what happens next.
2. **Transparency over promises.** We never say "this agent will make you money". We say "this agent did exactly what it said it would — here is the proof". No investment advice framing anywhere.
3. **Speak to the crypto novice.** The user does not need to understand what an agent is. No jargon without explanation; concrete examples always ("Buys near $10, sells near $12").
4. **Trust is the product.** Agent, creator, and payment wallet trust signals are first-class UI, not an afterthought.
5. **Consistency beats decoration.** One token set, one button style, one card anatomy. Never introduce ad-hoc colors, sizes, or classes.

## Quick path (how to apply)

1. Tokens live in `:root` of `app/static/css/site.css` (dark theme, implemented). Never introduce hardcoded values; refactor to variables when touching existing code.
2. Build components from the [Components](#components) section; never style ad-hoc in templates or JS.
3. Write copy following [Copy & tone](#copy--tone); keep the "Hire" terminology.
4. For every new page/partial, run the [Checklist](#checklist) before merging.

---

## Design tokens

### Color — Dark theme (PRIMARY, design basis)

Decided (D4/D5): the design system is based on the team mockups — dark navy background with teal/cyan accents.

| Token | Value | Usage |
|---|---|---|
| `--color-bg` | `#0f172a` | Page background |
| `--color-surface` | `#1e293b` | Cards, panels |
| `--color-surface-hover` | `#243044` | Card hover |
| `--color-border` | `#2a3a4a` | Borders |
| `--color-text` | `#ffffff` | Primary text |
| `--color-text-secondary` | `#94a3b8` | Secondary text |
| `--color-text-muted` | `#64748b` | Muted text |
| `--color-accent` | `#14b8a6` | Primary accent (teal) — CTAs, highlights |
| `--color-accent-alt` | `#38bdf8` | Secondary accent (cyan) — links, icons |
| `--color-surface-muted` | `#334155` | Placeholder images, chips, category badges |
| `--color-border-strong` | `#3a4a5a` | Form inputs, active borders |
| `--color-bnb` | `#f0b90b` | BNB Chain association (x402 badge, brand) |
| `--color-success` | `#22c55e` | Verified, paid, positive |
| `--color-warning` | `#f59e0b` | Warning, pending |
| `--color-danger` | `#ef4444` | Errors, failed |
| `--color-danger-bg` | `#3a1215` | Error toast background |

### Color — Light theme (legacy — migration period only)

The app now runs the dark theme (design-alignment, 2026-08). Keep these tokens for the migration period; do not build new UI in light.

| Token | Value | Usage |
|---|---|---|
| `--color-bg` | `#fafafa` | Page background |
| `--color-surface` | `#ffffff` | Cards, header, panels |
| `--color-surface-muted` | `#f0f0f0` | Placeholder images, chips |
| `--color-text` | `#1a1a1a` | Primary text |
| `--color-text-secondary` | `#555555` | Meta text, descriptions |
| `--color-text-muted` | `#666666` | Footer, hints |
| `--color-border` | `#eeeeee` | Card/header borders |
| `--color-border-strong` | `#dddddd` | Pagination, active borders |
| `--color-brand` | `#f0b90b` | BNB yellow — legacy brand accent |
| `--color-success` | `#2dbe60` | Verified, paid, positive |
| `--color-warning` | `#f59e0b` | Warning |
| `--color-danger` | `#b91c1c` | Errors, failed |
| `--color-danger-bg` | `#ffeeee` | Error toast background |

Implementation: dark tokens in `:root` (primary — implemented); light tokens under `[data-theme="light"]` for the migration period.

### Typography

| Token | Value | Usage |
|---|---|---|
| `--font-family` | `system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif` | Base family (current app) |
| `--font-family-display` | `"Inter", system-ui, sans-serif` | Display font — **not loaded** (decided D9: system stack; revisit only if the marketing layer lands) |
| `--text-xs` | `0.75rem` | Labels, badges |
| `--text-sm` | `0.875rem` | Meta, hints, descriptions |
| `--text-base` | `1rem` | Body |
| `--text-lg` | `1.25rem` | Card titles, section titles |
| `--text-xl` | `1.5rem` | Page titles |
| `--text-2xl` | `2rem` | Hero title (marketing only) |
| `--weight-normal` | `400` | Body |
| `--weight-semibold` | `600` | Card titles, buttons |
| `--weight-bold` | `700` | Brand, headings, prices |
| `--line-height` | `1.5` | Base line height |

### Spacing, radii, shadows

| Token | Value | Usage |
|---|---|---|
| `--space-1` | `4px` | Micro gaps |
| `--space-2` | `8px` | Small gaps, padding |
| `--space-3` | `12px` | Compact padding |
| `--space-4` | `16px` | Card padding |
| `--space-6` | `24px` | Section gaps |
| `--space-8` | `32px` | Section padding |
| `--space-12` | `48px` | Hero/landing padding |
| `--radius-sm` | `6px` | Buttons, badges |
| `--radius-md` | `10px` | Cards, inputs |
| `--radius-lg` | `14px` | Large panels |
| `--radius-full` | `9999px` | Pills, avatars |
| `--shadow-card` | `0 1px 3px rgba(0,0,0,0.08)` | Card resting |
| `--shadow-card-hover` | `0 4px 12px rgba(0,0,0,0.10)` | Card hover |

### Breakpoints (explicit in `site.css` since the 2026-08 design alignment)

| Token | Range | Layout |
|---|---|---|
| Mobile | `< 640px` | Single column, cards stack; category hero scrolls horizontally |
| Tablet | `640px – 1023px` | 2-column grids; category hero scrolls horizontally |
| Desktop | `≥ 1024px` | 3–4 column grids (category hero: 2×5), `main` max-width `1200px` |

---

## Components

### Buttons

| Class | Look | Use |
|---|---|---|
| `.btn` | Base: `--radius-sm`, padding `10px 18px`, `--weight-semibold`, focus ring | Base class, always combined with a variant |
| `.btn-primary` | `background: var(--color-accent); color: #062a26;` — on hover slightly lighter | Primary CTA: **Hire this agent**, Sign in, Apply |
| `.btn-outline` | `border: 1px solid var(--color-accent); color: var(--color-accent); background: transparent` | Secondary actions: Explore, Learn more |
| `.btn-danger` | `background: var(--color-danger); color: #fff` | Destructive: Remove favorite, Revoke |
| `.btn[disabled]` | `opacity: 0.5; cursor: not-allowed` | Disabled states (e.g., agent not hireable) |

> Note: `.btn` was undefined at design time; it is now defined in `site.css` (design alignment, 2026-08).

### Agent card

Anatomy (from `partials/agent_card_core.html` — shared by plain + HTMX variants — plus team mockups):

```
[image 140px]   Category badge (top-right)
Name            Verified badge (if verified) · x402 badge (if hireable)
Description (1–2 lines, no jargon)
Meta row: price · score · hires count · trust signals
[.btn-primary "Hire"]  (or disabled "Not hireable")
```

Rules:
- Image fallback: keep the existing placeholder (restyled for the dark theme in 2026-08); card never breaks when the image is missing.
- The HTMX variant (`agent_card_htmx.html`) MUST render the exact same card as `agent_card.html` — both include `agent_card_core.html`, the single source of card markup (fixed in the 2026-08 design alignment; the `verified` badge is included in both).
- Category badge: `--color-surface-muted` background, `--text-xs`, `--radius-full`.

### Badges

| Class | Look | Use |
|---|---|---|
| `.badge` | Base: `--text-xs`, `--radius-full`, padding `2px 8px` | Base |
| `.badge.verified` | `color: var(--color-success)` with ✓ icon | On-chain verified agent |
| `.badge.x402` | `background: var(--color-bnb)` | Payable via x402 |
| `.badge.category` | `background: var(--color-surface-muted)` | Agent category |
| `.badge.risk` | `color: var(--color-danger)` with ⚠ icon | Trust warning (see Trust indicators) |

### Score (decided D7: objective metrics + user ratings)

- Display as a compact read-only component: numeric value + context label. Never a bare "good/bad" judgment.
- Two layers, both in scope:
  - **Objective — Agent Execution Profile** (base): execution time, cost, rule adherence, risk limits, transaction success, explainability, permissions, unauthorized actions. Verifiable on-chain.
  - **Social — user ratings**: 1–5 stars, user voting (secondary signal on cards/detail).
- Rule: never present score as investment advice ("this agent is good because it earned X").
- Implemented: `.score` shows `average_score` (0–100) + context label on cards/detail, with a ratings line (`★ star_count · total_feedbacks ratings`) when feedbacks exist. The full execution-profile metrics are **not collected yet** — they arrive with the on-chain data source; until then `average_score` stands in for the base layer.

### Trust indicators (decided D8: T1 now)

Scope decided: **T1 — creator link + hires count** (from existing data). T2+ is roadmap.

| Signal | Component | Scope |
|---|---|---|
| Verified agent | `.badge.verified` | Today — implemented |
| Creator history | Link "by {owner}" → `/?owner=<owner_address>` (home filter by owner address) | T1 — implemented |
| Hires count | "Hired by N users" line on card/detail, counted from **paid** hires (`status='paid'`, distinct users) | T1 — implemented |
| Wallet risk flags | `.badge.risk` "flagged wallet" (estafa/warnings list) | T2 — CSS defined, **not rendered** until the flagged-address data source lands |
| Payment wallet trust | Same risk treatment as creator | T2 |
| On-chain proof | Link/button "View on-chain record" opening BSC explorer | T2 |
| Recommendations | "Users who hired this also use…" (Amazon-style, cold-start aware) | T3 — Mayari's model |

### Hire panel (agent detail)

- Title: "Hire this agent"
- Price line: `Hire for $1.00` (price from `X402_DEFAULT_PRICE_USD`)
- Status line (4 states): Not started / Pending — awaiting wallet signature / Paid — redirecting / Payment failed
- Not-hireable state: explain why ("This agent cannot be hired — no payment wallet (payTo) is registered."), `.btn` disabled
- States driven by `payment.js`; prefer `is-*` classes over inline `style.display`.

### Stepper (roadmap — not in current scope, D6)

4 steps, numbered circles with connectors: ① Choose agent → ② Connect wallet → ③ Configure → ④ Activate. Always show the current step; this reduces novice anxiety. Build only if the full marketing layer is approved later.

### Header / navbar

- Brand (left): current `bnb_agent` brand + nav: **Browse · Favorites · Sign in**
- Height: ~64px; sticky; background `var(--color-surface)`, bottom border `var(--color-border)`
- Logged-in state replaces "Sign in" with the user's address (truncated) — no separate page.

### Forms & filters

- Inputs: `1px solid var(--color-border-strong)`, `--radius-sm`, focus ring `2px solid var(--color-accent)`
- Filters (home): Category select, Sort, Search, Apply — one row, wraps on mobile.

### Feedback states

| State | Component |
|---|---|
| Error | `.toast.error` (`#errors` region, HTMX OOB swap) — `--color-danger-bg` bg, `--color-danger` text |
| Success | `.toast.success` — `--color-success` text |
| Empty | `.empty-state`: icon + one-line explanation + action button ("No agents found — clear filters", "No favorites yet — browse agents") |
| Loading | `.loading` spinner (neutral gray) for HTMX load-more |

### Hero / category cards (decided D6: lightweight layer on home; taxonomy D11)

In scope: a compact hero + category explanation section **above the existing listing on `/`** (one partial). The full marketing layer (stepper, separate landing) is roadmap.

- Hero: headline in second person ("Automate your investments with AI agents"), subline ("Choose, activate, and let them work for you"), CTA `.btn-primary` "Explore agents" (scrolls to listing / applies no filter).
- Category cards — **10 cards** (taxonomy accepted from `docs/category-study.md`, decision D11): icon + name + tagline (max 5–6 words) + concrete example. Clicking a card filters the listing to that category.
- Layout (design D7): **2 rows × 5 columns** on desktop (`≥1024px`, `repeat(5, 1fr)`), **horizontal scroll** below `1024px` (reusing the 640/1024/1280 breakpoints) — all 10 cards reachable without vertical page growth.
- Icons: Rebalancing ↔/balance, Grid Trading ▦ bars, Yield Optimization ↗, Health Factor 🛡️ shield (never a heart — reads as "favorites"), Dev & Automation `</>` code, Creative & Design ✒ pen, Marketing & Content 📣 megaphone, Data & Analytics 📊 bars, Security & Compliance 🔒 lock, Admin & Ops 📋 clipboard.

---

## Copy & tone

- **Language:** English — everything (decided D3): UI, marketing, documentation. No Spanish UI copy.
- **Terminology (fixed):**

| Term | Use | Never |
|---|---|---|
| Hire | The action verb (flat-fee, x402): "Hire this agent", "Hire for $1.00" | Buy, Rent, Contract (in UI copy) |
| Agent | A contractible AI agent on BNB Chain | Bot, robot (except educational content) |
| Wallet | User's BSC wallet | — |
| Categories | 10 categories + other (taxonomy D11): Rebalancing, Grid Trading, Yield Optimization, Health Factor Monitoring, Dev & Automation, Creative & Design, Marketing & Content, Data & Analytics, Security & Compliance, Admin & Ops, Other | — |

- Second person, short sentences, concrete examples over abstractions.
- Never promise returns. Never frame risk as fear. The Health Factor "protects your collateral", it does not "save you from liquidation horror stories".
- Jargon rule: any crypto term visible to users must have a one-line explanation or tooltip (wallet, gas, liquidity, slippage, LP).
- Disclaimers: "Not investment advice" framing belongs in the agent detail/hire area, not as marketing.

## Accessibility

- Contrast ≥ 4.5:1 for text (verify `--color-text-secondary` on surfaces).
- Focus visible: `2px` outline/focus ring on all interactive elements.
- Keep existing `aria-live` on error region, `role="status"` on hire status, `aria-label` on pagination.
- `prefers-reduced-motion`: disable spinner animations.
- Interactive targets ≥ 40px height.

## Implementation notes

- Tokens live as CSS custom properties in `:root` of `site.css`. No preprocessor, no Tailwind, no framework (project constraint).
- Class naming: flat, semantic (`.agent-card`, `.badge.verified`, `.hire-panel`). No ad-hoc inline styles in templates.
- The orphan classes listed at design time (`.btn`, `.agent-detail`, `.hero`, `.hire-panel`, `.hire-price`, `.hint`, `.score`, `.status-*`) are all defined in `site.css` (design alignment, 2026-08).
- Card markup is unified: `partials/agent_card_core.html` is the single source, included by both `agent_card.html` (full page) and `agent_card_htmx.html` (HTMX swap) so the variants render identically, with one image-fallback definition.
- The "Explore agents" CTA scrolls to the listing via `/#listing`; `html { scroll-behavior: smooth }` (disabled under `prefers-reduced-motion`).
- New pages reuse `base.html` blocks (`title`, `content`) and the `#errors` region for HTMX swaps.

## Checklist

- [ ] All colors/sizes come from tokens, no new hardcoded hex values
- [ ] Buttons use `.btn` + a variant; primary CTA is `.btn-primary`
- [ ] Agent cards match the anatomy; HTMX variant identical to the plain variant
- [ ] Hire panel shows all 4 states + not-hireable state
- [ ] Copy follows the terminology table (Hire, not Buy); no jargon without explanation
- [ ] No promise of returns / no investment-advice framing anywhere
- [ ] Empty, error, and loading states exist on every data-driven page
- [ ] Focus rings and contrast rules applied
- [ ] New components documented back into this file

Status (design alignment, 2026-08): the current implementation passes all items. Re-run the checklist for every new page/partial.

## Decisions

All design decisions are resolved. Add new decisions here as the product evolves.

| # | Decision | Resolution |
|---|---|---|
| D1 | **Product name** | ✅ "BNB Agent Marketplace" (current brand `bnb_agent`) |
| D2 | **Logo** | 🔶 In progress — team has an idea to develop; never the BNB logo; placeholder until created |
| D3 | **UI language** | ✅ English — everything (UI, marketing, docs) |
| D4 | **Theme** | ✅ Dark theme (mockup direction) is the design basis; light is legacy until migration |
| D5 | **Brand color** | ✅ Mockup palette: teal `#14b8a6` primary, cyan `#38bdf8` secondary; BNB yellow kept only for BNB-linked elements |
| D6 | **Home page scope** | ✅ Lightweight hero + category cards above the existing listing; stepper and full marketing layer are roadmap |
| D7 | **Score model** | ✅ Both: Agent Execution Profile (objective metrics, base) + user ratings 1–5 (secondary); never as investment advice |
| D8 | **Trust features** | ✅ T1 now: creator link + hires count (existing data). T2 wallet flags / on-chain proof, T3 recommendations — roadmap |
| D9 | **Display font** | ✅ System stack (no webfont); revisit only if the marketing layer lands |
| D10 | **T1 trust signals implementation** | ✅ Creator link → `/?owner=<owner_address>` home filter; hires count = paid hires, distinct users; card markup unified in `agent_card_core.html` (design alignment, 2026-08) |
| D11 | **Category taxonomy** | ✅ 10 categories + `other` accepted from `docs/category-study.md` (2026-08-26 — **source of truth** for the taxonomy). Signal priority: termix source category → offchain tags → x402 → skill/protocol hints → `other`. Hero renders all 10 cards (2×5 grid `≥1024px`, horizontal scroll below). Sync: `sdd/doc-refresh` |

Roadmap (post-hackathon): full marketing landing + stepper, wallet risk flags (T2), recommendation model (T3, Mayari), logo delivery.