# Design — logo-v2

## Summary

Replace the text brand mark `bnb_agent` in `base.html` with the crystal-diamond
logo (`NuevosCambios/LogoV2.png`). Pure presentation change: one HTML line
replacement, one small CSS rule, one DESIGN.md doc line, one committed PNG
asset. Closes DESIGN.md D2 from "🔶 In progress" to adopted. No model, route,
or behavior change.

---

## Technical approach

### 1. File format decision — PNG only (SVG is follow-up)

- **Decision:** ship PNG only. Defer SVG conversion to a follow-up change.
- **Rationale:** PNG works in the existing static-asset pipeline immediately,
  needs no tooling, and matches the asset the team delivered. SVG conversion
  is a separate polish task (smaller file, HiDPI crisp scaling, inline
  recoloring).
- **Implication:** we commit a binary asset. No build step is added; no
  preprocessing pipeline touches `app/static/`.

### 2. File size optimization — re-export at ~128px tall

- **Source:** `NuevosCambios/LogoV2.png` (full-res, ~1.3 MB per proposal risk).
- **Target:** `<100 KB` PNG, **~128px tall**, width auto-preserved.
- **Why 128px:** navbar renders the logo at 36px tall (header is 64px, see CSS
  §Header). 128px gives ~3.5× pixel density cushion for HiDPI displays
  (Retina/2×, 3×) without bloating the payload. Anything larger is wasted
  bandwidth for a navbar asset; anything smaller loses sharpness on HiDPI.
- **Tooling (preferred → fallback):**
  - ImageMagick: `magick NuevosCambios/LogoV2.png -resize x128 -strip -define png:compression-level=9 app/static/img/logo.png`
  - Pillow: `python -c "from PIL import Image; im=Image.open('NuevosCambios/LogoV2.png'); w,h=im.size; im.resize((round(w*128/h),128), Image.LANCZOS).save('app/static/img/logo.png', optimize=True)"`
- **Verification:** `ls -lh app/static/img/logo.png` → must be `<100KB`.
  Open the file in an image viewer; verify it visually matches the source.

### 3. Asset placement — `app/static/img/logo.png`

- Path follows the existing convention (`app/static/img/placeholder.svg` already
  lives there).
- Served by the existing FastAPI `StaticFiles` mount at `/static/...` — no
  route, no middleware change.

### 4. HTML change — `app/templates/base.html`

- **Before:**
  ```html
  <a class="brand" href="/">bnb_agent</a>
  ```
- **After:**
  ```html
  <a class="brand brand-img" href="/"><img src="/static/img/logo.png" alt="BNB Agent Marketplace" /></a>
  ```
- **Why two classes on the `<a>`:** keep `.brand` for existing layout hooks
  (the `header .brand` rule still owns positioning inside the sticky header).
  Add `.brand-img` for image-specific sizing — keeps the text-brand and
  image-brand CSS independent so a future revert to text is a one-line
  template change.
- **Why `alt="BNB Agent Marketplace"`:** matches DESIGN.md D1 product name
  verbatim. Keeps the brand string available to screen readers and serves as
  the de-facto title-bearing text now that the visual mark is non-textual.
- **Scope:** one line, no surrounding markup change, no JS hooks.

### 5. CSS adjustment — `app/static/css/site.css`

- Add a new rule immediately after the existing `header .brand` block
  (Header / navbar section):
  ```css
  /* When the brand slot carries an image instead of text. */
  header .brand-img img {
    height: 36px;
    width: auto;
    display: block;
  }
  ```
- **Why `height: 36px`:** fits within the 64px sticky header with ~14px
  breathing room top/bottom — matches the existing text brand baseline
  visually and satisfies acceptance criterion 3 (≤ 40px).
- **Why `width: auto`:** preserve aspect ratio from the optimized PNG.
- **Why `display: block`:** remove the inline-image baseline gap (the `<a>`
  is `display: inline` by default; the `<img>` inside inherits inline layout
  and leaves ~4px of descender space below, raising the logo off-center).
- **No override of the existing `header .brand` font rules** — they only
  affect text (font-weight, font-size, color: `--color-bnb`); the `<img>`
  inside doesn't read them.

### 6. DESIGN.md update — D2 row only

- Replace:
  > D2 | **Logo** | 🔶 In progress — team has an idea to develop; never the BNB logo; placeholder until created
- With:
  > D2 | **Logo** | ✅ Adopted: crystal diamond with stylized "A" — teal/cyan/BNB yellow on dark navy (LogoV2.png; navbar renders `<img>` at 36px height)
