# PortalPoint — Design Palette
> Derived from the official PortalPoint pitch deck and check-in presentation.  
> Use this as the single source of truth for all frontend styling decisions.

---

## Colors

### Core Palette

| Token | Hex | Usage |
|---|---|---|
| `--color-bg-primary` | `#1B2838` | Main slide / page background (slate navy) |
| `--color-bg-secondary` | `#263849` | Card backgrounds, secondary panels |
| `--color-bg-surface` | `#FFFFFF1A` | Translucent white surface (≈10% opacity white on dark bg) — used for content cards |
| `--color-accent-orange` | `#FF6B35` | Primary accent: CTAs, section labels, left-edge accent bars, bullet markers |
| `--color-accent-blue` | `#5BA3E8` | Secondary accent: section headers ("Progress/Updates"), numbered steps, links |
| `--color-accent-cream` | `#f3e5d0` | Warm accent from slide deck: text selection, scrollbar thumb, active route highlight |
| `--color-text-primary` | `#F2F6FA` | All primary body text, headings on dark backgrounds |
| `--color-text-secondary` | `#A8B9CC` | Supporting / descriptive text (muted steel blue) — captions, sub-bullets |
| `--color-text-muted` | `rgba(242, 246, 250, 0.60)` | Tertiary text, metadata, timestamps (≈60% opacity white) |

### Status Colors

Used in build status views, pipeline tracking, and any pass/fail indicators.

| Token | Hex | Usage |
|---|---|---|
| `--color-status-success` | `#4CAF50` | Complete ✓ — green |
| `--color-status-error` | `#F44336` | Not started ✗ — red |
| `--color-status-warning` | `#FF9800` | In progress / stub ⚠ — amber |
| `--color-status-info` | `#5BA3E8` | Informational highlights — blue (same as accent-blue) |

### Functional Overlays

| Token | Value | Usage |
|---|---|---|
| `--color-overlay-card` | `rgba(255,255,255,0.10)` | Translucent card background on dark bg |
| `--color-overlay-card-accent` | `rgba(255,107,53,0.12)` | Orange-tinted card (highlighted/active state) |
| `--color-overlay-card-blue` | `rgba(91,163,232,0.12)` | Blue-tinted card (informational state) |
| `--color-overlay-card-cream` | `rgba(243,229,208,0.15)` | Cream-tinted surface (active route highlight) |
| `--color-border-orange` | `#FF6B35` | Card border, active states, left-edge accent |
| `--color-border-blue` | `#5BA3E8` | Comparison/before-state card border |
| `--color-border-subtle` | `rgba(255,255,255,0.14)` | Subtle dividers, inactive borders |

---

## Typography

### Font Family

The deck uses the **Inter** type family exclusively. All weights are available as embedded fonts.

```css
font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
```

| Weight | CSS Weight | Usage |
|---|---|---|
| Inter Light | `300` | Body copy, descriptive sub-text, captions |
| Inter | `400` | Regular body, labels |
| Inter SemiBold | `600` | List item headers, emphasized body, bullet labels |
| Inter Bold | `700` | Section subheadings, card titles |
| Inter Black | `900` | Slide titles, hero headings (italic style applied) |

### Type Scale

Derived from PPTX `sz` values (sz in hundredths of a point → divide by 100 for pt, ×1.333 for px at 96dpi).

| Token | pt | px (approx) | Usage |
|---|---|---|---|
| `--text-hero` | 19pt | 25px | Slide titles / page hero headings (Inter Black, italic) |
| `--text-section-header` | 12pt | 16px | Section label headers ("KEY MILESTONES", "INFRASTRUCTURE") |
| `--text-card-title` | 10pt | 13px | Card/component titles, subsection names |
| `--text-body-lg` | 9pt | 12px | Primary body copy, milestone labels, list headers |
| `--text-body` | 8.5pt | 11px | Standard body, bullet descriptions |
| `--text-body-sm` | 8pt | 11px | Secondary body, supporting copy |
| `--text-caption` | 7.5pt | 10px | Captions, metadata, small labels |
| `--text-micro` | 6pt | 8px | Minimal labels, icon badges |

