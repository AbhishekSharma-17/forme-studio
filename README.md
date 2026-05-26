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
- **[SETUP_KEYS.md](docs/SETUP_KEYS.md)** — where to get OpenAI,
  Replicate, Vectorizer.AI credentials.

---

## Stack

| Layer       | Tech                                              |
| ----------- | ------------------------------------------------- |
| Backend     | FastAPI · SQLModel · SQLite · uv · structlog      |
| Frontend    | Next.js 14 App Router · Tailwind v3 · TypeScript  |
| AI gen      | OpenAI **gpt-image-2** (SSE partial-image stream) |
| Segmentation| Replicate SAM-2 *or* your own self-hosted DGX     |
| Vector      | Vectorizer.AI (primary) · Inkscape Potrace (free) |
| PSD writer  | psd-tools 1.17 + Pillow                           |
| CDR writer  | Inkscape CLI (post-MVP)                           |
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

### Slice 4 — Tier A flat PSD export
- `app/services/export_psd.py`: PNG → CMYK (Pillow baseline) → PSD via
  `psd-tools`, 300 DPI baked into the PSD Resolution Info image resource
- `POST /workspaces/{slug}/exports/psd` — body
  `{source_asset_id, tier, color_space: CMYK|RGB, dpi}`
- `Asset(kind="export")` + `export.psd.tier_<x>.created` audit per tier
- UI: **PSD ▾** split-button: one click for Tier A flat, dropdown for B/C

### Slice 4.5 — Tier B + Tier C PSD
- **Tier B (layered)** — SAM-2 segmentation → per-mask alpha overlay
  layers on top of the base. Designers toggle visibility in Photoshop.
- **Tier C (editable text)** — Tier B + Tesseract OCR per-word overlays
  whose layer names encode the detected text. Also writes a sidecar
  `<filename>.ocr.json` listing every region with bbox + confidence.
- **Segmentation providers** (configurable, non-auto fallback):
  - `replicate` — meta/sam-2 (default), uses `REPLICATE_API_TOKEN`
  - `self_hosted` — your DGX Spark; multipart contract documented in
    `app/services/segmentation.py`
- **OCR**: local Tesseract via `pytesseract` (`brew install tesseract`).
  Configurable command path + languages. Tier C also requires
  `FORME_TIER_C_ENABLED=true`.
- UI: PSD button is a split with a dropdown — Tier A always available,
  Tier B/C grey out when their providers aren't ready, hint copy points
  to Settings for the fix.

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
- **`app/services/vector.py`** — dispatcher with the same non-auto-fallback contract as `segmentation.py`:
  - `vectorizer_ai` — POSTs the PNG to `https://vectorizer.ai/api/v1/vectorize` (HTTP-Basic with `VECTORIZER_AI_API_ID` / `_KEY`). Honours `FORME_VECTORIZER_AI_MODE` (`production` · `test` · `preview`).
  - `inkscape_potrace` — shells out to `inkscape --actions="select-all;trace-bitmap;export-filename:…;export-do;FileQuit"` (Inkscape 1.2+).
  - Errors surface as 502/503/504 with the upstream message in `detail` — the UI shows them and offers a "Try with <fallback>?" button; the backend **never** retries the alternate provider automatically.
- `POST /workspaces/{slug}/exports/vector` — body `{source_asset_id, provider?}`. `provider` is the explicit override the UI passes when the user clicks the fallback button.
- `Asset(kind="export", mime="image/svg+xml")` + `export.vector.created` audit row carrying the chosen provider + mode.
- UI: clay-coloured **SVG** button (icon: `Shapes`) on every variant tile and gallery card. On vector-export failure, the Exports section renders the upstream error plus a one-click *"Try with Vectorizer.AI"* / *"Try with Inkscape Potrace"* affordance — exactly one alternate, never auto-invoked. Exports list distinguishes SVG by colour + icon.
- Settings: Provider routing card now exposes Vectorizer.AI **mode** (`production` / `test` / `preview`) and a shared vector-timeout field; per-stage availability hints flag missing creds (e.g. "no key") and missing tools (e.g. "not installed").