- **No other DESIGN.md changes.** D5 palette tokens remain unchanged; the logo
  confirms existing tokens, does not require new ones.

---

## Files touched

| File | Change | Lines (non-binary) |
|---|---|---|
| `openspec/changes/logo-v2/proposal.md` | new (already exists) | n/a |
| `openspec/changes/logo-v2/spec.md` | new | n/a |
| `openspec/changes/logo-v2/design.md` | new (this file) | n/a |
| `app/static/img/logo.png` | new asset (binary) | — |
| `app/templates/base.html` | modify — 1 line replacement | ~1 |
| `app/static/css/site.css` | modify — add ~5-line `.brand-img` rule | ~5 |
| `DESIGN.md` | modify — D2 row text only | ~1 |

**Total non-binary diff:** ~7 changed lines (excluding `design.md`). Well
under the 400-line budget.

---

## Data flow

n/a — pure presentation change. No DB, no API, no auth, no env, no JS.

---

## Contracts

| Contract | Value |
|---|---|
| Asset URL | `GET /static/img/logo.png` |
| Asset path on disk | `app/static/img/logo.png` |
| Asset dimensions | ~128px tall, width auto-preserved |
| Asset size | `<100KB` |
| HTML element | `<a class="brand brand-img" href="/"><img src="/static/img/logo.png" alt="BNB Agent Marketplace" /></a>` |
| Alt text | `BNB Agent Marketplace` (matches DESIGN.md D1) |
| CSS hook | `header .brand-img img { height: 36px; width: auto; display: block; }` |
| Rendered height in header | 36px (acceptance criterion 3) |
| Background compatibility | Dark only (logo PNG has a dark bg; matches D4 dark primary) |

---

## Work units

| WU | Title | Files | Done when |
|---|---|---|---|
| **WU1** | Re-export LogoV2.png to navbar-sized PNG | `NuevosCambios/LogoV2.png` → `app/static/img/logo.png` | PNG is ≤128px tall, `<100KB`, visually matches source |
| **WU2** | Verify asset placement | `app/static/img/logo.png` | File exists at path; `ls -lh` confirms size budget |
| **WU3** | Update `base.html` brand mark | `app/templates/base.html` | One line replaced; alt text reads `BNB Agent Marketplace` |
| **WU4** | Add `.brand-img` CSS rule | `app/static/css/site.css` | Rule added next to `header .brand`; logo renders at 36px in header |
| **WU5** | Update DESIGN.md D2 row | `DESIGN.md` | D2 row reads `✅ Adopted: crystal diamond...` |
| **WU6** | Run test suite (regression guard) | — (no file change) | `uv run pytest` → 285 baseline preserved |
| **WU7** | Sanity-check `git diff` | — (no file change) | `git diff -- app/ DESIGN.md` shows only: new `logo.png`, modified `base.html` + `site.css` + `DESIGN.md` (D2 row only) |

**Sequencing:** WU1 → WU2 → (WU3, WU4, WU5 in any order) → WU6 → WU7.

---

## Tests

- **No new tests** — the change is purely visual. Grep confirms no existing
  test asserts on `class="brand"`, the `bnb_agent` text in templates, the
  `BNB Agent Marketplace` string, or the `/img/logo` path.
- **No test changes needed** — the 285-test baseline remains valid. WU6 runs
  the suite as a regression guard only.
- **Manual visual check** (not automated): open `/`, `/flagged`, and one
  agent detail page (e.g. `/agents/56/1`) in a browser; confirm the logo
  renders at 36px in the header and is visually centered with the nav items
  (acceptance criteria 3, 4, 8).

---

## Rollout

- **Risk:** low. Binary asset + 1-line HTML + ~5-line CSS + 1-line docs. No
  runtime behavior change.
- **Backward compatibility:** the text brand mark is removed; no URL, route,
  or Jinja block is affected. Every page extending `base.html` automatically
  picks up the change.
- **Light-theme consideration:** logo PNG has a dark background (matches
  dark navy `--color-bg`). D4 confirms dark is primary; light is legacy
  migration-only. Logo is not visible under `[data-theme="light"]` in current
  scope. If the light theme reappears in the future, treat the logo as
  dark-only and pick a separate asset for light (out of scope here).
- **Follow-ups (out of scope, documented in proposal):** SVG conversion,
  favicon, Apple touch icon, Open Graph image, light-theme logo variant.