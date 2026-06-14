---
name: uiux
description: >
  Design system + UI/UX guide for the Trauma Stress Lab website (Flask/Jinja2 +
  vanilla CSS/JS, no React). Use this WHENEVER building, restyling, or animating
  any public page, hero, section, card, component, or template, or whenever
  editing static/css/style.css or static/js/*. Trigger even on vague asks like
  "make this nicer", "match the design", "add a section", "fix the styling",
  "animate this", "more premium", or "like the homepage". Covers Figtree
  typography, theme-aware color tokens, vibrant gradient cards, duotone portrait
  cards, scroll-reveal / scroll-unfold motion, the intro splash + cursor-paint
  hero, and the verify-via-Flask-preview workflow (including the gotchas that
  waste the most time).
---

# Trauma Stress Lab — UI/UX & Design System

The site is the public face of a **mental-health research lab** (Chung-Ang University).
Aesthetic target: **calm, premium, motion-forward** — clean light surfaces, large
confident Figtree headlines, generous whitespace, soft pastel + vibrant gradient
accents, and animation that *reveals on scroll* rather than decorating. Reference
point the lab likes: upsunday.co (adopt the **patterns/feel**, never copy its
assets/copy — use our own content).

## Tech context (read first — it constrains everything)

- **Flask + Jinja2** server-rendered templates in `templates/`. There is **no
  React, no Tailwind, no bundler.** Do **not** reach for Framer Motion / 21st.dev
  components — they need React. For library-grade animation use **GSAP +
  ScrollTrigger via CDN** (global `gsap`), or vanilla IntersectionObserver.
- **One stylesheet**: `static/css/style.css` (CSS custom properties = the design
  tokens). It is the **single source of truth for tokens** — read its `:root`
  and `[data-theme="light"]` blocks before styling; use the tokens, don't
  hardcode hexes that duplicate them.
- **Vanilla JS** in `static/js/` loaded with `<script defer>` from `base.html`
  (global) or a page's `{% block scripts %}`. Existing motion modules:
  `reveal.js` (scroll-reveal), `scroll-expand.js` (scroll-unfold),
  `paint.js` (cursor paint), `intro.js` (splash). Reuse them before writing new.
- **Theme**: `data-theme` on `<html>` (`dark` default, `light` toggle), set by an
  inline script in `base.html` from `localStorage`. Every color must come from a
  token so both themes work.

## Typography

- `--font-display` and `--font-body` are both **Figtree** (Pretendard fallback for
  Korean, then system). Set on `body` and `h1–h4`; just use the tokens.
- Big headings: **bold (700–800) with tight negative tracking** (`letter-spacing:
  -0.03em` on h1/h2, up to `-0.04em` on the hero). This is what makes type feel
  premium — flabby tracking reads cheap.
- Eyebrows/labels: small, `font-weight: 600`, `letter-spacing: .04em`,
  `text-transform: uppercase`, muted color.
- Headings already inherit `--font-display`; don't re-declare `font-family`
  inline unless you mean it.

## Color & theme

Tokens live in `style.css` (`:root` = dark, `[data-theme="light"]` = light).
Key light-mode values (Apple-neutral): `--bg #fff`, `--surface #f5f5f7`,
`--text/--fg-1 #1d1d1f`, `--muted/--fg-3 #6e6e73`, `--border rgba(0,0,0,.10)`.
The brand accent is violet (`--violet #7c6fe0`) but the current direction leans
**neutral + gradient accents**, so prefer neutrals for text/UI and save color for
gradients.

Signature gradient recipes (already in the codebase — match them):
- **Hero (light)**: soft pastel `linear-gradient(158deg,#dce7fb,#ecedf1,#f4efe8,#f8e8de)` (blue→cream→peach).
- **Vibrant cards** (`.ri-grid .ri-card`): full-bleed radial gradients, white text —
  blue / pink / orange / teal cycled by `nth-child`. See the "Vibrant gradient
  cards" block in `style.css`.

> ⚠️ **The `var(--dark)` trap.** `--dark` is the *heading color*, which is
> **near-white in dark theme** (`rgba(255,255,255,.96)`). Several older rules did
> `background: var(--dark)` expecting a dark fill → **white-on-white** in dark mode
> (this bug hit `.portal-header` and a home CTA). If you need a fixed dark surface,
> use an explicit hex (e.g. `#15132c`) or a theme-aware token — never `var(--dark)`
> as a background.

## Spacing, layout, radius

- 8pt rhythm; sections breathe: `section { padding: 104px 0 }` (smaller on mobile).
- Containers: `.container` (max ~1140px) / `.container-sm`; showcase uses ~1300px.
- Radius scale: cards ~20–28px (vibrant cards 28px), pills `--r-pill` (9999px),
  buttons are pills. Keep radii consistent within a component family.
- Shadows are **neutral and soft** (`var(--shadow-sm/md/lg)`), not neon glows —
  we deliberately de-neoned. Avoid `0 0 Npx <accent>` ring glows.

## Component recipes

- **Vibrant gradient card** — full gradient background, white text, transparent
  `.ri-card-top` for the icon, frosted CTA. Implemented for `.ri-grid .ri-card`.
- **Duotone portrait card** (`.member-card`, `.professor-card`, alumni) — large
  square photo, `filter: grayscale(1) contrast(1.03)` → **color on hover**,
  left-aligned name/role. Premium, editorial.
- **Frosted iOS app-icon tile** — emoji in a 100px+ squircle with translucent
  white bg + `backdrop-filter: blur` + soft shadow (`span.ri-card-icon`). Emoji
  in a bare box renders ~16px; the tile + large `font-size` is what fixes it.
- **Scroll-unfold showcase** — `.scroll-expand` scales `0.85→1` and squares its
  corners as it crosses viewport center, driven by `--p` from `scroll-expand.js`.
- **Scroll-reveal** — add `.reveal` (or let `reveal.js` auto-tag section blocks);
  it fades/slides up via IntersectionObserver, staggered by sibling index.
- **Intro splash** (`intro.js` + `.intro-overlay`) — CAU mark on a gradient, once
  per session.
- **Cursor paint** (`paint.js` + `#paint-canvas` in the hero) — pastel watercolour
  blobs flow from the pointer.

## Motion principles

- Reveal on scroll; stagger siblings; keep easing subtle/premium
  (`var(--ease-out)`); durations ~.6–.9s.
- **Always** guard with `@media (prefers-reduced-motion: reduce)` and an early
  return in JS — accessibility and it stops animation from wedging things.
- Continuous canvas animations (paint, particle fields) are fine in real browsers
  but can hang the *preview screenshot tool* — see verification.
- New scroll/parallax/carousel work: prefer **GSAP + ScrollTrigger** (CDN globals)
  for smoothness over hand-rolled scroll math.

## Workflow & verification

1. Edit `style.css` (tokens/components) and/or templates and/or `static/js`.
2. **Cascade order matters.** A later rule wins at equal specificity, and the
   per-template inline `<style>` in `templates/portal_*.html` loads *after*
   `style.css`, so to override portal styles globally use a **higher-specificity**
   selector (e.g. `.lin-wrap .x`, `.portal-wrap .x`). When you append an override
   for a public component, make sure it's *after* the original or more specific —
   appending near the top of the file does not beat a rule 400 lines below.
3. **Verify in the Flask preview** (`preview_start`; the launch config is "TSL
   Flask"). **Template (.html) changes require a server restart** (the app runs
   `debug=False`, so Jinja caches templates) — `preview_stop` then `preview_start`.
   **CSS/JS are static** and just need a browser reload.
4. **Verify via computed styles, not screenshots.** The screenshot tool here is
   flaky (it wedges on animated canvases). `preview_eval` + `preview_inspect` give
   exact computed values (colors, fonts, sizes, layout) — more reliable than an
   image. Confirm: token resolved to the right value, element present, no console
   errors (`preview_console_logs level:error`). The user can eyeball the live
   panel for the subjective call.
5. Commit + push after each coherent change (the repo auto-deploys; see the
   project memory about auto-push).

## Gotchas that waste the most time

- `var(--dark)` as a background (see trap above).
- Forgetting to restart the server after a template edit → "my change didn't show".
- Fixed-dark feature bands (`.apps-stat-row`, banners) need **explicit white text**
  (`#fff`), not `var(--fg-1)` — `--fg-1` flips dark in light mode → invisible.
- `neurobreeze/` and other large binaries must stay **gitignored** (a 135 MB zip
  once blocked a push).
- Keep contrast: pastel/accent text on white needs a deep enough shade (use
  `--violet-deep`, not `--violet-soft`, for small text on light bg).
