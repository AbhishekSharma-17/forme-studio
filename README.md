# Forme Studio

AI-assisted packaging & print design. Single-tenant pilot.

> "Forme" — printer's term for the locked-up bed of type in a
> letterpress. Every workspace freezes its print specs (trim, bleed,
> DPI, colour space) the moment it's created, and every later export is
> anchored to that frozen forme.

---

## Quick start

```bash
# 1. Backend on :8002
cd backend
cp .env.example .env             # fill OPENAI_API_KEY etc.
uv sync --extra dev
uv run uvicorn app.main:app --reload --reload-include '*.env' --port 8002

# 2. Frontend on :2002 (in another terminal)
cd frontend
pnpm install
pnpm dev
```

Open <http://localhost:2002> → **+ New workspace** → write a brief →
**Generate** → click **PSD** on a variant. Done.

| URL                                  | What's there                          |
| ------------------------------------ | ------------------------------------- |
| <http://localhost:2002>              | Marketing landing                     |
| <http://localhost:2002/workspaces>   | All workspaces                        |
| <http://localhost:2002/workspaces/new> | Create a workspace                  |
| <http://localhost:2002/workspaces/[slug]> | The design studio for one SKU    |
| <http://localhost:2002/settings>     | Local dashboard (providers, secrets)  |
| <http://127.0.0.1:8002/docs>         | Swagger UI for the API                |

---

## Documentation

- **[USER_GUIDE.md](docs/USER_GUIDE.md)** — the day-to-day workflow,
  from brief to print-ready PSD.
- **[DESIGN_GUIDE.md](docs/DESIGN_GUIDE.md)** — colour, type, layout
  rules; what to use when extending the UI.
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** — why modules, why frozen
  specs, why audit-on-disk.
- **[SETUP_KEYS.md](docs/SETUP_KEYS.md)** — where to get OpenAI and
  Vectorizer.AI credentials.

---

## Stack

| Layer       | Tech                                              |
| ----------- | ------------------------------------------------- |
| Backend     | FastAPI · SQLModel · SQLite · uv · structlog      |
| Frontend    | Next.js 14 App Router · Tailwind v3 · TypeScript  |
| AI gen      | OpenAI **gpt-image-2** (SSE partial-image stream) |
| Vision      | GPT-4o-mini (element discovery for composable PSD)|
| OCR         | Tesseract (local) — runs inside Make print-ready  |
| Vector      | Vectorizer.AI (primary) · Inkscape Potrace (free) |
| PSD writer  | psd-tools 1.17 + Pillow                           |
| CDR writer  | CloudConvert · UniConvertor 2 (sK1, local)        |
| Storage     | Local filesystem (`./workspaces/<slug>/...`)      |

Backend port: **8002** · Frontend port: **2002**.

---

## What ships today

### Slice 1 — Foundation
- `Workspace` + `AuditEvent` SQLModel rows with frozen print specs
- Filesystem layout (`<slug>/{references,generations,exports}/`)
- 5 packaging presets (lotion bottle, cream jar, cream box, serum
  dropper, shampoo pouch)
- Next.js shell: landing, workspace list, create form, detail page
- Capability strip in header showing the live provider routing

### Slice 2 — gpt-image-2 inside a workspace
- `Asset(kind="generation")` rows with exact cost from usage tokens +
  optional markup
- Two endpoints: `/generate` (non-stream JSON) and `/generate/stream`
  (SSE: partial frames → asset → cost)
- Per-variant audit (`asset.generated`)
- **Design Studio** UI: brief textarea, 1–4 variant chips, quality
  picker, live partial-frame previews, elapsed/expected progress strip,
  cost display, persistent gallery

### Slice 3 — References + edit-loop (up to 16 refs)
- `Asset(kind="reference")` + multi-image upload (≤ 16 files, ≤ 25 MB
  each), Pillow normalize → RGBA PNG, EXIF baked, 3840 px cap
- `/edit/stream` calls `images.edit` with `[base, …refs]`; audit
  `asset.edited` captures the edit chain
- **Generate-with-references** — when refs are selected in generate
  mode, Forme routes through `images.edit` so the model sees them
- UI: **ReferencesPanel** (drag-drop + click-to-select), **Edit**
  button on every variant tile and gallery card, edit-mode banner +
  read-only "Original brief" caption, 16-ref cap enforced client-side

### Slice 10 — Unified Make print-ready pipeline
- **One headline action per variant: "Make print-ready"**. Opens a
  dialog that walks the user through: Analyse → Review → Assemble →
  Export cascade. There are no separate tier menus.
- **Analyse** (`app/services/analyze.py`) runs Vision (GPT-4o-mini) for
  graphic elements and Tesseract OCR for text in one pass, then merges
  + sorts top-to-bottom into a unified element manifest.
- **Review** lets the designer edit prompts on graphic elements, edit
  text content on OCR-discovered text rows (with low-confidence chips
  on garbled OCR), reposition, add or remove elements.
