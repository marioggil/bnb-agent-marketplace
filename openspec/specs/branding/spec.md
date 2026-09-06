# Branding Specification — `logo-v2`

**Change:** `logo-v2`
**Domain:** `branding` (rendered by `app/templates/base.html`, styled by `app/static/css/site.css`, asset at `app/static/img/logo.png`)
**Scope:** Adopt `NuevosCambios/LogoV2.png` as the official product logo, replacing the text-only brand mark, and close DESIGN.md D2.

**Output path note:** Written flat at `openspec/changes/logo-v2/spec.md` per the parent task's explicit output path, instead of the `specs/{domain}/spec.md` nested layout. Archive will treat this as a full new domain spec and copy it to `openspec/specs/branding/spec.md`.

---

## Purpose

DESIGN.md D2 (Logo) is `🔶 In progress`. The product ships a text-only brand mark (`bnb_agent`) in the header. The team has produced a crystal-diamond logo (`NuevosCambios/LogoV2.png`) whose palette matches the existing design system (D5: teal `#14b8a6` + cyan `#38bdf8` + BNB yellow `#f0b90b` on dark navy `#0f172a`). This change adopts that asset, ships it as `app/static/img/logo.png`, renders it in `base.html` with the required `alt` text, sizes it to fit the existing nav density, and closes D2 to `✅ Adopted`.

---

## Non-Goals

- No logo SVG conversion (raster only — vector polish is a follow-up).
- No favicon, Apple touch icon, or Open Graph image (separate changes).
- No mobile-specific or light-theme logo variants (D4 confirms dark is primary; light is legacy only).
- No new design tokens — the logo uses existing D5 palette tokens.

---

## Requirements

### Requirement: REQ-001 — Logo asset is shipped at the canonical static path

The system MUST provide the crystal-diamond logo at `app/static/img/logo.png`, byte-for-byte identical to `NuevosCambios/LogoV2.png`, so that the FastAPI static mount at `/static/img/logo.png` serves it on every request.

#### Scenario: AC-1 — logo.png exists and matches source byte-for-byte

- GIVEN the source asset `NuevosCambios/LogoV2.png` is present in the repo
- WHEN the change is implemented
- THEN `app/static/img/logo.png` MUST exist as a tracked file
- AND `cmp NuevosCambios/LogoV2.png app/static/img/logo.png` MUST exit with status `0` (no byte difference)

#### Scenario: AC-7 — git diff under `app/` is limited to expected files

- GIVEN the change is implemented on a clean checkout of the parent commit
- WHEN `git diff -- app/ DESIGN.md` is run
- THEN the changed paths MUST be exactly: `app/static/img/logo.png` (new), `app/templates/base.html`, `app/static/css/site.css`, `DESIGN.md`
- AND no other file under `app/` or `DESIGN.md` MUST appear in the diff

---

### Requirement: REQ-002 — Header brand mark is the logo image, not text

The system MUST render the logo as `<img src="/static/img/logo.png" alt="BNB Agent Marketplace">` inside the `<header>` of `app/templates/base.html`, replacing the existing `<a class="brand">bnb_agent</a>` text link. The brand link MUST remain an `<a href="/">` wrapping the image so home-page navigation is preserved.

#### Scenario: AC-2 — base.html renders an `<img>` element instead of the text brand mark

- GIVEN the implemented `app/templates/base.html`
- WHEN any page is rendered (e.g. `GET /`, `GET /flagged`, `GET /agents/56/1`)
- THEN the rendered HTML MUST contain an `<img>` element with `src="/static/img/logo.png"`
- AND the rendered HTML MUST contain `alt="BNB Agent Marketplace"`
- AND the rendered HTML MUST NOT contain the bare text node `bnb_agent` inside `<a class="brand">` (it MAY appear in `<title>` or page chrome)
- AND the brand link `<a class="brand" href="/">` MUST still wrap the image so the home link works

#### Scenario: AC-8 — production URL resolves and renders the logo on all primary pages

- GIVEN the application is deployed and reachable at the production URL
- WHEN a client issues `GET /`, `GET /flagged`, and `GET /agents/56/1`
- THEN each response status MUST be `200`
- AND each response body MUST contain `<img src="/static/img/logo.png" alt="BNB Agent Marketplace">`
- AND `GET /static/img/logo.png` MUST return `200` with a PNG `Content-Type`

