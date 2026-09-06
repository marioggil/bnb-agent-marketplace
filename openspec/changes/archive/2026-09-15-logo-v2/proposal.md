# Proposal: logo-v2 — adopt crystal diamond logo

## Change name
`logo-v2`

## Domain
Branding / design system

## Status
Draft

---

## Problem

DESIGN.md D2 (Logo) is in "🔶 In progress" state. The current branding uses the text label `bnb_agent` everywhere — no visual logo. The team has produced a crystal-diamond logo that aligns with the existing design system palette (D5: teal `#14b8a6` + cyan `#38bdf8` + BNB yellow `#f0b90b` on dark navy `#0f172a`). The logo is at `NuevosCambios/LogoV2.png`.

A first proposal (`NuevosCambios/Propuesta logo.jpeg`) used purple/magenta that conflicted with D5. This is LogoV2 — the team revised it to match the design system.

## Goal

Adopt LogoV2 as the official product logo, replace the text-only brand mark in `base.html`, and close DESIGN.md D2 from "in progress" to "✅ adopted".

## Scope

**In**:
- Copy `NuevosCambios/LogoV2.png` → `app/static/img/logo.png` (committed asset)
- Update `app/templates/base.html` — replace `<a class="brand">bnb_agent</a>` with `<img src="/static/img/logo.png" alt="BNB Agent Marketplace">`
- Update `app/static/css/site.css` — adjust `.brand` height/width to fit the logo (~36px tall in header)
- Update `DESIGN.md` D2 row: `✅ Adopted: crystal diamond with stylized "A" — teal/cyan/BNB yellow on dark navy`
- Optional: update `DESIGN.md` D5 if the logo confirms any palette tokens (it should not require new tokens)

**Out**:
- Logo SVG (raster only for now — vector conversion is a future polish task)
- Favicon (separate change; favicon currently is the placeholder)
- Mobile-specific logo variants
- Light-theme logo variant (D4 confirms dark is primary; light is legacy only)

## Acceptance criteria

1. `app/static/img/logo.png` exists and matches `NuevosCambios/LogoV2.png` byte-for-byte
2. `app/templates/base.html` renders the `<img>` instead of the text brand mark on all pages
3. Logo height ≤ 40px in header (preserves nav density)
4. Logo is visually centered with nav items (not floating high/low)
5. `DESIGN.md` D2 reads "✅ Adopted: crystal diamond..."
6. `uv run pytest` → 285 baseline preserved (no test changes needed; the text-only assertion `bnb_agent` in templates should still pass since the alt text contains the brand name; otherwise one-line template update)
7. `git diff -- app/` shows only: new `logo.png` + `base.html` + `site.css` + `DESIGN.md`
8. Production URL still resolves and `/`, `/flagged`, `/agents/56/1` all render with the logo

## Risks

- **Low**: brand text might appear in tests as `"bnb_agent"` — if any assertion fails, fix is one-line update to match the alt text or skip the brand assertion.
- **Low**: logo file size — PNG is ~1.3 MB which is too heavy for a navbar. Should optimize to <100KB before commit.
- **Low**: PNG transparency — the logo background is black, which works on the dark navy page bg, but if used on white/light surfaces it would show as a black box. Header is dark navy so this is fine; document the constraint in DESIGN.md.

## Open questions

1. **File format**: PNG only or also generate SVG? SVG is smaller and scales for HiDPI. Decision: PNG only for this change; SVG conversion as a follow-up if needed.
2. **File size optimization**: should we re-export the logo at smaller dimensions before committing? Yes — for navbar use 128px tall PNG is plenty. Use the existing LogoV2.png as source and re-export.

## Out of scope (future)

- Favicon (separate)
- Apple touch icon (separate)
- Open Graph image for social sharing (separate)
- Logo SVG conversion
- Animated logo
- Logo on dark vs light theme differentiation

## References

- DESIGN.md D2, D4, D5
- LogoV2.png source at `NuevosCambios/LogoV2.png`
- Team chat (informal) confirming the revised palette match