- **Assemble** (`app/services/compose.py`):
  - Each graphic element → fresh gpt-image-2 render with
    `background="transparent"`
  - Each text element → Pillow renders the confirmed string in a clean
    font; layer name carries a `[type:"<text>"]` hint so a future
    Photoshop script can convert pixel → type
  - Line-art elements (logos, wordmarks, ornaments) auto-vectorize via
    Vectorizer.AI; photo illustrations stay raster
  - Layered CMYK PSD assembled at 300 DPI with bleed
- **Export cascade** — the same per-element generations also feed a
  composable SVG (each vectorized element as `<g>` + each raster as
  `<image>`), the PDF/X-4, and CDR (via the SVG). One run, every
  format.
- Audit: `export.psd.composable.created` + `export.svg.composable.created`
  capture the element count, vector / raster split, and total cost.

### Workspace `design_mode` toggle (slice 10d / 10e)
Two entry points for the same pipeline:
- **OFF (default) — "I have the design"**. Upload a finished label,
  the studio runs Make print-ready straight away.
- **ON — "I have the bottle"**. Upload a plain product photo +
  reference + brief. Generate label variants shown on the product;
  iterate; hit **Approve & flatten** to drop the design into a clean
  rectangular sticker that feeds the same Make-print-ready flow.

### Slice 5 — Print PDF/X-4
- **`app/services/export_pdf.py`** — ReportLab-based PDF/X-4 generator:
  - sRGB → CMYK conversion via `PIL.ImageCms` using the configured ICC profile (defaults to macOS Generic CMYK; swap for ISO Coated v2 in Settings)
  - MediaBox / **TrimBox** / **BleedBox** all set per the workspace's frozen specs
  - 5 mm corner **trim marks** + bullseye **registration marks** in CMYK process-black
  - ICC profile embedded as a PDF/X `GTS_PDFX` **OutputIntent** (extends ReportLab's PDFCatalog to allow this attribute at module import)
  - Mirror-bleed extension on the rasterised CMYK image so the embedded PNG fills the media box without distorting the trim region's aspect.
- `POST /workspaces/{slug}/exports/pdf` — body `{source_asset_id, dpi, trim_marks, registration_marks}`
- `Asset(kind="export")` + `export.pdf.created` audit row with the full print spec snapshot
- UI: dark **PDF** button on every variant + gallery card (next to the PSD split control); Exports table now distinguishes PSD / PDF / OCR JSON by icon + tone
- Settings: new **Print PDF/X-4** card with ICC path + name + green/amber present indicator

### Slice 6 — Vector exports (PNG → SVG)
- **`app/services/vector.py`** — provider dispatcher with the same non-auto-fallback contract Forme uses everywhere:
  - `vectorizer_ai` — POSTs the PNG to `https://vectorizer.ai/api/v1/vectorize` (HTTP-Basic with `VECTORIZER_AI_API_ID` / `_KEY`). Honours `FORME_VECTORIZER_AI_MODE` (`production` · `test` · `preview`).
  - `inkscape_potrace` — shells out to `inkscape --actions="select-all;trace-bitmap;export-filename:…;export-do;FileQuit"` (Inkscape 1.2+).
  - Errors surface as 502/503/504 with the upstream message in `detail` — the UI shows them and offers a "Try with <fallback>?" button; the backend **never** retries the alternate provider automatically.
- `POST /workspaces/{slug}/exports/vector` — body `{source_asset_id, provider?}`. `provider` is the explicit override the UI passes when the user clicks the fallback button.
- `Asset(kind="export", mime="image/svg+xml")` + `export.vector.created` audit row carrying the chosen provider + mode.
- UI: clay-coloured **SVG** button (icon: `Shapes`) on every variant tile and gallery card. On vector-export failure, the Exports section renders the upstream error plus a one-click *"Try with Vectorizer.AI"* / *"Try with Inkscape Potrace"* affordance — exactly one alternate, never auto-invoked. Exports list distinguishes SVG by colour + icon.
- Settings: Provider routing card now exposes Vectorizer.AI **mode** (`production` / `test` / `preview`) and a shared vector-timeout field; per-stage availability hints flag missing creds (e.g. "no key") and missing tools (e.g. "not installed").

### Slice 7 — CDR export (CorelDRAW)
- **`app/services/export_cdr.py`** — dispatcher mirroring slice 6:
  - `cloudconvert` — paid, hosted. `POST /v2/jobs` builds an import→convert→export pipeline, then we poll until `export-cdr` finishes and download the resulting `.cdr`.
  - `uniconvertor` — free, local. Shells out to `uniconvertor input.svg output.cdr` (sK1 Project's UniConvertor 2).
  - **Honest note**: the original roadmap said "CDR via Inkscape CLI" — that was wishful. Inkscape's CLI can import `.cdr` but cannot export it. UniConvertor or CloudConvert are the only real paths.
- `POST /workspaces/{slug}/exports/cdr` — body `{source_asset_id, vector_provider?, cdr_provider?}`. Orchestrates PNG → SVG (slice 6) → CDR (slice 7). Both stages have independent non-auto fallback.
- `Asset(kind="export", mime="application/x-cdr")` + `export.cdr.created` audit row capturing both provider names and both sizes.
- UI: sage-toned **CDR** button (icon: `Box`) next to the SVG button. Failure surface shows *"Retry CDR via CloudConvert / UniConvertor (local)"* — non-auto fallback identical to vector. Exports table distinguishes CDR by colour + icon + badge.
- Settings: new **CDR export** card with provider routing, UniConvertor path (with green/amber present indicator), CDR timeout, and `CLOUDCONVERT_API_KEY` in the redacted credentials list.

### Configurable provider architecture
- `FORME_VECTORIZER_PROVIDER` / `_FALLBACK` — Vectorizer.AI ↔ Inkscape
  Potrace. Fallback is **never** auto-invoked.
- `FORME_CDR_PROVIDER` / `_FALLBACK` — CloudConvert ↔ UniConvertor.
  Same non-auto-fallback discipline.
- `/settings` dashboard: read every env-derived setting (secrets
  redacted to last 4 chars), toggle non-secret fields, writes back to
  `backend/.env` and triggers worker reload.

---

## Provider matrix

| Stage          | Paid primary       | Free / alt                       | Status                          |
| -------------- | ------------------ | -------------------------------- | ------------------------------- |
| Image gen/edit | gpt-image-2 (only) | —                                | live ✓                          |
| Vision         | GPT-4o-mini        | —                                | live ✓ (analyze pass)           |
| OCR            | Tesseract (local)  | —                                | live ✓ — auto when binary found |
| Print PDF/X-4  | ReportLab + ICC    | —                                | live ✓                          |
| Vector         | Vectorizer.AI      | Inkscape Potrace                 | live ✓ (per-element auto-vec)   |
| PSD            | psd-tools (local)  | —                                | Composable (multi-layer)        |
| CDR            | CloudConvert       | UniConvertor (sK1, local)        | live ✓                          |

---

## Folder layout

```
forme-studio/
├── backend/                 FastAPI + uv + SQLite
│   ├── .env                 ← all credentials + provider routing
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── deps.py
│   │   ├── models/          ← Workspace · AuditEvent · Asset
│   │   ├── services/        ← filesystem · audit · pricing · ocr
│   │   │                       openai_image · analyze · export_pdf
│   │   │                       export_cdr · vector · vision · compose
│   │   │                       image_normalize · assets
│   │   ├── modules/
│   │   │   └── packaging/   ← routes · schemas · presets
│   │   └── routes/          ← health · settings
│   └── tests/               ← 155 pytests (hermetic, stubbed OpenAI)
├── frontend/                Next.js 14
│   ├── app/
│   │   ├── page.tsx                       Landing
│   │   ├── workspaces/
│   │   │   ├── page.tsx                   List
│   │   │   ├── new/page.tsx               Create
│   │   │   └── [slug]/page.tsx            Design Studio
│   │   └── settings/page.tsx              Local dashboard
│   ├── components/
│   │   ├── DesignStudio.tsx
│   │   ├── ReferencesPanel.tsx
│   │   ├── SettingsForm.tsx
│   │   ├── AppShell.tsx
│   │   ├── WorkspaceCard.tsx
│   │   ├── CreateWorkspaceForm.tsx
│   │   ├── Logo.tsx
│   │   └── ui/              ← Button · Card · Field · Badge
│   └── lib/
│       ├── api.ts           ← typed backend client
│       └── sse.ts           ← POST-bodied SSE consumer
├── workspaces/              ← per-product workspaces (gitignored)
│   └── <slug>/
│       ├── brief.md
│       ├── references/
│       ├── generations/
│       ├── exports/
│       └── audit.log.jsonl
├── forme.db                 ← SQLite (canonical state)
└── docs/                    ← USER_GUIDE · DESIGN_GUIDE · ARCHITECTURE · SETUP_KEYS
```

---

## Verification matrix

| Check          | Command                                  | Result   |
| -------------- | ---------------------------------------- | -------- |
| Lint           | `uv run ruff check app/ tests/`         | clean    |
| Type check     | `uv run mypy app/ --strict`             | clean    |
| Tests          | `uv run pytest -q`                      | 155/155  |
| Frontend types | `pnpm typecheck`                        | clean    |

Pilot is **production-ready** against real gpt-image-2 calls.

---

## Roadmap

- ✅ **Slice 5** — Print PDF/X-4 with CMYK ICC + bleed/trim/registration marks
- ✅ **Slice 6** — Vector exports (Vectorizer.AI primary + Inkscape Potrace fallback, non-auto fallback)
- ✅ **Slice 7** — CDR exports (CloudConvert paid + UniConvertor local, non-auto fallback)
- ✅ **Slice 8** — Composable PSD (multi-layered editable export, per-element regeneration)
- ✅ **Slice 10** — Unified Make-print-ready pipeline (Vision + OCR merge, Pillow text rendering, selective auto-vectorization, design_mode toggle, single CTA)
- **Module #2** — Apparel (tee, hoodie, packaging-adjacent print)
