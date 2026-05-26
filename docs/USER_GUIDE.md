# Forme Studio — User Guide

A practical walkthrough for a packaging designer using Forme to take a
brief from idea to print-ready PSD.

> Forme is a **single-tenant local tool**. Everything lives on the
> machine running the backend: the SQLite database, the workspaces,
> generations, references, exports and audit logs.

---

## 1. Concepts

| Term         | Meaning                                                                 |
| ------------ | ----------------------------------------------------------------------- |
| **Workspace**| One product / SKU you're designing for. Freezes its print specs.       |
| **Preset**   | The recipe of a product type (trim, bleed, DPI, colour space, etc.).   |
| **Generation**| A gpt-image-2 output that landed inside a workspace.                   |
| **Reference**| Any image you uploaded into a workspace (logo, mood, brand photo).      |
| **Edit**     | A new generation whose visual input was an existing variant + refs.    |
| **Export**   | A print-ready file derived from a generation (Tier A PSD today).       |
| **Audit row**| Every state change is logged to SQLite *and* a JSONL file on disk.     |

---

## 2. The big loop

```
brief        →  Generate     →  Iterate (Edit)  →  Export
(your text)     (gpt-image-2)   (gpt-image-2)       (PSD/PDF/SVG)
                ↑                                       ↓
        References (logos, mood, brand photos)         print
```

Forme is built around iterative refinement: generate first, then refine
specific variants by feeding them back as the base for an edit, possibly
alongside extra reference images.

---

## 3. Walkthrough — your first design

### 3.1 Create a workspace

1. Click **+ New workspace** in the top-right.
2. Type a name — usually the product / SKU.
   Example: *Glow Serenity Lotion 250 ml*.
3. Pick a **Product type**. The presets list five common formats:
   - Lotion bottle label (70 × 100 mm)
   - Cream jar label (60 × 60 mm)
   - Cream box, tuck-end (140 × 50 mm front)
   - Serum dropper label (50 × 80 mm)
   - Shampoo pouch (90 × 120 mm)
   Each preset shows its frozen specs in a live preview.
4. (Optional) Add a short brief in the description field.
5. **Create workspace.** Forme creates the on-disk folder tree:
   ```
   workspaces/<your-slug>/
   ├── brief.md           ← edit freely
   ├── references/
   ├── generations/
   ├── exports/
   └── audit.log.jsonl    ← do not edit
   ```

### 3.2 Add references (optional but recommended)

In the workspace detail page, the **References panel** sits at the top:

- **Drop images** anywhere in the box, or click to browse.
- Forme accepts up to **16 files per upload**, **25 MB each**, JPEG / PNG / WEBP.
- Every upload is re-encoded as RGBA PNG with EXIF rotation baked in, so
  iPhone photos don't trip the model later.
- Click a thumbnail to **select** it for the next run (clay-coloured ring +
  ✓ badge). Click again to deselect.

> **gpt-image-2 sees up to 16 references per call.** Forme caps the
> selection at 16; the Generate / Run edit button disables if you exceed.

### 3.3 Write the brief

- In generate mode the textarea is labelled **Brief** — what the product
  needs to communicate (style, mood, audience, key visual cues).
- In edit mode it's relabelled **Edit instruction** — the *diff* you want
  applied, not the whole brief. The original brief shows below the box as
  a read-only caption.

### 3.4 Pick variants + quality

- **Variants**: 1–4 per run. gpt-image-2 charges per variant.
- **Quality**:
  - *Draft* — fast preview (~12 s)
  - *Medium* — balanced
  - *High* — production (~55 s, default)
  - *Auto* — let OpenAI choose

### 3.5 Generate

Hit **Generate**.

- Forme streams **partial images** as gpt-image-2 produces them.
  You'll see a blurred low-res preview in each variant tile, upgrading
  to the final crisp PNG as it lands.
- The status strip shows **elapsed time / expected time** and a progress
  bar. The expected time depends on your quality choice.
- **Cost is reported live** with both the OpenAI provider amount and the
  user-facing amount (provider + markup, configurable in
  *Settings → Model & cost*).
- The new variants land in the **Gallery** below.

If a run errors mid-stream (e.g. content moderation) you'll see the
error message and the partial frames are discarded.

### 3.6 Iterate — Edit

Hover any variant tile (live run or gallery) and click **Edit**.

- The studio flips into **edit mode**. A banner shows the base variant
  with its filename + your reference-selection count.
