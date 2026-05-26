# Forme Studio — Design Guide

The visual system behind the product. Keep this file open when adding
new pages so the look stays coherent.

---

## 1. Brand idea

> **"Forme"** is the printer's term for the locked-up bed of type in a
> letterpress — the precise frozen arrangement that gets pressed into
> paper.

Every workspace freezes its print specs the moment it's created (trim,
bleed, DPI, colour space). The brand reflects that idea: things land
into a *forme*, and from there every export is anchored.

**Voice**: calm, precise, slightly old-press. Avoid AI-marketing-speak;
Forme is a craft tool, not a magic wand.

---

## 2. Logo

The mark is a **rounded square plate** (the press bed) with a heavy
sans-serif **F** in paper-white, with a small clay registration tick at
the bottom-right.

- File: `frontend/components/Logo.tsx`
- Defaults: 28 px box, paired with the wordmark "Forme" in Fraunces.
- Single-colour use: black or `ink-900` on light, `paper-100` on dark.

```
┌──────────┐
│ ╶┰╮      │
│  ┃       │
│  ┠──     │
│  ┃       │
│       ●  │
└──────────┘
```

---

## 3. Colour system

Tokens live in `frontend/tailwind.config.ts` + raw CSS vars in
`globals.css`. Use semantic names; never the raw hex.

### 3.1 Surfaces — Paper

The default page background. Warm off-white, slightly creamy.

| Token        | Hex       | Use                                    |
| ------------ | --------- | -------------------------------------- |
| `paper-50`   | `#fdfcf9` | white-y secondary surface              |
| `paper-100`  | `#faf8f2` | **page background**                    |
| `paper-200`  | `#f3efe5` | hover state, subtle card               |
| `paper-300`  | `#e8e2d2` | progress trough, image placeholder bg  |

A subtle dotted **paper grain** is rendered via two CSS radial-gradients
on `body::before` — it's free (no image fetch) and gives the surface
some texture without screaming.

### 3.2 Ink — text + UI hardware

Cool-warm near-black. Slightly warmer than pure grey.

| Token       | Hex       | Use                                    |
| ----------- | --------- | -------------------------------------- |
| `ink-900`   | `#0c0a09` | **headlines, primary buttons**         |
| `ink-800`   | `#1c1917` | secondary text on white                |
| `ink-700`   | `#292524` | tertiary text                          |
| `ink-500`   | `#57534e` | labels, captions                       |
| `ink-400`   | `#78716c` | secondary labels, badges off           |
| `ink-300`   | `#a8a29e` | placeholders, disabled                 |
| `ink-200`   | `#d6d3d1` | **all borders**                        |
| `ink-100`   | `#e7e5e4` | quiet dividers                         |

### 3.3 Accents

| Accent | Tone           | Hex       | Used for                                    |
| ------ | -------------- | --------- | ------------------------------------------- |
| Clay   | `clay-500`     | `#d75827` | progress, links, focus, edit-mode banner    |
|        | `clay-600`     | `#bb3f12` | destructive / primary clay                  |
|        | `clay-50`/`100`| `#fef6f1` | edit-mode banner background, error          |
| Sage   | `sage-500`     | `#577d60` | success, "online", "set" tag                |
|        | `sage-700`     | `#36503d` | export buttons, reference selected ring     |
|        | `sage-50`/`100`| `#f3f6f3` | success banner                              |

**Rule of thumb**: clay = action / in-progress, sage = success /
verified. Both stay quiet — saturated splashes only on interactive
hovers + states.

### 3.4 State colours

- **Success**: sage-50 background, sage-700 text, sage-200 border
- **Warning** (key missing, etc.): amber-50 / amber-800 / amber-200
- **Error**: clay-50 / clay-800 / clay-200
- **Info / neutral**: paper-200 / ink-700 / ink-200

---

## 4. Typography

Two web fonts, both via `next/font` so they self-host:

| Family    | Role       | Notes                                                        |
| --------- | ---------- | ------------------------------------------------------------ |
| Inter     | UI / body  | `--font-inter`. Sans-serif. Body text + buttons + small UI.  |
| Fraunces  | Display    | `--font-fraunces`. Variable serif with optical sizing.       |
| ui-mono   | Mono       | System default. Used for slugs, filenames, code blocks.      |

