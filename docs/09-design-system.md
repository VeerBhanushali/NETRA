<!-- status: DRAFT (recovered from interrupted run; not yet adversarially hardened) -->

## Design System — Minimalist White, Professional

Every hex, ratio and pixel below is fixed. Nothing here is a suggestion. All contrast ratios were computed with the WCAG 2.1 relative-luminance formula and are stated as `X.XX:1`; severity colours were additionally run through a Viénot deuteranope simulation and the results are reported honestly, including where colour alone is *not* sufficient.

Assumed target: desktop operations console, minimum viewport **1280 × 800**, Chrome/Edge. Light mode is the default and the **only** mode used on stage.

---

### 1. Five rules to check a screen against

Run these five checks on any screen before it is merged. A screen that fails any one of them is not done.

1. **White is the default; ink is the decoration.** The page background is `#FFFFFF`. Every surface stays white or one step off it (`#FAFBFC`, `#F4F6F8`). Colour never fills a large area — a coloured region larger than a 20 px badge, a 3 px rule, or a 2 px focus ring is a bug.
2. **Hierarchy comes from type size, weight and whitespace — never from colour.** If you removed all colour from the screenshot, the reading order must be unchanged. If it collapses, the layout is wrong, not the palette.
3. **Colour is a redundant channel, never the only one.** Severity, status and confidence are always carried by a text label *and* a glyph *and* position, with colour added on top. Assume the duty officer is deuteranopic and the projector is miscalibrated.
4. **Every line is 1 px; nothing floats.** Structure is expressed by hairline borders (`1px solid #E9ECEF` / `#D3D9E0`) and by the 4 px spacing grid. Shadows exist only for things that genuinely overlap the page (popover, drawer, modal, toast) — exactly two shadow tokens, no others.
5. **Numbers and plates are monospaced, tabular, right-aligned where scanned.** Anything an operator compares vertically down a column — plate strings, camera IDs, track IDs, timestamps, speeds, confidences — uses the mono stack with `tabular-nums`. Proportional digits in a data table are a defect.

---

### 2. Colour

#### 2.1 Choosing the accent

One accent: **`#0A4BA0`**, a desaturated institutional blue (H ≈ 214°, S ≈ 88%, L ≈ 33%).

Why this and not a brighter blue: at 33% lightness it reaches **8.31:1 on white**, so the *same* token works as link text, as icon colour, as a 2 px focus ring, and as a solid button fill with white text (**8.31:1** the other way). A brighter blue (`#0B57D0`, 6.39:1) forces a second darker blue for text; two blues in the same UI is how palettes rot. `#0A4BA0` also reads as the blue of Indian government/police signage and of the IND strip on the number plate, which is thematically correct without being a costume. It is far enough from `#12626E` (severity *low*) and from every neutral to stay unambiguous.

The accent is used for exactly four things: **focus rings, the primary button, selected/active navigation state, and interactive text (links, sort-active column)**. It never fills a card, never appears as a background wash larger than a table-row selection tint, and never gets a gradient.

#### 2.2 Surface + neutral ink ramp

The neutral ramp carries a slight blue tint (hue ≈ 213°) so it sits with the accent instead of fighting it.

| Token | Hex | Contrast vs `#FFF` | Use |
|---|---|---|---|
| `--surface-0` | `#FFFFFF` | — | Page canvas, table rows, cards, inputs |
| `--surface-1` | `#FAFBFC` | 1.04:1 | Table zebra (optional), sidebar, toolbars |
| `--surface-2` | `#F4F6F8` | 1.08:1 | Table header, disabled input fill, code blocks |
| `--surface-sunken` | `#EEF1F4` | 1.14:1 | Map background gutters, skeleton base |
| `--ink-100` | `#E9ECEF` | 1.19:1 | Hairline divider inside a panel |
| `--ink-200` | `#D3D9E0` | 1.42:1 | **Default border** (input, card, table) |
| `--ink-300` | `#B4BDC7` | 1.90:1 | Hover border, disabled control border |
| `--ink-400` | `#8B95A2` | 3.04:1 | Placeholder text, disabled text, decorative icon |
| `--ink-500` | `#69737F` | **4.82:1** | Secondary/meta text (min size 12 px) |
| `--ink-600` | `#5A6673` | **5.86:1** | Uppercase micro-labels, table column headers |
| `--ink-700` | `#3C4552` | **9.70:1** | Body text in dense tables |
| `--ink-800` | `#232B36` | **14.28:1** | Sub-headings |
| `--ink-900` | `#0E141B` | **18.51:1** | Primary text, headings, plate strings |

`--ink-400` (3.04:1) is **only** legal for placeholder text and non-essential icons — never for content. `--ink-500` is the floor for any text that carries meaning.

#### 2.3 Severity + status

Six semantic ramps. Each has three tokens: `-ink` (text/icon/3 px rule), `-tint` (badge fill), `-wash` (whole-row background).

| Semantic | `-ink` | on white | on `-tint` | `-tint` | `-wash` | Glyph (Lucide) |
|---|---|---|---|---|---|---|
| `critical` | `#8C1D18` | **9.11:1** | 7.88:1 | `#FBEBE9` | `#FEF5F4` | `octagon-alert` |
| `high` | `#AE4408` | **5.78:1** | 5.16:1 | `#FCF0E6` | `#FEF8F2` | `triangle-alert` |
| `medium` | `#8A6700` | **5.22:1** | 4.71:1 | `#FAF3DF` | `#FDFAF0` | `circle-alert` |
| `low` | `#12626E` | **7.00:1** | 6.14:1 | `#E7F2F4` | `#F4F9FA` | `info` |
| `info` | `#0A4BA0` | **8.31:1** | 7.32:1 | `#EBF1FB` | `#F5F8FD` | `circle-dot` |
| `success` | `#0F6B33` | **6.62:1** | 5.82:1 | `#E8F3EC` | `#F4F9F6` | `circle-check` |

Plus one solid: `--sev-critical-solid: #B3261E` (white text = **6.54:1**). **Critical is the only severity rendered as a solid-filled badge.** That single structural difference — fill vs tint — is the loudest, most colour-blind-proof signal in the whole UI, and it is why the critical *ink* can afford to be a dark maroon rather than a shouty red.

**Colour-vision honesty.** Under a Viénot deuteranope simulation the warm ramp does not fully separate by hue: `#8C1D18 → #72781A`, `#AE4408 → #939929`, `#8A6700 → #7E8139` — three olives. Their simulated luminances are 0.171 / 0.291 / 0.204, so critical vs high *does* separate by brightness, but high vs medium is weak. The cool ramp (`low`, `info`) separates cleanly from all warm severities under deuteranopia, protanopia and tritanopia. **Therefore severity is encoded four ways, and colour is the fourth:** (1) uppercase text label `CRITICAL`/`HIGH`/…, (2) distinct Lucide glyph per row above, (3) fill-vs-tint for critical, (4) colour. Never ship a severity dot with no label. Never sort a list only by colour.

`success` (green) and `critical` (red) never appear on the same axis: green is used **only** for pipeline/system health (camera online, worker healthy, job complete), never as a severity level. That removes the classic red/green collapse from the alert list entirely.

#### 2.4 Interaction + state tokens