- Write only the *change* in the textarea ("Change the background to deep
  sage", "Make the typography thinner", "Add a small botanical mark in
  the bottom-right").
- Toggle reference thumbnails on/off — they're stacked with the base as
  inputs to gpt-image-2's edit API.
- Click **Run edit**.

Forme writes the new output as a **fresh generation** linked back to its
base via the audit chain (`asset.edited` event with `edit_of` +
`references`). Nothing is overwritten.

To go back to a fresh generation, click the X on the edit banner or hit
Cancel.

### 3.7 Export as PSD

The PSD button is a **split control**: click the left half for an
instant Tier A export, or the chevron on the right for the full menu:

| Tier | What it is | Use when |
| --- | --- | --- |
| **A · Flat** | Single CMYK layer at 300 DPI | Quick proof, simple SKUs, fastest |
| **B · Layered** | Tier A + one alpha layer per SAM-2 mask | Designer needs to swap/move regions |
| **C · Editable text** | Tier B + OCR'd text overlays + JSON sidecar | Designer needs to retype copy without re-rendering |

- The file downloads immediately *and* persists under
  `workspaces/<slug>/exports/`.
- Tier C also writes a sidecar `.ocr.json` listing every detected text
  region with `text`, `bbox`, and `confidence`.
- The **Exports** section at the bottom of the page lists every export
  with filename, tier, size, and a re-download link.

#### When Tier B / C are unavailable

- **Tier B grey** → segmentation provider isn't reachable. Open
  Settings → flip `Segmentation provider` to `replicate` (paid) or
  `self_hosted` (your DGX), and confirm the credentials in the
  Credentials section are set.
- **Tier C grey** → either Tier B isn't ready, Tesseract isn't on the
  configured path, or `FORME_TIER_C_ENABLED` is off. The Settings page
  shows all three and explains the fix.

Forme never auto-falls-back. If the primary segmentation provider
errors mid-export the error is surfaced and you choose what to do — the
output that lands on disk is always anchored to a known provider.

### 3.8 Export as Print PDF/X-4

Next to the PSD split-button on every variant is a dark **PDF** button.
One click produces a press-ready PDF/X-4-compatible file:

| Box / mark | What it is | Default |
| --- | --- | --- |
| **MediaBox** | Total page area incl. bleed | `trim + 2 × bleed` (mm → pts) |
| **TrimBox** | Final cut line | workspace's frozen trim |
| **BleedBox** | Bleed area for press tolerance | same as MediaBox |
| **Trim marks** | 5 mm corner crop marks just outside trim | on |
| **Registration marks** | Bullseye targets at each side mid-edge in CMYK process-black | on |
| **OutputIntent** | Embedded ICC + `GTS_PDFX` marker | from configured ICC file |

The colour conversion uses the ICC profile configured in
**Settings → Print PDF/X-4** (defaults to macOS *Generic CMYK*; swap in
ISO Coated v2 for European press-grade colour). If the ICC file is
missing, Forme falls back to Pillow's baseline RGB→CMYK conversion and
the OutputIntent is skipped — the PDF still ships with trim/bleed boxes
and marks, but isn't colour-managed.

#### Verifying the PDF

Open in Adobe Acrobat (or any X-4-aware previewer) → **File → Properties
→ Description**. You should see:

- Title: `<variant filename> — Forme Studio`
- Creator: `Forme Studio`
- Output Intent: `Generic CMYK` (or whatever you configured)

Press shops will check that **TrimBox** and **BleedBox** match the spec
you sent in your job ticket. Forme writes both directly from the
workspace's frozen specs, so they're guaranteed correct.

### 3.9 Export as Vector (SVG)

Next to the PSD ▾ and PDF buttons is a clay-coloured **SVG** button. One
click vectorises the variant into an `.svg` file that lands in the
workspace's `exports/` folder and is logged as `export.vector.created`.

| Provider              | When to use                                                                |
| --------------------- | -------------------------------------------------------------------------- |
| **Vectorizer.AI**     | Anything photo-real, multi-colour illustration, or a layout you'll re-style in Illustrator. Costs ~1 credit per image in `production` mode. |
| **Inkscape Potrace**  | Mono-colour logos and marks where you just need clean curves on a single colour. Free, runs locally. |

The configured **primary** (Settings → Provider routing → *Vectoriser
primary*) runs first. **Fallback is never automatic.** If the primary
fails — quota exhausted, model error, network timeout, Inkscape not
installed — Forme shows the upstream error in the Exports section with
a **Try with <fallback>?** button. One click reruns the same source
through the fallback provider; nothing else changes.

#### Choosing a Vectorizer.AI mode

Settings → Provider routing → *Vectorizer.AI mode*:

- **production** — 1 credit per image. The output you'd ship to the press.
- **test** — 0.1 credit per image, but the result carries a watermark
  and is downscaled. Use this while iterating on the UI / pipeline so
  you don't burn credits proofing a flow.
- **preview** — free, smaller dimensions, suitable for thumbnails only.

You can flip modes from the Settings page; the change takes effect on
the next request.

#### Verifying the SVG

The SVG opens cleanly in Illustrator / Affinity / Inkscape. Vectorizer.AI
output ships with multiple colour layers; Inkscape Potrace produces a
single-colour silhouette path that you can recolour by hand.

### 3.10 Export as CDR (CorelDRAW)

Next to the SVG button is a sage **CDR** button. One click runs a
two-stage pipeline:

1. **Vectorize** the variant PNG → SVG (same engine as §3.9 — primary
   uses your `FORME_VECTORIZER_PROVIDER` setting).
2. **Convert** the SVG → `.cdr` using one of two engines.

| Engine                | When to use                                                |
| --------------------- | ---------------------------------------------------------- |
| **UniConvertor 2**    | Default. Free, runs locally via the sK1 Project's CLI. Install once and forget. |
| **CloudConvert**      | Paid, hosted. Use when UniConvertor isn't installed or fails on a complex SVG. Needs `CLOUDCONVERT_API_KEY` in `.env`. |

If either stage errors, Forme shows the upstream error in the Exports
section and offers a *"Retry CDR via <fallback>"* button — same
non-auto-fallback contract as §3.9. The two stages have **independent**
fallbacks: a vector-stage failure shows the vector retry button, a CDR
stage failure shows the CDR retry button.

#### Honest note about Inkscape

Inkscape's CLI **cannot export CDR** — it only imports it (via
`libcdr`). The original roadmap line hinted at "CDR via Inkscape CLI";
that turned out to not exist. UniConvertor and CloudConvert are the two
real options and Forme wires both.