### Type Styles (Compositions)

```css
/* Page / slide title */
.text-hero {
  font-family: 'Inter', sans-serif;
  font-weight: 900;         /* Inter Black */
  font-style: italic;
  font-size: 25px;
  color: #F2F6FA;
  line-height: 1.15;
}

/* Section label (e.g. "KEY MILESTONES", "CRITICAL PATH") */
.text-section-label {
  font-family: 'Inter', sans-serif;
  font-weight: 700;
  font-size: 13px;
  color: #FF6B35;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

/* Secondary section label (e.g. "Progress / Updates") */
.text-section-label-blue {
  font-family: 'Inter', sans-serif;
  font-weight: 700;
  font-size: 13px;
  color: #5BA3E8;
  letter-spacing: 0.04em;
}

/* List item header / card title */
.text-item-header {
  font-family: 'Inter', sans-serif;
  font-weight: 600;
  font-size: 12px;
  color: #F2F6FA;
}

/* Body / descriptive copy */
.text-body {
  font-family: 'Inter', sans-serif;
  font-weight: 300;         /* Inter Light */
  font-size: 11px;
  color: #A8B9CC;
  line-height: 1.5;
}

/* Highlighted / key takeaway */
.text-highlight {
  font-family: 'Inter', sans-serif;
  font-weight: 600;
  font-size: 12px;
  color: #5BA3E8;
}

/* Status: success */
.text-status-success { color: #4CAF50; font-weight: 400; }

/* Status: error */
.text-status-error   { color: #F44336; font-weight: 400; }

/* Status: warning */
.text-status-warning { color: #FF9800; font-weight: 400; }
```

---

## Spacing & Layout

### Grid

The slide canvas is 9144000 × 5143500 EMU (12" × 6.75" at 96dpi = **1152px × 648px**).  
For web, scale proportionally or treat as a 16:9 layout grid.

| Token | Value | Notes |
|---|---|---|
| `--spacing-page-margin` | `32px` | Edge margin (left/right) — approx. 285750 EMU from slide edge |
| `--spacing-section-gap` | `24px` | Between major sections on a page |
| `--spacing-card-gap` | `16px` | Between sibling cards |
| `--spacing-card-padding` | `16px 20px` | Internal card padding (top/bottom 16px, left/right 20px) |
| `--spacing-item-gap` | `8px` | Between list items within a card |
| `--spacing-label-gap` | `4px` | Between a label and its description line |

### Column Layouts

The deck uses consistent 2-column and 3-column layouts for content slides.

```
2-column (even split):
  Left col:   ~45% width
  Right col:  ~48% width
  Gutter:     ~7%

3-column (equal thirds):
  Each col:   ~29% width
  Gutters:    ~6.5% each

Left-accent bar:
  Width:       6–8px
  Color:       #FF6B35
  Position:    flush to left edge of header area
```

---

## Component Patterns

### Page / View Header

```
┌─────────────────────────────────────────────────┐
│ ▌  Hero Title (Inter Black, italic, 25px white) │
│    Subtitle line (Inter Light, 11px #A8B9CC)    │
└─────────────────────────────────────────────────┘

▌ = 6px wide left-edge accent bar in #FF6B35
```

### Section Label

A short all-caps label in orange or blue that introduces a content block. No underline, no decorative bar below — use whitespace above and below to separate.

```css
/* Orange variant — primary sections */
color: #FF6B35;
font-weight: 700;
font-size: 13px;
text-transform: uppercase;
letter-spacing: 0.04em;
margin-bottom: 12px;

/* Blue variant — secondary / right-column sections */
color: #5BA3E8;
```

### Content Card

Semi-transparent surface on the dark background. Two border variants:

```css
/* Standard card */
.card {
  background: rgba(255, 255, 255, 0.10);
  border-radius: 4px;
  padding: 16px 20px;
}

/* Orange-accented card (highlighted / active) */
.card-accent-orange {
  background: rgba(255, 107, 53, 0.12);
  border: 1.5px solid #FF6B35;
  border-radius: 4px;
  padding: 16px 20px;
}

/* Blue-accented card (informational / before-state) */
.card-accent-blue {
  background: rgba(74, 144, 226, 0.12);
  border: 1.5px solid #5BA3E8;
  border-radius: 4px;
  padding: 16px 20px;
}
```

### Bullet List

```css
/* Orange filled circle bullet (primary list) */
li::before {
  content: '●';
  color: #FF6B35;
  margin-right: 10px;
}

/* Blue star bullet (key takeaway / highlighted insight) */
li.highlight::before {
  content: '★';
  color: #5BA3E8;
}

/* List item header */
li .item-header {
  font-weight: 600;
  color: #F2F6FA;
  font-size: 12px;
}

/* List item description */
li .item-desc {
  font-weight: 300;
  color: #A8B9CC;
  font-size: 11px;
  margin-top: 2px;
}
```

### Status Indicator

Used in build trackers, pipeline views, model status lists.

```
✓  Complete      #4CAF50  font-weight: 400
✗  Not started   #F44336  font-weight: 400
⚠  In progress   #FF9800  font-weight: 400
★  Key insight   #5BA3E8  font-weight: 600
```

```css
.status-success { color: #4CAF50; }
.status-error   { color: #F44336; }
.status-warning { color: #FF9800; }
.status-info    { color: #5BA3E8; font-weight: 600; }
```

### Numbered Step (Critical Path)

```css
.step-number {
  color: #5BA3E8;
  font-weight: 700;
  font-size: 12px;
  margin-right: 6px;
}

.step-label {
  color: #F2F6FA;
  font-weight: 700;
  font-size: 12px;
}

.step-desc {
  color: #A8B9CC;
  font-weight: 300;
  font-size: 11px;
  margin-left: 18px;    /* indent under step label */
  margin-top: 2px;
}
```

### Before / After Comparison

```
┌──────────────────────────┐     →     ┌──────────────────────────┐
│  BEFORE                  │           │  AFTER ✓                 │
│  (blue border)           │           │  (orange border + tint)  │
│  color: #5BA3E8          │           │  color: #FF6B35          │
└──────────────────────────┘           └──────────────────────────┘
```

Arrow (`→`) between panels: `color: #FF6B35`, `font-size: 32px`.

### Footer / Banner Strip

Low-opacity tinted strip pinned to the bottom of a view, summarizing key metrics or targets.

```css
.footer-banner {
  background: rgba(255, 107, 53, 0.15);
  padding: 10px 24px;
  border-radius: 4px;
}

.footer-banner .label {
  font-weight: 700;
  color: #F2F6FA;
  font-size: 11px;
}

.footer-banner .value {
  font-weight: 300;
  color: #A8B9CC;
  font-size: 11px;
}
```

---

## CSS Custom Properties — Full Token Sheet

Paste this into your global stylesheet or design token file:

```css
:root {
  /* ── Backgrounds ── */
  --color-bg-primary:          #1B2838;
  --color-bg-secondary:        #263849;
  --color-bg-surface:          rgba(255, 255, 255, 0.10);
  --color-bg-surface-orange:   rgba(255, 107, 53, 0.12);
  --color-bg-surface-blue:     rgba(91, 163, 232, 0.12);
  --color-bg-surface-cream:    rgba(243, 229, 208, 0.15);

  /* ── Accents ── */
  --color-accent-orange:       #FF6B35;
  --color-accent-blue:         #5BA3E8;
  --color-accent-cream:        #f3e5d0;

  /* ── Text ── */
  --color-text-primary:        #F2F6FA;
  --color-text-secondary:      #A8B9CC;
  --color-text-muted:          rgba(242, 246, 250, 0.60);

  /* ── Borders ── */
  --color-border-orange:       #FF6B35;
  --color-border-blue:         #5BA3E8;
  --color-border-subtle:       rgba(255, 255, 255, 0.14);

  /* ── Status ── */
  --color-status-success:      #4CAF50;
  --color-status-error:        #F44336;
  --color-status-warning:      #FF9800;
  --color-status-info:         #5BA3E8;

  /* ── Typography ── */
  --font-family-base:          'Inter', -apple-system, BlinkMacSystemFont, sans-serif;

  --font-weight-light:         300;
  --font-weight-regular:       400;
  --font-weight-semibold:      600;
  --font-weight-bold:          700;
  --font-weight-black:         900;

  --text-hero:                 25px;
  --text-section-header:       16px;
  --text-card-title:           13px;
  --text-body-lg:              12px;
  --text-body:                 11px;
  --text-body-sm:              11px;
  --text-caption:              10px;
  --text-micro:                 8px;

  /* ── Spacing ── */
  --spacing-page-margin:       32px;
  --spacing-section-gap:       24px;
  --spacing-card-gap:          16px;
  --spacing-card-padding:      16px 20px;
  --spacing-item-gap:           8px;
  --spacing-label-gap:          4px;

  /* ── Shape ── */
  --radius-card:                4px;
  --radius-badge:               2px;
  --accent-bar-width:           6px;
}
```

---

## Tailwind Config (if using Tailwind CSS)

```js
// tailwind.config.js
module.exports = {
  theme: {
    extend: {
      colors: {
        'pp-navy':       '#1B2838',
        'pp-navy-light': '#263849',
        'pp-orange':     '#FF6B35',
        'pp-blue':       '#5BA3E8',
        'pp-cream':      '#f3e5d0',
        'pp-slate':      '#A8B9CC',
        'pp-success':    '#4CAF50',
        'pp-error':      '#F44336',
        'pp-warning':    '#FF9800',
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
      },
      fontWeight: {
        light:     '300',
        semibold:  '600',
        black:     '900',
      },
      fontSize: {
        'hero':    ['25px', { lineHeight: '1.15' }],
        'section': ['16px', { lineHeight: '1.3'  }],
        'card':    ['13px', { lineHeight: '1.4'  }],
        'body-lg': ['12px', { lineHeight: '1.5'  }],
        'body':    ['11px', { lineHeight: '1.5'  }],
        'caption': ['10px', { lineHeight: '1.4'  }],
      },
      borderWidth: {
        'accent': '6px',
      },
      borderRadius: {
        'card': '4px',
      },
      backgroundOpacity: {
        '10': '0.10',
        '12': '0.12',
        '15': '0.15',
      },
    },
  },
}
```

---

## Do's and Don'ts

**Do:**
- Always use `#1B2838` as the page background — never white or grey
- Use `#FF6B35` sparingly: CTAs, section labels, accent bars, bullet markers only
- Use `#5BA3E8` for secondary section headers, links, and numbered steps
- Use `#f3e5d0` as a warm accent for selection, scrollbars, and active-route highlights
- Use `#A8B9CC` for all supporting / descriptive text (not pure white)
- Apply Inter Light (`300`) for body copy and Inter SemiBold (`600`) for list headers
- Use translucent cards (`rgba(255,255,255,0.10)`) rather than opaque boxes
- Include the 6px orange left-edge accent bar on page/section headers

**Don't:**
- Don't use white (`#F2F6FA`) as body copy color — use `#A8B9CC` for secondary text
- Don't use orange and blue together as background fills — they are accent-only
- Don't use border-radius > 4px — the deck aesthetic is near-flat, not rounded
- Don't add decorative dividers or horizontal rules — use whitespace
- Don't use any font other than Inter
- Don't use colored backgrounds on text labels (pill badges) unless clearly a status indicator