| Token | Hex | Note |
|---|---|---|
| `--accent` | `#0A4BA0` | 8.31:1 |
| `--accent-hover` | `#083C80` | white text 10.67:1 |
| `--accent-active` | `#062F66` | white text 13.07:1 |
| `--accent-tint` | `#EBF1FB` | selected nav item, sort-active header |
| `--accent-ring` | `#0A4BA0` | 2 px `focus-visible` ring, 1 px offset |
| `--row-selected` | `#E8EFF9` | ink-900 on it = 15.99:1 |
| `--row-hover` | `#F4F6F8` | |
| `--danger` | `#B3261E` | destructive button fill, white text 6.54:1 |
| `--danger-hover` | `#8C1D18` | white text 9.11:1 |

#### 2.5 Dark mode (stretch — do not build before the demo works)

Ship light only for the hackathon. If time remains, add `.dark` on `<html>` and override the semantic layer *only*; the primitive ramp stays untouched. Verified values against `--surface-0: #0F1317`:

| Token | Hex | Contrast vs `#0F1317` |
|---|---|---|
| `--surface-0` / `-1` / `-2` | `#0F1317` / `#161B22` / `#1C222B` | — |
| `--ink-900` / `-700` / `-500` | `#E9EDF2` / `#A9B3BF` / `#8A94A0` | 15.86 / 8.78 / 6.06 |
| `--ink-200` (border) / `--ink-300` | `#2A323C` / `#3A434F` | 1.44 / 1.86 |
| `--accent` | `#6BA5F5` | 7.40 (ink `#0F1317` on it: 7.40) |
| `critical / high / medium / low / success` ink | `#F2837B` / `#F0A868` / `#DCC06A` / `#63BFB4` / `#6FC98C` | 7.38 / 9.33 / 10.47 / 8.56 / 9.25 |

Dark tints become `color-mix(in srgb, <ink> 16%, var(--surface-1))`. Never invert the *light* hexes algorithmically — dark red at 9:1 on white is unreadable at 1.2:1 on charcoal.

---

### 3. Typography

**Sans:** Inter (variable). **Mono:** JetBrains Mono. Both loaded through `next/font/google` with `display: "swap"` and self-hosted by Next — no runtime request to Google.

```
--font-sans: var(--font-inter), ui-sans-serif, system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
--font-mono: var(--font-jetbrains-mono), ui-monospace, "Cascadia Mono", "Segoe UI Mono", Consolas, "Liberation Mono", monospace;
```

Inter is chosen over Geist purely for the feature set we need — `tnum`, `zero` (slashed zero), and the `cv` character variants that disambiguate `I`/`l`. Geist is an acceptable drop-in if the team already has it; do not mix both.

> **Verify:** Inter's slashed-zero feature tag is `zero` and the straight-`l` alternate is `cv05` in Inter v4; confirm against the shipped `next/font` version before relying on `cv05`. `tnum` and `zero` are safe.

#### 3.1 Why plates MUST be monospace with tabular numerals

An operator's core task is comparing two plate strings that differ in one glyph. In a proportional face, `MH12DE1433` and `MH12DE1483` do not align vertically down a table column, so the differing character does not pop out — the eye has to read both strings. In a mono face with fixed advance width, the mismatching column is visible without reading. Second, the exact confusion set that ANPR OCR gets wrong — `0/O`, `1/I/l`, `8/B`, `5/S`, `2/Z`, `6/G` — is precisely the set that a mono face with a slashed zero and a serifed `1` disambiguates by design. A human verifying a low-confidence read must be able to tell whether the model output `O` or `0`; in Inter they are nearly identical, in JetBrains Mono they are not. Third, `tabular-nums` keeps timestamps, speeds and confidence percentages in fixed columns so a scanning eye can compare magnitudes positionally.

```css
.plate {
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 0.875rem;      /* 14px */
  line-height: 1.25rem;     /* 20px */
  letter-spacing: 0.06em;   /* opens up the run so glyph shapes read individually */
  text-transform: uppercase;
  font-variant-numeric: tabular-nums slashed-zero;
  font-feature-settings: "zero" 1, "ss02" 1;
  color: var(--ink-900);
  white-space: nowrap;
}
.plate--lg { font-size: 1.25rem; line-height: 1.75rem; }  /* alert detail header */
.plate--sm { font-size: 0.75rem; line-height: 1rem; }     /* map pin label */
```

The rendered string is always the **normalised** plate (`^[A-Z0-9]{6,11}$`, no spaces, no hyphens) so it matches the database value exactly and copy-paste is lossless. Visual grouping is done with `letter-spacing` only — never by inserting spaces into the string.

Per-character confidence from the OCR is rendered as a background tint on the individual character, not as a separate score column:

```tsx
// apps/web/src/components/ui/plate-text.tsx
export function PlateText({ value, charConfidences, size = "md" }: {
  value: string; charConfidences?: number[]; size?: "sm" | "md" | "lg";
}) {
  const cls = size === "md" ? "plate" : `plate plate--${size}`;
  if (!charConfidences || charConfidences.length !== value.length) {
    return <span className={cls} title={value}>{value}</span>;
  }
  return (
    <span className={cls} title={value} aria-label={value.split("").join(" ")}>
      {value.split("").map((ch, i) => (
        <span
          key={i}
          aria-hidden
          data-low={charConfidences[i] < 0.85 ? "" : undefined}
          className="data-[low]:bg-[var(--sev-medium-tint)] data-[low]:text-[var(--sev-medium-ink)]"
        >
          {ch}
        </span>
      ))}
    </span>
  );
}
```

Threshold `0.85`: below this, PaddleOCR character-level scores on Indian plates are empirically unreliable enough that a human should look. Tune once against your own validation set; keep it in one constant (`PLATE_CHAR_CONF_WARN = 0.85`) shared with the backend.

#### 3.2 Type scale

Base = 16 px. Scale is a flattened 1.15-ish ramp — a command centre needs many close sizes, not a dramatic 1.618.

| Token | px / rem | Weight | Line-height | Tracking | Use |
|---|---|---|---|---|---|
| `text-display` | 28 / 1.75rem | 600 | 34px (1.21) | −0.02em | KPI hero number, incident count |
| `text-h1` | 22 / 1.375rem | 600 | 28px (1.27) | −0.015em | Page title |
| `text-h2` | 18 / 1.125rem | 600 | 24px (1.33) | −0.01em | Panel title |
| `text-h3` | 15 / 0.9375rem | 600 | 20px (1.33) | −0.006em | Card / section heading |
| `text-body` | 14 / 0.875rem | 400 | 20px (1.43) | 0 | Default UI text, table cells |
| `text-body-md` | 14 / 0.875rem | 500 | 20px | 0 | Emphasised cell, button label |
| `text-sm` | 13 / 0.8125rem | 400 | 18px (1.38) | 0 | Compact table rows, dense lists |
| `text-caption` | 12 / 0.75rem | 400 | 16px (1.33) | 0 | Timestamps, helper text, meta |
| `text-micro` | 11 / 0.6875rem | 600 | 12px (1.09) | **0.08em** | **Uppercase micro-label** |
| `text-kpi` | 32 / 2rem | 600 | 36px | −0.02em | Stat tile value (tabular) |

**Micro-label style** — the signature element of this system. Column headers, field labels, panel eyebrows, KPI captions:

```css
.label-micro {
  font-size: 0.6875rem;      /* 11px */
  line-height: 0.75rem;      /* 12px */
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--ink-600);     /* 5.86:1 */
  font-variant-numeric: tabular-nums;
}
```