---

## 4. The Settings page

Top-right navigation: **Settings**.

Four sections, top to bottom:

1. **Provider routing** — pick your vector + segmentation providers.
   Both stages have a *primary* and a *fallback*. Fallbacks are **never
   auto-invoked**; if a primary fails the UI shows the error + a
   "Try with fallback?" button (post-MVP).
2. **Model & cost** — image model snapshot pin, markup percent over the
   OpenAI bill, request timeout.
3. **Credentials** — every secret is shown **redacted**
   (`••••••••••••sk-XXXX`). To rotate any key, edit
   `backend/.env` directly. The backend hot-reloads on file save.
4. **Local tools & server** — Inkscape CLI path, log level, workspaces
   directory (read-only), database path (read-only).

Hit **Save changes** at the bottom. Writes go back to `backend/.env`;
uvicorn restarts the worker and your changes take effect on the next
request.

---

## 5. Where the files live

```
forme-studio/
├── backend/                   FastAPI + SQLite + services
│   ├── .env                   ← all credentials + provider routing
│   └── app/                   ← source
├── frontend/                  Next.js 14 app
├── workspaces/                ← per-workspace folders
│   └── <slug>/
│       ├── brief.md
│       ├── references/        ← user uploads
│       ├── generations/       ← AI outputs
│       ├── exports/           ← print deliverables
│       └── audit.log.jsonl    ← canonical event log
├── forme.db                   ← SQLite (canonical state)
└── docs/                      ← these files
```

Workspaces are **portable**: copy the folder, and every artefact + the
JSONL audit trail goes with it. SQLite is the canonical store, but the
JSONL mirror means a workspace folder is self-describing.

---

## 6. Cost ballpark

| Action               | Provider cost (typical, gpt-image-2, high quality) |
| -------------------- | -------------------------------------------------- |
| Generate 1 variant   | ~$0.10–$0.20                                       |
| Generate 4 variants  | ~$0.40–$0.80                                       |
| Edit (with refs)     | Higher — every reference is billed as image input  |
| PSD export           | $0 — local                                         |

A typical pilot SKU (3–4 rounds of iteration, 8–12 variants total) lands
between **$1 and $3** of OpenAI spend.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Amber banner: "OPENAI_API_KEY isn't configured" | Empty key in `.env` | Paste a real `sk-…` key, save `.env` |
| Generate button disabled | < 4 chars in brief, OR > 16 refs selected, OR no API key | Address what the banner says |
| Stream errors mid-run with "content moderation" or similar | OpenAI rejected the prompt | Re-word the brief; political/electoral content is killed |
| PSD opens at 72 DPI in Photoshop | Old export before the resolution-info patch | Re-export — fixed in the current build |
| Capability dot stays grey in header | Provider key missing / Inkscape not installed | Open Settings — the dashboard will show what's missing |

For anything else, the live OpenAPI docs are at
<http://127.0.0.1:8002/docs> — every endpoint is documented there with
its exact request/response shape.