### Slice 6.5 — SAM 3.1 self-hosted segmentation
- New `sam3` segmentation provider — image-only, self-hosted (your DGX Spark). Default stays Replicate SAM-2; flip in Settings → Provider routing → Segmentation provider once your endpoint is up.
- New env vars: `FORME_SAM3_ENDPOINT_URL`, `FORME_SAM3_ENDPOINT_TOKEN`, optional `FORME_SAM3_TEXT_PROMPT` for concept-based segmentation.
- **Semantic Tier B PSDs** — when SAM 3.1 is text-prompted, masks come back with `label` ("logo", "bottle", …) and `score`. Forme propagates the label to the PSD layer name (with `_1` / `_2` disambiguation for duplicates) so designers see real names in Photoshop instead of `sam2_layer_01`. AMG mode without a prompt still works and falls back to anonymous `sam3_layer_NN` names.
- Wire contract for the user's DGX endpoint is documented in [`docs/SAM_UPGRADE.md`](docs/SAM_UPGRADE.md) — same multipart POST shape as the existing self-hosted slot, with optional `score` + `label` per mask.
- Audit: `export.psd.tier_b.created` payload now carries `mask_labels` when SAM 3.x returned them.

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
- `FORME_SEGMENTATION_PROVIDER` — `replicate` / `self_hosted` / `none`.
  Self-hosted slot reserved for the DGX Spark deployment.
- `/settings` dashboard: read every env-derived setting (secrets
  redacted to last 4 chars), toggle non-secret fields, writes back to
  `backend/.env` and triggers worker reload.

---

## Provider matrix

| Stage          | Paid primary       | Free / alt                       | Status                |
| -------------- | ------------------ | -------------------------------- | --------------------- |
| Image gen/edit | gpt-image-2 (only) | —                                | live ✓                |
| Segmentation   | Replicate SAM-2    | self-hosted (SAM-2 or SAM 3.1)   | live ✓ (Tier B PSD)   |
| OCR (Tier C)   | Tesseract (local)  | —                                | live ✓ — toggle on    |
| Print PDF/X-4  | ReportLab + ICC    | —                                | live ✓                |
| Vector         | Vectorizer.AI      | Inkscape Potrace                 | live ✓ (slice 6)      |
| PSD            | psd-tools (local)  | —                                | Tier A · B · C live ✓ |
| CDR            | CloudConvert       | UniConvertor (sK1, local)        | live ✓ (slice 7)      |

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
│   │   │                       openai_image · export_psd · export_pdf
│   │   │                       export_cdr · vector · segmentation
│   │   │                       image_normalize · assets
│   │   ├── modules/
│   │   │   └── packaging/   ← routes · schemas · presets
│   │   └── routes/          ← health · settings
│   └── tests/               ← 77 pytests (hermetic, stubbed OpenAI)
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

| Check          | Command                                  | Result |
| -------------- | ---------------------------------------- | ------ |
| Lint           | `uv run ruff check app/ tests/`         | clean  |
| Type check     | `uv run mypy app/ --strict`             | clean  |
| Tests          | `uv run pytest -q`                      | 77/77  |
| Frontend types | `pnpm typecheck`                        | clean  |

Pilot is **production-ready** for slices 1–4 against real gpt-image-2
calls.

---

## Roadmap

- ✅ **Slice 4.5** — Tier B (SAM-2) + Tier C (OCR) PSD exports
- ✅ **Slice 5** — Print PDF/X-4 with CMYK ICC + bleed/trim/registration marks
- ✅ **Slice 6** — Vector exports (Vectorizer.AI primary + Inkscape Potrace fallback, non-auto fallback)
- ✅ **Slice 6.5** — SAM 3.1 self-hosted segmentation (semantic Tier B PSD layer names)
- ✅ **Slice 7** — CDR exports (CloudConvert paid + UniConvertor local, non-auto fallback)
- **Module #2** — Apparel (tee, hoodie, packaging-adjacent print)