11 px is legible only because it is 600-weight, tracked out to 0.08em, and at 5.86:1. Do not use 11 px at weight 400, and do not go below 11 px anywhere in the product.

Global numeric rule: `body { font-variant-numeric: tabular-nums; }`. Prose paragraphs (there are almost none) opt out with `.prose-nums { font-variant-numeric: normal; }`.

---

### 4. Spacing, grid, page shell

**Spacing scale (4 px base).** Only these values exist:

`0 · 2 · 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64 · 80` px → tokens `space-0, 0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20`.

Rules: gap between related controls = 8; between a label and its control = 4 (micro-label) or 6; between fields in a form = 16; between sections in a panel = 24; page gutter = 24 (≥1440px) / 16 (<1440px); panel internal padding = 16 (dense) or 20 (default).

**Layout grid:** 12 columns, 24 px gutter, fluid within `max-width: 1440px`. Map pages are full-bleed and ignore the max width. Below 1280 px the app shows a "This console requires a 1280 px viewport" notice rather than degrading; mobile is out of scope for MVP.

**Page shell (fixed pixel values):**

| Element | Value |
|---|---|
| Top header (app bar) | `56px` height, `1px` bottom border `--ink-200`, `--surface-0` |
| Sidebar (expanded) | `240px` |
| Sidebar (rail / collapsed) | `56px`, icon-only |
| Sidebar surface | `--surface-1`, `1px` right border `--ink-200` |
| Filter / toolbar strip | `48px` height, sticky under header, `--surface-0`, `1px` bottom border |
| Content max width | `1440px`, centred; map + video pages full-bleed |
| Page gutter | `24px` |
| Right inspector drawer | `420px` (docked ≥1600px, overlay below) |
| Map overlay panel | `360px` wide, `16px` inset from map edge, `--surface-0`, `1px` border, `--shadow-overlay` |
| Modal widths | `sm 480px` · `md 640px` · `lg 800px`; max-height `calc(100vh - 96px)` |
| Toast | `380px` wide, bottom-right, `24px` inset, stack gap `8px` |
| Table header row | `36px` |
| Table row — comfortable | `44px`, cell padding `12px 16px`, `text-body` |
| Table row — compact | `32px`, cell padding `6px 12px`, `text-sm` |

Density is a single attribute on the table root: `data-density="comfortable" | "compact"`, persisted per user in `localStorage` key `anpr.density`. Compact is the default on the Alerts and Plate Reads tables (an operator needs ~25 rows visible); comfortable is default everywhere else.

**Z-index scale (no other values):** `base 0` · `sticky-header 10` · `table-header 20` · `map-overlay 30` · `dropdown 35` · `drawer 40` · `modal 50` · `toast 60` · `tooltip 70`.

---

### 5. Border, radius, elevation

**Radius:** `--radius-xs: 2px` (default: inputs, buttons, badges, cards, table container), `--radius-sm: 4px` (modal, drawer, popover, map overlay panel), `--radius-full: 9999px` (**only** the status dot, the avatar, and the toggle knob). Nothing else is round.

The argument for near-zero radius: a 2 px corner reads as engineered rather than friendly, it keeps adjacent cells and stacked panels visually aligned on the same grid line (an 8 px radius eats the corner of a dense table and breaks the column rhythm), and it is the shared visual grammar of government and industrial-control software. Large radii signal "consumer app" to a judging panel and cost you credibility in the first three seconds.

**Borders:** `1px solid` at all times. `--ink-100` for dividers inside a panel, `--ink-200` for the boundary of any container or control, `--ink-300` for hover/emphasis. Never 2 px borders except the 3 px severity left-rule and the 2 px focus ring. Use `border` for structure, not `box-shadow` spread hacks — a border participates in layout and stays crisp at any DPI.

**Elevation:** exactly two tokens, and they are only for things that overlap other content.

```css
--shadow-overlay: 0 1px 2px rgba(14,20,27,0.06), 0 8px 24px -4px rgba(14,20,27,0.10);
--shadow-modal:   0 2px 4px rgba(14,20,27,0.08), 0 24px 48px -12px rgba(14,20,27,0.18);
```

`--shadow-overlay` → dropdown, popover, toast, map overlay panel, drawer. `--shadow-modal` → modal only. Cards, panels, tables, KPI tiles, sidebars: **no shadow, ever** — a 1 px border does the job. Both shadows use the ink-900 hue, never pure black, so they read as neutral grey rather than muddy.

**Focus:** `:focus-visible` only, never `:focus`, and never `outline: none` without a replacement.

```css
:where(a, button, input, select, textarea, [tabindex]):focus-visible {
  outline: 2px solid var(--accent-ring);
  outline-offset: 1px;
  border-radius: var(--radius-xs);
}
```

---

### 6. Component specifications

Interactive height scale: `sm 28px` · `md 32px` (default) · `lg 40px` (primary page actions and the global search only).

#### Button

| Variant | Default | Hover | Active | Focus-visible | Disabled |
|---|---|---|---|---|---|
| **Primary** | bg `--accent`, text `#FFF`, no border | bg `--accent-hover` | bg `--accent-active` | + 2 px ring, 1 px offset | bg `--ink-200`, text `--ink-400`, `cursor: not-allowed` |
| **Secondary** | bg `#FFF`, text `--ink-900`, border `--ink-200` | bg `--surface-2`, border `--ink-300` | bg `--surface-sunken` | + ring | bg `--surface-2`, text `--ink-400`, border `--ink-200` |
| **Ghost** | transparent, text `--ink-700` | bg `--surface-2` | bg `--surface-sunken` | + ring | text `--ink-400` |
| **Danger** | bg `--danger`, text `#FFF` | bg `--danger-hover` | `#7A1914` | ring uses `--danger` | as primary |

Geometry: height 32 px, padding `0 12px` (`0 10px` if leading icon), `text-body-md` (14/500), radius 2 px, icon 16 px at `stroke-width 1.5`, gap 6 px. No transform on hover, no shadow, no scale. Loading state: label stays, leading icon swaps to a 16 px spinner, `aria-busy="true"`, width does not change.

#### Input / search

Height 32 px (`lg 40px` for the global command search), padding `0 10px`, border `1px --ink-200`, radius 2 px, bg `#FFF`, `text-body`, placeholder `--ink-400`.
Hover → border `--ink-300`. Focus-visible → border `--accent` **and** the 2 px ring. Disabled → bg `--surface-2`, text `--ink-400`. Invalid → border `--sev-critical-ink`, helper text `--sev-critical-ink` at `text-caption`, `aria-invalid="true"`.
Search variant: leading `search` icon 16 px in `--ink-400`, trailing `x` clear button appears only when non-empty, `⌘K`/`Ctrl K` kbd hint chip (`text-micro`, bg `--surface-2`, border `--ink-200`, radius 2 px) at the right. Plate-search inputs use `font-family: var(--font-mono); text-transform: uppercase;` and normalise on change (strip non-`[A-Za-z0-9]`).

#### Select

Native `<select>` styled to match input geometry, with `appearance: none` and a 16 px `chevron-down` in `--ink-500` at 10 px inset. For multi-select filters (camera, severity, vehicle class) use a popover checkbox list — 280 px wide, max-height 320 px, `--shadow-overlay`, 32 px rows, search field at top when >8 options, "Clear · Apply" footer bar.

#### Data table

The core component of this product; get it exactly right.

