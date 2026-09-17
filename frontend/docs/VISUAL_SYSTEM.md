# Visual System — Phase 1

**Dark by default.** Yellow accent for exactly three uses. Status colors for messaging. Motion only for live work.

## Theme

The platform is dark mode first, optimized for reduced eye strain and modern aesthetics. Users can explicitly choose light mode.

- **Default (OS dark preference or no preference):** Dark theme
- **Explicit user choice:** Light or dark, via a theme toggle (not yet built)
- **CSS tokens:** All colors are CSS custom properties (`--color-*`) that respond to `:root`, `@media (prefers-color-scheme)`, and `[data-theme]` attributes in `app/globals.css`

## Color Palette

### Text (Ink)
- `text-ink` — body text and primary headings
- `text-ink-soft` — secondary text and disabled states
- `text-ink-muted` — tertiary text, labels, hints
- `text-ink-faint` — very subtle text (use sparingly)

Dark mode: `#f0f2f5` → `#64748b`
Light mode: `#0f1d29` → `#8b9aab`

### Surface & Paper
- `bg-paper` — the page background, usually unseen (scrolls under the main view)
- `bg-surface` — cards, dialogs, input backgrounds
- `border-border` — primary dividers and strokes
- `border-border-soft` — very subtle strokes and hover states

Dark mode: paper `#0f1418`, surface `#1a1f26`, border `#2d3139`
Light mode: paper `#f7f8fa`, surface `#ffffff`, border `#e3edf3`

### Accent: Yellow `#FFDD00`
**Used for exactly three things:**

1. **Agent working** — a Spinner or progress indicator showing an agent is running
2. **Primary action** — the one button the user should click next (`.btn-primary`)
3. **Focus** — keyboard focus ring on interactive elements (visible with Tab)

Do not use yellow for:
- Secondary buttons (use `bg-surface` instead)
- Status indicators (use ok/warn/crit/info)
- Hover states on non-primary elements
- Decorative elements

### Status Colors

Used for semantic messaging. Each has text and background variants.

#### OK (positive/success)
- `text-ok` / `bg-ok-bg` — for successful states and affirmative messaging
- Dark: text `#10b981`, bg `rgba(16, 185, 129, 0.1)`
- Light: text `#1f7a55`, bg `rgba(31, 122, 85, 0.1)`

#### Warn (caution/warning)
- `text-warn` / `bg-warn-bg` — for warnings and cautionary messaging
- Dark: text `#f59e0b`, bg `rgba(245, 158, 11, 0.1)`
- Light: text `#b9852b`, bg `rgba(185, 133, 43, 0.1)`

#### Crit (critical/error)
- `text-crit` / `bg-crit-bg` — for errors and critical states
- Dark: text `#ef4444`, bg `rgba(239, 68, 68, 0.1)`
- Light: text `#b23b3b`, bg `rgba(178, 59, 59, 0.1)`

#### Info
- `text-info` / `bg-info-bg` — for informational messaging
- Dark: text `#3b82f6`, bg `rgba(59, 130, 246, 0.1)`
- Light: text `#2563eb`, bg `rgba(37, 99, 235, 0.1)`

## Components

### Buttons

**Primary (`.btn-primary`)** — yellow, for the main action
```tsx
<button className="btn-primary">Approve</button>
```

**Secondary (`.btn-secondary`)** — surface with border, for secondary actions
```tsx
<button className="btn-secondary">Dismiss</button>
```

**Ghost (`.btn-ghost`)** — transparent, for tertiary actions
```tsx
<button className="btn-ghost">Cancel</button>
```

**Outline (`.btn-outline`)** — bordered surface, for contextual actions
```tsx
<button className="btn-outline">Learn more</button>
```

**Status buttons** — for state-specific actions
```tsx
<button className="btn-ok">Accept</button>
<button className="btn-warn">Review</button>
<button className="btn-crit">Delete</button>
```

### Cards (`.card`)
Cards use `bg-surface` with a subtle border and shadow. The border is always `border-border` (no semantic colors for structure).

```tsx
<div className="card p-4">
  <h3 className="text-ink font-semibold">Title</h3>
  <p className="text-ink-muted">Description</p>
</div>
```

### Input (`.input`)
Inputs use `bg-surface` with `border-border`. Focus brings an accent ring.

```tsx
<input className="input" placeholder="Type here…" />
```

### Status Badges
Use inline status colors with the `.chip` class:

```tsx
<span className="chip bg-ok-bg text-ok">Approved</span>
<span className="chip bg-warn-bg text-warn">Pending</span>
<span className="chip bg-crit-bg text-crit">Failed</span>
<span className="chip bg-info-bg text-info">Info</span>
```

## Motion

Motion is used for two things: **live work** (agent running, data loading) and **arriving messages**.

### Agent Working (`.agent-working`)
Use a subtle pulse animation when an agent is running. The animation collapses to a static opacity on `prefers-reduced-motion`.

```tsx
{busy && <div className="agent-working">Processing…</div>}
```

### Message Arrival (`.fade-in`)
New messages fade in over 0.3s. The animation is disabled if `prefers-reduced-motion` is set.

```tsx
{newMessage && <div className="fade-in">{newMessage.text}</div>}
```

Do not use motion for:
- Hover states on buttons (use color only)
- Scrolling (let the browser handle it)
- Animated backgrounds or decorative effects
- Loading spinners in static UI (motion only for live work)

## Tokens in Code

All colors are CSS custom properties, not hardcoded values:

**✓ Correct:**
```css
.my-component {
  color: var(--color-ink);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}
```

**✗ Incorrect:**
```css
.my-component {
  color: #0f1418;
  background: #1a1f26;
  border: 1px solid #2d3139;
}
```

In Tailwind classes, use the semantic tokens defined in `tailwind.config.ts`:

**✓ Correct:**
```tsx
<div className="bg-surface text-ink border-border">…</div>
```

**✗ Incorrect:**
```tsx
<div className="bg-slate-900 text-yellow-50 border-slate-700">…</div>
```

## Updating the Palette

To change colors for the entire app:
1. Update both light and dark values in `app/globals.css`
2. Test with `prefers-color-scheme` on OS settings and explicit `data-theme` attribute
3. Verify contrast ratios for accessibility

To add a new semantic color:
1. Add the token to `:root`, `@media (prefers-color-scheme: light)`, and `[data-theme]` blocks in `globals.css`
2. Add it to `tailwind.config.ts` under `colors`
3. Use it consistently: always with both text and background variants, never as a structural color

## Accessibility

- **Contrast:** All text meets WCAG AA standards (4.5:1 for body, 3:1 for large text)
- **Motion:** `prefers-reduced-motion` collapses all animations to static states
- **Focus:** Keyboard focus uses the accent ring at all times, never hidden
- **Color alone:** Status is conveyed with color AND shape or text, never color alone