### 4.1 Scale

- Hero headline: `font-display text-5xl md:text-6xl` (Fraunces)
- Section heading: `font-display text-2xl` or `text-3xl` (Fraunces)
- Card title: `font-display text-lg` (Fraunces)
- Body: `text-sm` Inter (mostly) or `text-base` for long copy
- Label: `text-sm font-medium text-ink-800`
- Hint / micro: `text-xs text-ink-500` or `text-[11px] text-ink-400`

### 4.2 Letter-spacing

Fraunces gets `letter-spacing: -0.01em` at display sizes (already in
`globals.css` via `.font-display`).

Uppercase eyebrows use `tracking-[0.18em]` + `text-[10px]` to feel
press-set.

---

## 5. Layout

- Max content width: **`max-w-6xl`** (~1152 px) for the workspace pages,
  **`max-w-3xl`** (~768 px) for forms (settings, new workspace).
- Page padding: `px-6` on mobile, no extra inset on desktop.
- Vertical rhythm between major sections: `space-y-8` (32 px). Inside a
  card body: `space-y-5` (20 px).

### 5.1 Cards

Always:

```tsx
<Card>
  <CardHeader>
    <CardTitle>Title</CardTitle>
    <Badge tone="neutral">side info</Badge>
  </CardHeader>
  <CardBody className="space-y-5">…</CardBody>
  <CardFooter>…</CardFooter>  {/* optional, for forms */}
</Card>
```

Cards have:
- 1 px `border-ink-200` border
- `shadow-card` (subtle) — `shadow-card-hover` on hover for click cards
- `rounded-xl`
- White (`bg-white/90 backdrop-blur-sm`) background so the paper grain
  shows through faintly

### 5.2 Buttons

Sizes: `sm` (h-8), `md` (h-10, default), `lg` (h-12). All `rounded-md`.

| Variant     | When to use                                  |
| ----------- | -------------------------------------------- |
| `primary`   | Main action on a card / page (one per card)  |
| `secondary` | Same prominence, alternate path              |
| `ghost`     | Cancel, Discard                              |
| `danger`    | Destructive / clay action                    |

Icons go on the left at 14–16 px. Loading state shows a spinner before
children.

### 5.3 Badges

`tone="neutral" | "clay" | "sage" | "warning"`. Use them generously to
label state — particularly **frozen**, **draft**, **partial**, **final**,
**set**, **missing**.

---

## 6. Animation

- All transitions: 150–250 ms, `ease-in-out`.
- Hover state on cards: shadow + a 1.03x scale on the image inside.
- Loading: progress bar at the top of the page on data-fetch
  (`forme-progress` class).
- The page background grain is **static** — no animated noise.

---

## 7. Voice + microcopy

- **No exclamation marks** in the UI. Forme is calm.
- **No emoji**. Use lucide-react icons instead.
- Filenames + workspace slugs are rendered in mono.
- Cost is always shown to 4 decimal places in `font-mono` (e.g.
  `$0.1294`).
- Empty states must explain *why* something is empty + how to fix it,
  not just say "no items".

---

## 8. Icons

`lucide-react` only. Standard sizes:

- Inline with text: 14 px
- Button glyphs: 16 px
- Section heading prefix: 16 px
- Empty-state hero: 20 px

Stroke width is `lucide`'s default (1.5).

---

## 9. Composition checklist

Before merging a new page:

- [ ] Uses `<AppShell>` via the root layout — never a custom header.
- [ ] Page heading is in Fraunces (`font-display`).
- [ ] Max-width set (`max-w-6xl` or `max-w-3xl`).
- [ ] Vertical rhythm via `space-y-*` not ad-hoc margins.
- [ ] All inputs use the shared `<Input>` / `<Textarea>` / `<Select>` from
      `components/ui/Field.tsx`.
- [ ] No raw hex — use Tailwind tokens.
- [ ] No emoji.
- [ ] Loading + error + empty states present.
- [ ] Buttons use the `<Button>` component (right variant, right size).
- [ ] If client-only: `"use client"` at top.