- Container: `1px --ink-200`, radius 2 px, `overflow: hidden`, horizontal scroll inside.
- Header: 36 px, bg `--surface-2`, `position: sticky; top: 0; z-index: 20`, `1px` bottom border `--ink-200`, `.label-micro` in `--ink-600`, `text-align` matches the column (numeric = right).
- Rows: 1 px bottom border `--ink-100`. **No zebra striping by default** — zebra plus severity washes plus hover produces three competing backgrounds. Zebra (`--surface-1` on odd rows) is allowed only on tables with no row-level semantic colour, and it is off in the demo.
- Row states: hover `--row-hover` (whole row, `cursor: pointer` if navigable); selected `--row-selected` + left 3 px `--accent`; keyboard-focused row gets the 2 px ring inset.
- Severity rows: 3 px left rule in `--sev-{level}-ink`. The `-wash` background is applied **only** to `critical` and `high` rows so the eye still has white space; medium/low carry the rule and badge alone.
- Sorting: entire header cell is a button. Inactive → 12 px `chevrons-up-down` at `--ink-400`, visible on hover only. Active → `--accent` text, 12 px `arrow-up`/`arrow-down` always visible, `aria-sort="ascending|descending"`.
- Numeric, timestamp, plate, ID and confidence columns: `font-variant-numeric: tabular-nums`; numeric right-aligned; plate/ID left-aligned in mono.
- Sticky first column (plate) on tables wider than the viewport: `position: sticky; left: 0;` with a `1px --ink-200` right border and `box-shadow: 1px 0 0 var(--ink-100)` — the one permitted extra shadow, because it is a border substitute.
- Row actions: right-most 48 px column, ghost icon button revealed on row hover and always present for keyboard users (`opacity-0 group-hover:opacity-100 focus-within:opacity-100`).
- Realtime insert: new row animates `background-color` from `--accent-tint` to transparent over 400 ms and nothing else. No slide, no height animation — layout must not jump while an operator is clicking.

#### Badge / pill

Height 20 px, padding `0 6px`, radius 2 px (pills are square-ish here — `radius-full` is reserved for status dots), `.label-micro`, `1px` border, optional leading 12 px glyph.
Severity badge: bg `--sev-{level}-tint`, text and border `--sev-{level}-ink` at 20% via `color-mix(in srgb, var(--sev-x-ink) 22%, transparent)`. **Critical is the exception:** bg `--sev-critical-solid`, text `#FFF`, no border.
Status badge (camera/worker/job): 6 px `radius-full` dot + label in `--ink-700`; dot colour `--sev-success-ink` (online), `--ink-400` (offline), `--sev-medium-ink` (degraded), `--sev-critical-ink` (error). Never dot-only.

#### Card / panel

`--surface-0`, `1px --ink-200`, radius 2 px, no shadow. Optional header: 44 px, `text-h3`, `1px` bottom border `--ink-100`, actions right-aligned as ghost buttons. Body padding 16 or 20. Panels never nest more than two deep; an inner grouping uses a hairline divider, not another bordered box.

#### Tabs

Underline style only. 40 px tall strip with a `1px --ink-200` bottom border spanning the full width. Tab: `text-body-md`, `--ink-500`; hover `--ink-800`; active `--ink-900` with a 2 px `--accent` bottom bar overlapping the container border; focus-visible ring inset. Padding `0 12px`, gap 4. No pill tabs, no boxed tabs, no icons-only tabs.

#### Side drawer

420 px, full height minus the 56 px header, right-anchored, `--surface-0`, `1px` left border `--ink-200`, `--shadow-overlay`. Header 56 px: `text-h2` title + `--ink-500` subtitle, close `x` ghost button. Slides in `180ms var(--ease-standard)` (`transform: translateX(100%) → 0`); the page behind does **not** dim when the drawer is docked, and dims with `rgba(14,20,27,0.32)` when it overlays. Focus trapped; `Esc` closes.

#### Modal

Centred, `--shadow-modal`, radius 4 px, backdrop `rgba(14,20,27,0.40)` with **no blur**. Header 56 px (`text-h2`), body padding 20, footer 64 px with a `1px` top border `--ink-100` and right-aligned buttons (`Cancel` secondary, then primary/danger, gap 8). Enter: opacity `0→1` and `translateY(4px)→0` over 180 ms. Modals are for destructive confirmation and short forms only — evidence review goes in the drawer.

#### Toast

380 × auto, `--surface-0`, `1px --ink-200`, radius 2 px, `--shadow-overlay`, left 3 px severity rule, 12 px padding, 16 px glyph + `text-body` message + optional ghost "View" action + `x`. Auto-dismiss 6 s (info/success), **never auto-dismiss critical**. Max 3 stacked; older ones collapse to a "+N more" row. Enter 180 ms `translateY(8px)+opacity`, exit 120 ms opacity only.

#### Empty state

Centred in the container, max-width 360 px, 48 px vertical padding: 24 px Lucide glyph in `--ink-300`, `text-h3` in `--ink-800`, one `text-caption` line in `--ink-500` explaining what to do, and at most one secondary button. **No illustration, no mascot, no emoji.** Distinguish three cases explicitly: *no data yet* ("No plate reads in the last 15 minutes"), *filtered to nothing* ("No results for these filters" + Clear filters), *error* (severity-critical rule + Retry).

#### Skeleton loader

`--surface-2` blocks at the exact height of the real content (table row 32/44 px, KPI tile 96 px), radius 2 px, animating `opacity: 1 → 0.55 → 1` over 1400 ms `ease-in-out`. **No shimmer sweep gradient** — that is a gradient, and gradients are banned. Show skeletons only for the first load; subsequent refreshes keep stale data visible and set `aria-busy` on the container.

#### Map overlay panel

360 px wide, 16 px inset, `--surface-0` at `opacity: 1` (never translucent — translucency over satellite tiles destroys text contrast and is glassmorphism by another name), `1px --ink-200`, radius 4 px, `--shadow-overlay`. Sections separated by hairlines. Map controls (zoom, layers, reset) are 32 × 32 secondary buttons in a vertical stack with 1 px separators, top-right, 16 px inset. Map markers: 10 px `radius-full` dot, 2 px white stroke, fill `--sev-{level}-ink`; selected marker gets a 20 px ring in `--accent` at 30% opacity. Marker labels are `.plate--sm` on a white 1 px-bordered chip, never floating text on tiles.

#### KPI stat tile

Height 96 px, `1px --ink-200`, radius 2 px, padding 16, `--surface-0`. Structure top→bottom: `.label-micro` in `--ink-600`; `text-kpi` value in `--ink-900` with `tabular-nums`; a `text-caption` delta line where the arrow glyph carries direction and the colour is `--sev-success-ink` / `--sev-critical-ink` — the arrow, not the colour, is the signal. Tiles sit in a 4-up grid with 16 px gaps and 1 px borders; do not put a shadow, a background tint, or a sparkline fill gradient on them. A sparkline, if present, is a 1.5 px `--ink-300` stroke with no fill, 32 px tall.

---

### 7. Iconography

**One library: `lucide-react`.** No Heroicons, no Material Icons, no Font Awesome, no inline hand-drawn SVGs.