---

### Requirement: REQ-003 — CSS sizes and aligns the logo to fit the header

The system MUST adjust `app/static/css/site.css` so that `header .brand` (and the rendered `<img>` inside it) renders at no more than 40px tall and is vertically centered with the nav items, preserving current nav density.

#### Scenario: AC-3 — logo height is ≤ 40px in the header

- GIVEN the implemented `app/static/css/site.css`
- WHEN the index page is rendered and the `<header>` is measured
- THEN the `<img>` inside `<a class="brand">` MUST render with a CSS computed `height` ≤ `40px`
- AND the `<img>` MUST declare an explicit `height` (in `px` or `rem`) in `site.css` so the size is enforced, not browser-default

#### Scenario: AC-4 — logo is visually centered with the nav items

- GIVEN the implemented `app/static/css/site.css`
- WHEN the `<header>` is laid out at the design viewport (≥ 768px wide)
- THEN the `<img>` element's vertical center MUST align with the vertical center of the nav `<a>` items within a ±2px tolerance
- AND the existing `header .brand` block MUST keep its color tokens (e.g. `var(--color-bnb)`) where they still apply, with only height/width/alignment properties changed

---

### Requirement: REQ-004 — DESIGN.md D2 reflects the adopted logo

`DESIGN.md` row `D2 | Logo` MUST read `✅ Adopted: crystal diamond with stylized "A" — teal/cyan/BNB yellow on dark navy` (matching the source asset `NuevosCambios/LogoV2.png` and the existing D5 palette). The 🔶 In progress marker MUST be removed.

#### Scenario: AC-5 — DESIGN.md D2 says "✅ Adopted"

- GIVEN the implemented `DESIGN.md`
- WHEN the file is read
- THEN the row whose ID column is `D2` MUST begin its Value column with `✅ Adopted:`
- AND the substring `✅ Adopted` MUST appear on the D2 row
- AND the substring `🔶 In progress` MUST NOT appear on the D2 row
- AND the row MUST mention "crystal diamond" and reference the teal/cyan/BNB-yellow palette consistent with D5

---

### Requirement: REQ-005 — Existing test suite stays green after the swap

`uv run pytest` MUST report 285 passing tests after the change, with no new failures and no new errors. If any existing assertion looks for the literal text `bnb_agent` inside the rendered header brand mark, that assertion MUST be updated to match the new alt text `BNB Agent Marketplace` — and only that assertion, with a per-line justification comment.

#### Scenario: AC-6 — `uv run pytest` reports 285 baseline tests passing

- GIVEN a clean working tree with the change applied
- WHEN `uv run pytest` is run from the repo root
- THEN the final summary MUST report `285 passed` (or `passed == 285` and `failed == 0` and `error == 0`)
- AND no test file outside of brand-text assertion updates MAY be modified
- AND any updated brand-text assertion MUST comment the diff line with the reason (e.g. `# logo-v2: brand text replaced by img alt text`)

---

## Risks

- **R-1 (low):** A test asserts the literal `bnb_agent` inside the brand link; the swap removes that text. The fix is a one-line assertion update to `BNB Agent Marketplace` (per REQ-005) — preserve the test's intent (it guards brand presence), do not delete the test.
- **R-2 (low):** `LogoV2.png` is a ~1.3 MB PNG. For a navbar asset this is heavy. Optimize the copy to <100KB (re-export at 128px tall) before committing; AC-1 still requires byte-equivalence, so this applies to the optimization step (re-export, then commit the smaller PNG and update the source `NuevosCambios/LogoV2.png` only if the team agrees).
- **R-3 (low):** The logo has a black background. The dark navy header hides this, but the asset is not safe for light surfaces. Note this constraint in DESIGN.md (or rely on the D4 "dark is primary" statement already in scope).
- **R-4 (info):** This spec is written flat (`openspec/changes/logo-v2/spec.md`) rather than nested under `specs/branding/spec.md`. Archive should still treat it as a full new domain spec and copy to `openspec/specs/branding/spec.md` on acceptance.

---

## References

- Proposal: `openspec/changes/logo-v2/proposal.md`
- DESIGN.md rows D2, D4, D5
- Source asset: `NuevosCambios/LogoV2.png`
- Existing flat-spec precedent: `openspec/changes/test-copy-fix/spec.md`