- **Stroke width `1.5` everywhere.** Set once: `<LucideIcon size={16} strokeWidth={1.5} />`, enforced via a wrapper `apps/web/src/components/ui/icon.tsx`.
- **Three sizes only: 12 / 16 / 20 px.** 12 = inside badges and sort affordances. 16 = default (buttons, table actions, inputs, nav). 20 = page headers and empty states (24 allowed for the empty-state glyph only).
- `color: currentColor`, never a hard-coded hex; never filled.
- Decorative icons get `aria-hidden="true"`; an icon-only button gets `aria-label` and a tooltip.
- Icons are aligned to the 4 px grid; a 16 px icon inside a 32 px control sits on 8 px padding.

**No emoji. Ever.** Not in the UI, not in toasts, not in table cells, not in seed data, not in commit-visible strings. Emoji render differently on Windows, macOS and the projector's browser; they are colour-only signals that fail for CVD users and screen readers; they break `tabular-nums` column alignment; and they instantly reclassify the product as a hobby project in a judge's mind. Severity is a Lucide glyph plus a word.

---

### 8. Motion

Motion exists to answer one question: *"what just happened, and where did it go?"* If an animation does not express causality — this panel came from that button, this row is new — delete it.

```css
--dur-instant: 80ms;   /* colour/border state change on hover, active */
--dur-fast:    120ms;  /* toast exit, tooltip, dropdown */
--dur-base:    180ms;  /* drawer, modal, tab underline, popover */
--dur-slow:    240ms;  /* full-page transition, sidebar collapse — maximum allowed */
--ease-standard: cubic-bezier(0.2, 0, 0, 1);   /* entrances, movement */
--ease-exit:     cubic-bezier(0.4, 0, 1, 1);   /* exits */
--ease-linear:   linear;                        /* progress, spinner only */
```

Rules: only `opacity`, `transform` and `background-color` are animated — never `width`, `height`, `top` or `left` (layout thrash on a 16 GB machine already running an ONNX inference worker). Nothing loops except the spinner and the skeleton pulse. The live-alert row flash (400 ms `background-color` decay from `--accent-tint`) is the single attention-getting animation in the product and it exists because a new row appearing silently in a scrolling table is genuinely missable. No parallax, no scroll-triggered reveal, no number count-up on KPI tiles (a counting number is unreadable while it counts, and this is a control room).

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 1ms !important; animation-iteration-count: 1 !important;
    transition-duration: 1ms !important; scroll-behavior: auto !important;
  }
}
```

---

### 9. Code — paste-ready

**Assumption: Tailwind CSS v4.x** (`@import "tailwindcss"` + `@theme` in CSS, `@tailwindcss/postcss`), which is what a fresh `create-next-app` on Node 24 gives you today. A v3.4 `tailwind.config.ts` is given after it for teams that pin v3.

> **Verify:** the v4 dark-mode variant helper is `@custom-variant dark (&:where(.dark, .dark *));` — confirm against the installed v4 minor before relying on it. Everything else below is stable v4 API.

#### `apps/web/src/app/globals.css`

```css
@import "tailwindcss";

@custom-variant dark (&:where(.dark, .dark *));

/* ── 1. PRIMITIVES ──────────────────────────────────────────────────────── */
@theme {
  /* neutrals */
  --color-white:        #FFFFFF;
  --color-ink-25:       #FAFBFC;
  --color-ink-50:       #F4F6F8;
  --color-ink-75:       #EEF1F4;
  --color-ink-100:      #E9ECEF;
  --color-ink-200:      #D3D9E0;
  --color-ink-300:      #B4BDC7;
  --color-ink-400:      #8B95A2;
  --color-ink-500:      #69737F;
  --color-ink-600:      #5A6673;
  --color-ink-700:      #3C4552;
  --color-ink-800:      #232B36;
  --color-ink-900:      #0E141B;

  /* accent */
  --color-accent:        #0A4BA0;
  --color-accent-hover:  #083C80;
  --color-accent-active: #062F66;
  --color-accent-tint:   #EBF1FB;

  /* severity + status */
  --color-critical:       #8C1D18;
  --color-critical-solid: #B3261E;
  --color-critical-tint:  #FBEBE9;
  --color-critical-wash:  #FEF5F4;
  --color-high:           #AE4408;
  --color-high-tint:      #FCF0E6;
  --color-high-wash:      #FEF8F2;
  --color-medium:         #8A6700;
  --color-medium-tint:    #FAF3DF;
  --color-medium-wash:    #FDFAF0;
  --color-low:            #12626E;
  --color-low-tint:       #E7F2F4;
  --color-low-wash:       #F4F9FA;
  --color-info:           #0A4BA0;
  --color-info-tint:      #EBF1FB;
  --color-info-wash:      #F5F8FD;
  --color-success:        #0F6B33;
  --color-success-tint:   #E8F3EC;
  --color-success-wash:   #F4F9F6;

  /* type */
  --font-sans: var(--font-inter), ui-sans-serif, system-ui, "Segoe UI", Roboto, Arial, sans-serif;
  --font-mono: var(--font-jetbrains-mono), ui-monospace, "Cascadia Mono", Consolas, monospace;

  --text-micro:    0.6875rem;  --text-micro--line-height:    0.75rem;
  --text-caption:  0.75rem;    --text-caption--line-height:  1rem;
  --text-sm:       0.8125rem;  --text-sm--line-height:       1.125rem;
  --text-body:     0.875rem;   --text-body--line-height:     1.25rem;
  --text-h3:       0.9375rem;  --text-h3--line-height:       1.25rem;
  --text-h2:       1.125rem;   --text-h2--line-height:       1.5rem;
  --text-h1:       1.375rem;   --text-h1--line-height:       1.75rem;
  --text-kpi:      2rem;       --text-kpi--line-height:      2.25rem;
  --text-display:  1.75rem;    --text-display--line-height:  2.125rem;

  --tracking-micro: 0.08em;
  --tracking-plate: 0.06em;
  --tracking-tight: -0.015em;

  /* geometry */
  --spacing: 0.25rem;              /* 4px base -> p-1 = 4px, p-6 = 24px */
  --radius-xs: 2px;
  --radius-sm: 4px;

  --shadow-overlay: 0 1px 2px rgb(14 20 27 / 0.06), 0 8px 24px -4px rgb(14 20 27 / 0.10);
  --shadow-modal:   0 2px 4px rgb(14 20 27 / 0.08), 0 24px 48px -12px rgb(14 20 27 / 0.18);

  /* motion */
  --ease-standard: cubic-bezier(0.2, 0, 0, 1);
  --ease-exit:     cubic-bezier(0.4, 0, 1, 1);

  /* shell dimensions, usable as w-/h- arbitrary refs */
  --spacing-header:   56px;
  --spacing-sidebar:  240px;
  --spacing-rail:     56px;
  --spacing-drawer:   420px;
  --spacing-mappanel: 360px;
  --spacing-toolbar:  48px;

  --breakpoint-console: 1280px;
  --container-shell:    1440px;
}

/* ── 2. SEMANTIC ALIASES (themeable) ────────────────────────────────────── */
:root {
  --surface-0: #FFFFFF;
  --surface-1: #FAFBFC;
  --surface-2: #F4F6F8;
  --surface-sunken: #EEF1F4;
  --border-subtle: #E9ECEF;
  --border-default: #D3D9E0;
  --border-strong: #B4BDC7;
  --text-primary:   #0E141B;
  --text-secondary: #3C4552;
  --text-muted:     #69737F;
  --text-label:     #5A6673;
  --text-disabled:  #8B95A2;
  --row-hover:      #F4F6F8;
  --row-selected:   #E8EFF9;
  --accent-ring:    #0A4BA0;
  --danger:         #B3261E;
  --danger-hover:   #8C1D18;

  --dur-instant: 80ms;
  --dur-fast:   120ms;
  --dur-base:   180ms;
  --dur-slow:   240ms;

  --z-sticky: 10; --z-table-header: 20; --z-map-overlay: 30; --z-dropdown: 35;
  --z-drawer: 40; --z-modal: 50; --z-toast: 60; --z-tooltip: 70;
}

/* STRETCH — dark mode. Light is the demo default. */
.dark {
  --surface-0: #0F1317; --surface-1: #161B22; --surface-2: #1C222B; --surface-sunken: #0B0E12;
  --border-subtle: #1F2630; --border-default: #2A323C; --border-strong: #3A434F;
  --text-primary: #E9EDF2; --text-secondary: #A9B3BF; --text-muted: #8A94A0;
  --text-label: #A9B3BF; --text-disabled: #6C7684;
  --row-hover: #161B22; --row-selected: #17243A;
  --accent-ring: #6BA5F5; --danger: #F2837B; --danger-hover: #F5A09A;
}
.dark {
  --color-accent: #6BA5F5; --color-accent-hover: #8FBCF8; --color-accent-active: #B4D2FB;
  --color-accent-tint: #12233B;
  --color-critical: #F2837B; --color-high: #F0A868; --color-medium: #DCC06A;
  --color-low: #63BFB4; --color-info: #6BA5F5; --color-success: #6FC98C;
}

@theme inline {
  --color-surface:        var(--surface-0);
  --color-surface-1:      var(--surface-1);
  --color-surface-2:      var(--surface-2);
  --color-border-subtle:  var(--border-subtle);
  --color-border-default: var(--border-default);
  --color-border-strong:  var(--border-strong);
  --color-fg:             var(--text-primary);
  --color-fg-secondary:   var(--text-secondary);
  --color-fg-muted:       var(--text-muted);
  --color-fg-label:       var(--text-label);
  --color-row-hover:      var(--row-hover);
  --color-row-selected:   var(--row-selected);
  --color-danger:         var(--danger);
}

/* ── 3. BASE LAYER ──────────────────────────────────────────────────────── */
@layer base {
  *, *::before, *::after { border-color: var(--border-default); }

  html { -webkit-text-size-adjust: 100%; }

  body {
    background: var(--surface-0);
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: 0.875rem;
    line-height: 1.25rem;
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1, "zero" 1;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }

  h1 { font-size: 1.375rem; line-height: 1.75rem; font-weight: 600; letter-spacing: -0.015em; }
  h2 { font-size: 1.125rem; line-height: 1.5rem;  font-weight: 600; letter-spacing: -0.01em; }
  h3 { font-size: 0.9375rem; line-height: 1.25rem; font-weight: 600; letter-spacing: -0.006em; }

  :where(a, button, input, select, textarea, [tabindex]):focus-visible {
    outline: 2px solid var(--accent-ring);
    outline-offset: 1px;
    border-radius: var(--radius-xs);
  }
  :focus:not(:focus-visible) { outline: none; }

  ::selection { background: var(--row-selected); color: var(--text-primary); }

  ::-webkit-scrollbar { width: 10px; height: 10px; }
  ::-webkit-scrollbar-track { background: var(--surface-1); }
  ::-webkit-scrollbar-thumb { background: var(--border-strong); border: 2px solid var(--surface-1); }
}

/* ── 4. PRIMITIVE UTILITIES ─────────────────────────────────────────────── */
@layer components {
  .label-micro {
    font-size: 0.6875rem; line-height: 0.75rem; font-weight: 600;
    letter-spacing: 0.08em; text-transform: uppercase;
    color: var(--text-label); font-variant-numeric: tabular-nums;
  }
  .plate {
    font-family: var(--font-mono); font-weight: 600;
    font-size: 0.875rem; line-height: 1.25rem;
    letter-spacing: 0.06em; text-transform: uppercase; white-space: nowrap;
    font-variant-numeric: tabular-nums slashed-zero;
    font-feature-settings: "zero" 1; color: var(--text-primary);
  }
  .plate--lg { font-size: 1.25rem; line-height: 1.75rem; }
  .plate--sm { font-size: 0.75rem; line-height: 1rem; }
  .mono-id {
    font-family: var(--font-mono); font-size: 0.75rem; line-height: 1rem;
    color: var(--text-muted); font-variant-numeric: tabular-nums slashed-zero;
  }
  .hairline   { border-bottom: 1px solid var(--border-subtle); }
  .panel      { background: var(--surface-0); border: 1px solid var(--border-default); border-radius: var(--radius-xs); }
  .sev-rule   { border-left: 3px solid currentColor; }

  /* density switch: <table data-density="compact"> */
  [data-density="comfortable"] :is(td, th) { height: 44px; padding: 0 1rem; font-size: 0.875rem; }
  [data-density="compact"]     :is(td, th) { height: 32px; padding: 0 0.75rem; font-size: 0.8125rem; }
  [data-density] thead th { height: 36px; }
}

/* ── 5. MOTION ──────────────────────────────────────────────────────────── */
@keyframes row-flash { from { background-color: var(--color-accent-tint); } to { background-color: transparent; } }
@keyframes skeleton-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.55; } }

.animate-row-flash { animation: row-flash 400ms var(--ease-exit) 1; }
.animate-skeleton  { animation: skeleton-pulse 1400ms ease-in-out infinite; background: var(--surface-2); }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 1ms !important; animation-iteration-count: 1 !important;
    transition-duration: 1ms !important; scroll-behavior: auto !important;
  }
}
```

#### `apps/web/src/app/layout.tsx` (font wiring)

```tsx
import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
  axes: [], // variable weight range is included by default
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains-mono",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "City ANPR Command Console",
  description: "Centralised ANPR and crime-tracking operations console",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrains.variable}`}>
      <body className="bg-surface text-fg antialiased">{children}</body>
    </html>
  );
}
```

#### `apps/web/tailwind.config.ts` — v3.4 fallback only

Use this **instead of** the `@theme` blocks if the project is pinned to Tailwind v3.4 (keep sections 2–5 of `globals.css`, drop `@import "tailwindcss"`/`@theme` and use the three `@tailwind` directives).

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        surface: { DEFAULT: "#FFFFFF", 1: "#FAFBFC", 2: "#F4F6F8", sunken: "#EEF1F4" },
        ink: {
          25: "#FAFBFC", 50: "#F4F6F8", 75: "#EEF1F4", 100: "#E9ECEF", 200: "#D3D9E0",
          300: "#B4BDC7", 400: "#8B95A2", 500: "#69737F", 600: "#5A6673",
          700: "#3C4552", 800: "#232B36", 900: "#0E141B",
        },
        accent: { DEFAULT: "#0A4BA0", hover: "#083C80", active: "#062F66", tint: "#EBF1FB" },
        critical: { DEFAULT: "#8C1D18", solid: "#B3261E", tint: "#FBEBE9", wash: "#FEF5F4" },
        high:     { DEFAULT: "#AE4408", tint: "#FCF0E6", wash: "#FEF8F2" },
        medium:   { DEFAULT: "#8A6700", tint: "#FAF3DF", wash: "#FDFAF0" },
        low:      { DEFAULT: "#12626E", tint: "#E7F2F4", wash: "#F4F9FA" },
        info:     { DEFAULT: "#0A4BA0", tint: "#EBF1FB", wash: "#F5F8FD" },
        success:  { DEFAULT: "#0F6B33", tint: "#E8F3EC", wash: "#F4F9F6" },
        row: { hover: "#F4F6F8", selected: "#E8EFF9" },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "Segoe UI", "Arial", "sans-serif"],
        mono: ["var(--font-jetbrains-mono)", "ui-monospace", "Consolas", "monospace"],
      },
      fontSize: {
        micro:   ["0.6875rem", { lineHeight: "0.75rem",  letterSpacing: "0.08em", fontWeight: "600" }],
        caption: ["0.75rem",   { lineHeight: "1rem" }],
        sm:      ["0.8125rem", { lineHeight: "1.125rem" }],
        body:    ["0.875rem",  { lineHeight: "1.25rem" }],
        h3:      ["0.9375rem", { lineHeight: "1.25rem",  letterSpacing: "-0.006em" }],
        h2:      ["1.125rem",  { lineHeight: "1.5rem",   letterSpacing: "-0.01em" }],
        h1:      ["1.375rem",  { lineHeight: "1.75rem",  letterSpacing: "-0.015em" }],
        kpi:     ["2rem",      { lineHeight: "2.25rem",  letterSpacing: "-0.02em" }],
        display: ["1.75rem",   { lineHeight: "2.125rem", letterSpacing: "-0.02em" }],
      },
      letterSpacing: { micro: "0.08em", plate: "0.06em" },
      borderRadius: { xs: "2px", sm: "4px" },
      boxShadow: {
        overlay: "0 1px 2px rgb(14 20 27 / 0.06), 0 8px 24px -4px rgb(14 20 27 / 0.10)",
        modal:   "0 2px 4px rgb(14 20 27 / 0.08), 0 24px 48px -12px rgb(14 20 27 / 0.18)",
      },
      spacing: { header: "56px", sidebar: "240px", rail: "56px", drawer: "420px", mappanel: "360px", toolbar: "48px" },
      maxWidth: { shell: "1440px" },
      screens: { console: "1280px" },
      transitionTimingFunction: {
        standard: "cubic-bezier(0.2, 0, 0, 1)",
        exit: "cubic-bezier(0.4, 0, 1, 1)",
      },
      transitionDuration: { instant: "80ms", fast: "120ms", base: "180ms", slow: "240ms" },
      zIndex: { sticky: "10", "table-header": "20", "map-overlay": "30", dropdown: "35", drawer: "40", modal: "50", toast: "60", tooltip: "70" },
    },
  },
  plugins: [],
};
export default config;
```

---

### 10. What would ruin this

Any one of these single-handedly turns a credible government console into a student project. They are review-blocking.

1. **Gradients.** Any `linear-gradient` / `radial-gradient` anywhere — buttons, headers, KPI tiles, skeleton shimmer, chart fills. Charts get flat 1.5 px strokes and, at most, a 12%-opacity flat area fill.
2. **Glassmorphism.** `backdrop-filter: blur()`, translucent panels over the map, frosted modals. It destroys text contrast over satellite tiles and dates the product to 2021.
3. **Purple-to-pink, cyan-to-violet, or any "AI product" palette.** This is police infrastructure, not a SaaS landing page.
4. **Neon on black / the "hacker dashboard".** Lime `#00FF00` on `#0B0B0F`, glowing borders, CRT scanlines, Matrix rain. Dark mode itself is a stretch goal in *neutral charcoal*, not a theme.
5. **Oversized rounded cards.** `rounded-xl`/`rounded-2xl`, 24 px padding on every box, cards floating in a sea of grey. Radius is 2 px; 4 px for overlays; that is the whole list.
6. **Drop shadows everywhere.** Shadows on cards, tiles, table rows, buttons, inputs, sidebars. Two shadow tokens exist and both are for overlapping layers.
7. **Emoji as icons.** 🚨 in a toast, ✅ in a status column, 🚗 on a map pin. Lucide at `strokeWidth 1.5` plus a word.
8. **Three competing accent colours.** One accent (`#0A4BA0`). Semantic colours are for severity and status only, and only on badges, 3 px rules and glyphs — never as a decorative brand colour.
9. **Colour-only severity.** A bare coloured dot with no label, a red row with no `CRITICAL` badge, a green/red delta with no arrow.
10. **Proportional digits in a table.** Plates, IDs, timestamps, speeds and confidences that shift column position between rows.
11. **Count-up number animations and scroll-reveal.** An operator reading a live incident count cannot read a number that is still counting.
12. **A second font.** No display serif for headings, no Poppins, no Space Grotesk. Inter and JetBrains Mono; nothing else gets loaded.

---

## Appendix — Interface Contracts Declared by This Section

- `file: apps/web/src/app/globals.css — sole owner of all design tokens; no other file may declare a colour`
- `file: apps/web/tailwind.config.ts — Tailwind v3.4 fallback only; v4 @theme in globals.css is the assumed default`
- `file: apps/web/src/app/layout.tsx — loads Inter + JetBrains Mono via next/font/google, sets html className={`${inter.variable} ${jetbrains.variable}`}`
- `file: apps/web/src/components/ui/plate-text.tsx — exports PlateText({ value, charConfidences, size })`
- `file: apps/web/src/components/ui/icon.tsx — wrapper enforcing lucide-react strokeWidth=1.5 and size in {12,16,20}`
- `css-var: --font-inter (set by next/font), --font-jetbrains-mono (set by next/font)`
- `css-var: --font-sans, --font-mono`
- `css-var surfaces: --surface-0 #FFFFFF, --surface-1 #FAFBFC, --surface-2 #F4F6F8, --surface-sunken #EEF1F4`
- `css-var borders: --border-subtle #E9ECEF, --border-default #D3D9E0, --border-strong #B4BDC7`
- `css-var text: --text-primary #0E141B, --text-secondary #3C4552, --text-muted #69737F, --text-label #5A6673, --text-disabled #8B95A2`
- `css-var accent: --color-accent #0A4BA0, --color-accent-hover #083C80, --color-accent-active #062F66, --color-accent-tint #EBF1FB, --accent-ring #0A4BA0`
- `css-var rows: --row-hover #F4F6F8, --row-selected #E8EFF9`
- `css-var danger: --danger #B3261E, --danger-hover #8C1D18`
- `css-var severity ink: --color-critical #8C1D18, --color-high #AE4408, --color-medium #8A6700, --color-low #12626E, --color-info #0A4BA0, --color-success #0F6B33`
- `css-var severity solid: --color-critical-solid #B3261E (only severity rendered as a filled badge)`
- `css-var severity tint: --color-{critical|high|medium|low|info|success}-tint`
- `css-var severity wash: --color-{critical|high|medium|low|info|success}-wash (applied as row background ONLY for critical and high)`
- `css-var ink ramp: --color-ink-{25,50,75,100,200,300,400,500,600,700,800,900}`
- `css-var radius: --radius-xs 2px (default), --radius-sm 4px (overlays only), rounded-full reserved for status dot / avatar / toggle knob`
- `css-var shadow: --shadow-overlay, --shadow-modal (the only two elevation tokens)`
- `css-var motion: --dur-instant 80ms, --dur-fast 120ms, --dur-base 180ms, --dur-slow 240ms, --ease-standard cubic-bezier(0.2,0,0,1), --ease-exit cubic-bezier(0.4,0,1,1)`
- `css-var z-index: --z-sticky 10, --z-table-header 20, --z-map-overlay 30, --z-dropdown 35, --z-drawer 40, --z-modal 50, --z-toast 60, --z-tooltip 70`
- `css-var shell: --spacing-header 56px, --spacing-sidebar 240px, --spacing-rail 56px, --spacing-drawer 420px, --spacing-mappanel 360px, --spacing-toolbar 48px; --container-shell 1440px; --breakpoint-console 1280px`
- `css-class: .plate, .plate--sm, .plate--lg — monospace + tabular-nums + slashed-zero, uppercase, letter-spacing 0.06em`
- `css-class: .mono-id — for camera_id / track_id / alert_id rendering`
- `css-class: .label-micro — 11px/600/0.08em uppercase, colour --text-label`
- `css-class: .panel, .hairline, .sev-rule, .animate-row-flash, .animate-skeleton`
- `html-attribute: data-density="comfortable"|"compact" on any table root; drives 44px vs 32px row height, header always 36px`
- `localStorage key: anpr.density — persists the table density preference`
- `enum severity (must match DB and API): 'critical' | 'high' | 'medium' | 'low' | 'info' — 'success' is a SYSTEM-HEALTH status, never an alert severity`
- `enum system status: 'online' | 'degraded' | 'offline' | 'error' — rendered as dot + label, colours success/medium/ink-400/critical`
- `constant: PLATE_CHAR_CONF_WARN = 0.85 — per-character OCR confidence below which a plate character is tinted with --color-medium-tint; must match the backend threshold`
- `data contract: plate strings are stored and rendered normalised as ^[A-Z0-9]{6,11}$ (no spaces/hyphens); char_confidences is a float array whose length equals the plate string length`
- `npm dependency: lucide-react (the only icon library); tailwindcss v4 with @tailwindcss/postcss; next/font/google Inter + JetBrains_Mono`
- `tailwind token names (v3 fallback): colors surface/ink/accent/critical/high/medium/low/info/success/row; fontSize micro|caption|sm|body|h3|h2|h1|kpi|display; borderRadius xs|sm; boxShadow overlay|modal; spacing header|sidebar|rail|drawer|mappanel|toolbar; screens console`
- `icon rules: lucide-react only, strokeWidth 1.5, sizes 12/16/20 (24 permitted for empty-state glyph), color currentColor; severity glyphs octagon-alert/triangle-alert/circle-alert/info/circle-dot/circle-check`
- `accessibility contract: focus-visible = 2px solid var(--accent-ring) with 1px offset; aria-sort on sortable table headers; aria-busy on refreshing containers; no emoji anywhere in UI strings or seed data`

## Appendix — MVP vs Stretch

- MVP: globals.css with the full @theme primitive block, semantic :root aliases, base layer, .plate / .label-micro / .mono-id / .panel utilities, and the prefers-reduced-motion block
- MVP: layout.tsx font wiring for Inter + JetBrains Mono via next/font/google (self-hosted, no runtime Google request)
- MVP: Button (primary/secondary/ghost/danger) with all five states, Input, Search field, Badge (severity + status), Panel/Card, DataTable (sticky 36px header, 32/44px rows, sort affordance, row hover/selected, 3px severity left rule), KPI stat tile, Side drawer, Toast, Empty state, Skeleton
- MVP: PlateText component with per-character low-confidence tinting at 0.85
- MVP: page shell — 56px header, 240px sidebar, 48px toolbar strip, 1440px max content, z-index scale applied
- MVP: data-density toggle on the Alerts and Plate Reads tables, default compact
- MVP: icon wrapper enforcing lucide-react at strokeWidth 1.5
- MVP: light mode only
- STRETCH: dark-mode token overrides under .dark plus a header toggle — do not build until the demo path is green
- STRETCH: sidebar rail (56px collapsed) mode
- STRETCH: multi-select filter popover with search and Clear/Apply footer (MVP can ship a native multi-select)
- STRETCH: sticky first (plate) column on horizontally scrolling tables
- STRETCH: sparkline in the KPI tile (1.5px --color-ink-300 stroke, no fill)
- STRETCH: mobile/tablet read-only alert detail view; MVP shows a 'requires 1280px viewport' notice below the console breakpoint

## Appendix — Risks

- Under a deuteranope simulation the warm severity ramp partially collapses (critical #8C1D18 -> olive L0.171, high #AE4408 -> L0.291, medium #8A6700 -> L0.204); high vs medium is weakly separable by colour alone. Mitigation: severity is always encoded four ways — uppercase text label, distinct Lucide glyph, solid-fill-only-for-critical badge treatment, and colour last. Code review must reject any colour-only severity indicator.
- Tailwind v4 @theme vs v3 config drift: a teammate running `npm i tailwindcss@3` will silently break every token. Mitigation: pin the tailwind version in package.json and pick ONE of the two provided files; delete the other from the repo on day one.
- next/font/google requires network access at build time; an offline demo machine will fail the build. Mitigation: run one successful build before the venue, keep .next/cache, or fall back to self-hosted woff2 in apps/web/public/fonts with @font-face.
- Inter feature tags cv05/ss02 are version-dependent and may silently no-op. Mitigation: the design does not depend on them — tnum and zero (both stable) carry the plate-disambiguation requirement; cv05/ss02 are cosmetic. Marked Verify inline.
- Severity enum drift between frontend tokens, the FastAPI response model and the Postgres check constraint would render badges as unstyled fallbacks. Mitigation: 'critical|high|medium|low|info' is published in key_contracts and must be a shared enum; 'success' is deliberately excluded from severity.
- Row-flash animation on a high-throughput realtime feed can cause continuous repaints and drop the map to single-digit FPS on the 16 GB / RX 6500M dev machine. Mitigation: animate background-color only (no layout properties), cap the visible alert feed at 200 rows with virtualisation, and disable the flash when more than 5 rows arrive per second.
- Severity -wash row backgrounds combined with zebra striping and hover produce three competing backgrounds and unreadable tables. Mitigation: zebra is off by default and washes apply only to critical and high rows.
- 11px micro-labels can be illegible on a low-quality projector at the venue. Mitigation: they are 600-weight at 0.08em tracking and 5.86:1; do a projector rehearsal and, if needed, bump --text-micro to 12px globally (single token change, no layout break since line-height stays 12px -> raise to 14px).

## Appendix — Open Questions

- Is the repo pinned to Tailwind v4 or v3.4? This section assumes v4 with @theme; the v3 config is provided but only one may exist in the repo.
- Does the DB severity enum use exactly 'critical|high|medium|low|info', and is system health a separate column/enum? If the DB models severity as an integer 1-5, the frontend needs a mapping table and this section's token names must be aliased.
- Is char_confidences (float array, one entry per plate character) actually persisted by the OCR worker, or only a single aggregate plate confidence? PlateText degrades gracefully to plain text if absent, but the per-character tint is a strong demo moment.
- Is a dark mode toggle wanted on stage at all, or should the .dark block be deleted to reduce surface area?
- Confirm the app is mounted at apps/web in the monorepo (this section's file paths assume it); the repo-structure author owns that decision.
- Will the venue projector be 1920x1080 at 100% scaling? If it is 1366x768, the 240px sidebar + 420px drawer combination must be replaced by the 56px rail plus an overlay drawer.
