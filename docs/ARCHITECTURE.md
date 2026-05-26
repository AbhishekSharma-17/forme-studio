# Forme Studio — Architecture

Status: live through slice 4 (Tier A PSD export). This is the
source-of-truth for *why* the system is shaped the way it is.

---

## 1. North star

> **A craft tool**, not a generator. The user iterates with intent —
> Forme tracks every iteration, freezes every spec, and produces
> print-ready files at the end. Nothing happens behind the user's back.

Concrete consequences:

- **Specs freeze at workspace creation.** Preset edits never retroactively
  change an old workspace.
- **No automatic fallbacks.** If the primary vector provider fails, the
  UI surfaces the error and lets the user pick the fallback. Forme
  doesn't silently retry on a different model that may behave
  differently.
- **Full audit trail.** Every state-changing action lands in the DB
  *and* a JSONL mirror in the workspace folder.

---

## 2. Module structure

Forme starts with packaging but won't end there. Apparel, signage, and
merch will each have their own specs, dielines and product types. To
keep the codebase from rotting into spaghetti when module #2 lands,
every product family is a **self-contained vertical**:

```
backend/app/
├── routes/                    ← cross-module: health, settings
├── services/                  ← cross-module: openai_image, audit,
│                                 pricing, filesystem, image_normalize,
│                                 export_psd, assets
└── modules/
    └── packaging/             ← module #1
        ├── routes.py          /api/packaging/...
        ├── schemas.py
        └── presets.py
    └── apparel/               (future)
    └── signage/               (future)
```

Each module exposes a single `router` object that `main.py` mounts.
**The main file is the only place that knows the full module list.**

The frontend mirrors the structure — when module #2 lands, pages move
under `app/(modules)/packaging/...` and a sibling `apparel/` group.
Today (single module) the routes live at `app/workspaces/...` for less
nesting.

---

## 3. Workspace = product / SKU

One `Workspace` row in SQLite captures:

| Field          | Why                                                            |
| -------------- | -------------------------------------------------------------- |
| `slug`         | URL segment + on-disk folder name. Unique, derived from `name`. |
| `module`       | Which module owns it (`packaging` today).                      |
| `product_type` | Which preset within the module (`lotion_bottle_label` …).      |
| `specs`        | JSON: `trim_mm`, `bleed_mm`, `dpi`, `color_space`, `generation_size`, preset notes. **Frozen snapshot of the preset at creation time.** |

Frozen specs decouple workspaces from later preset edits. If we add a
"2024 Q3" variant of the lotion-bottle preset with different trim,
existing workspaces keep their original specs and stay byte-identical
in their audit chain.

---

## 4. The asset hierarchy

A single `Asset` table covers four kinds:

| `kind`        | Created when                                | Folder            |
| ------------- | ------------------------------------------- | ----------------- |
| `reference`   | User uploads an image                        | `references/`     |
| `generation`  | gpt-image-2 produces a variant               | `generations/`    |
| `export`      | An export pipeline writes a deliverable      | `exports/`        |

The `relative_path` field is stored relative to the workspace root, so a
workspace folder can be moved or copied without re-writing DB rows.

Cost fields (`provider_cost_usd`, `user_cost_usd`, `usage`) live on
the asset row itself, not in a separate billing table — every asset is
self-describing.

---

## 5. Audit trail

```
session → audit.record(event="…") →
    INSERT audit_events (SQLite)        ← canonical
    + append JSONL line to                ← mirror, travels with the workspace folder
      workspaces/<slug>/audit.log.jsonl
```

Event names use dotted notation:

- `workspace.created`
- `reference.uploaded`
- `asset.generated`         (with `references: [id…]` when refs were used)
- `asset.edited`            (with `edit_of: id`, `references: [id…]`)
- `export.psd.created`      (with `source_asset_id`, `color_space`, `dpi`, `tier`)

Atomicity caveat (documented in `services/audit.py`): the JSONL write
happens before the session commit. In slice 1–4 this is theoretical
because the only realistic commit failure is the SQLite UNIQUE on slug,
which is pre-checked. Slice 5+ will move the JSONL write to *after*
commit (or wrap in try/unlink) once we add network-bound services that
can fail post-flush.

---

## 6. Streaming flow

```
POST /api/packaging/workspaces/{slug}/generate/stream
     ─────────────────────────────────────────────────
     body: { prompt, n, quality, reference_asset_ids? }

     ┌─ if reference_asset_ids:
     │     edit_stream(images=[refs…])
     │
     └─ else:
           generate_stream()

           ── async iterator ──
       events:
         partial      { variant_index, image_b64 }      ← blurred preview
         completed    { variant_index, image_b64, usage } ← final frame
         (route reads `completed`, persists asset,
          writes asset.generated audit row, then emits)
         asset        { variant_index, asset: AssetOut }   ← to client
         cost         { provider_cost_usd, user_cost_usd,
                        markup_percent, usage, asset_ids,
                        references }
         done         {}
```

Errors mid-stream (content moderation, network) emit a single `error`
event and the client treats it as terminal.

The same machinery serves the **edit** endpoint:

```
POST /api/packaging/workspaces/{slug}/edit/stream
     body: { prompt, base_asset_id, reference_asset_ids?, n, quality }
```

The route loads `[base, …refs]` from disk, packs them as OpenAI
`(filename, bytes, mime)` tuples, and calls `edit_stream` — same event
loop, same SSE format. An extra `asset.edited` audit row captures the
edit chain.

---

## 7. Provider routing (no auto fallback)

Two stages have configurable providers — vector and CDR:

```
backend/.env:

  FORME_VECTORIZER_PROVIDER=vectorizer_ai   # paid; best for photos
  FORME_VECTORIZER_FALLBACK=inkscape_potrace  # free local; user opts in on error

  FORME_CDR_PROVIDER=cloudconvert           # paid; reliable
  FORME_CDR_FALLBACK=uniconvertor           # free, local
```

`/api/health` exposes both the *capability flags* (whether a provider's
credentials are present) and the *selected* providers. The frontend
header strip surfaces both — the user can see at a glance whether the
configured primary is reachable, and what the fallback would be.

**Why no auto fallback?** Different providers produce subtly different
output. Silently switching mid-job would surprise the designer with
output that doesn't match the rest of the workspace. Forme shows the
error + an explicit "Try with the fallback?" button.

---

## 8. Settings dashboard

`/api/settings` GET/PATCH lives at `app/routes/settings.py`. PATCH only
accepts a **whitelist of non-secret keys** (`WRITABLE_KEYS`):

- Provider routing toggles
- Pricing markup percent
- Image model snapshot pin
- Image timeout
- Inkscape path
- Log level

Secrets (`OPENAI_API_KEY`, `REPLICATE_API_TOKEN`, etc.) are
**read-only** through the UI — they're displayed with the last 4
characters revealed for verification. Rotating one requires editing
`backend/.env` directly. uvicorn's `--reload-include '*.env'` flag
restarts the worker on save.

The PATCH writer is line-aware (`_KV_RE`) so comments and unrelated
keys in `.env` survive a save unchanged.

---

## 9. Cost calculation

```
usage tokens (from OpenAI)  →  usage_to_dict()  →  flat int dict
                                                     │
                                                     ▼
                                          cost_from_usage()
                                            │  text input        $5 / M
                                            │  image input       $8 / M
                                            │  cached input      $2 / M
                                            │  image output     $30 / M
                                            ▼
                                   provider_cost_usd
                                            │
                                   apply_markup(±%)
                                            ▼
                                     user_cost_usd
```

Each generation `Asset` stores both numbers — the audit JSONL travels
with the workspace, so the per-asset cost is reconstructible without
the DB.

---

## 10. Hot-reload boundaries

uvicorn is started with `--reload --reload-include '*.env'`:

| Change                                   | Reloads?         |
| ---------------------------------------- | ---------------- |
| Edit `.env`                              | Yes (worker restart) |
| Edit Python source                       | Yes              |
| Edit `forme.db` (manual fiddling)        | No (use the API) |
| Add a file under `workspaces/`           | No (data, not code) |

The settings PATCH handler also resets the `Settings()` cache in
`config.py` so the same request that wrote the env returns the new
values.

---

## 11. Test posture

- **Hermetic by default.** `tests/conftest.py` blanks every provider
  credential env var + points Inkscape at a non-existent path, so no
  test can leak to a live API.
- **Stubbed OpenAI client.** `tests/stubs.py` mocks `images.generate`
  (sync + stream) and `images.edit` (sync + stream). Records every call's
  kwargs for inspection.
- **Real SQLite + real filesystem per test.** Each test gets a tmpdir
  workspace root + SQLite file, so behaviour over the file/DB boundaries
  is verified end-to-end without mocks.
- 32 tests, all sub-second:
  - 1 health
  - 7 workspaces (CRUD + slug + 4xx paths)
  - 8 generate (incl. refs route through edit)
  - 7 references (single/multi/oversized/bad)
  - 4 edit/stream (SDK call shape + audit chain + cross-workspace + 404)
  - 5 PSD export (CMYK + RGB + bad-kind + bad-source + bad-color)

---

## 12. Unified Make-print-ready pipeline (slice 10)

The headline action per variant. One endpoint pair drives the whole
flow:

- `POST /workspaces/{slug}/compose/discover` → unified manifest
  (graphic elements from Vision + text elements from OCR, merged +
  sorted top-to-bottom)
- `POST /workspaces/{slug}/exports/psd-composable` → layered CMYK PSD
  + sibling vector SVG composite

### Phase 1 — Analyse

`app/services/analyze.py` orchestrates two independent passes in one
call:

```
            ┌───────────────────────────────────────────┐
input PNG → │ vision.discover_elements (GPT-4o-mini) ──┐│
            │                                          ▼│   merge +
            │ ocr.extract (Tesseract) ─────────────────┼─→ sort y,x →
            │                                          ▲│   manifest
            │                                          ││
            └──────────────────────────────────────────┘│
```

The OCR pass collapses per-word records into block-level entries via
``_merge_lines_into_blocks`` (lookback merge that respects gap-vs-
median-line-height and horizontal column overlap). Each block becomes
an ``ElementSpec(kind="text")`` with the OCR'd string + confidence so
the review UI can flag low-confidence text.

If Tesseract isn't installed, the analyzer returns graphic elements
only and the response carries ``ocr_available=false`` — the UI lets
the user add text blocks manually.

### Phase 2 — Review

The frontend dialog renders one row per element. Graphic rows let the
designer edit the prompt; text rows let them edit the actual string
content (with a yellow chip when OCR confidence < 75%). Any element
can be removed; missing ones can be added (graphic or text via two
"+" buttons).

### Phase 3 — Assemble

`app/services/compose.py` is the only PSD producer left in the system.
Per-element routing:

| `kind`                              | Renderer                  | Vectorized? |
| ----------------------------------- | ------------------------- | ----------- |
| `graphic`                           | gpt-image-2 transparent   | if `vectorizable=true` |
| `wordmark` / `ornament` / `seal`    | gpt-image-2 transparent   | always      |
| `headline`                          | gpt-image-2 transparent   | no          |
| `text`                              | Pillow text rasteriser    | no (already crisp) |
| `body_copy`                         | skipped                   | n/a         |

Text-element rendering (`_render_text_element`): builds a transparent
RGBA canvas at the element's `size_px`, walks a font-discovery chain
(bundled DejaVu → system Helvetica → Linux DejaVu → Pillow default),
binary-searches the font size to fit the longest line within 95% of
the canvas width, centres the text, rasterises.

Selective auto-vectorization (`maybe_vectorize_element`): if
`_should_vectorize(spec)` returns true, the per-element PNG goes
through `app.services.vector.vectorize`. Failures degrade gracefully
to raster — never abort the assemble.

The PSD assembly composes elements as `PixelLayer`s at `position_mm
× dpi` offsets relative to the trim+bleed canvas. Text layer names
carry the `[type:"<actual text>"]` suffix so a Photoshop-script pass
can later auto-convert pixel → type.

The SVG assembly (`assemble_composable_svg`) emits a `<svg
viewBox="0 0 W_mm H_mm">` document where each element is either:

- An inline `<g transform="translate(x,y) scale(...)">` containing
  the per-element SVG paths (vectorized elements), or
- An `<image href="data:image/png;base64,...">` at the element's
  mm bounding box (raster elements).

Mixing vector + raster gives clean lines on logos and ornaments while
preserving photographic fidelity on illustrations — exactly the trade
the user agreed to in slice 10 planning.

### Phase 4 — Export cascade

The Composable endpoint writes both the PSD (master) and the SVG
sibling, each as their own `Asset(kind="export")` rows. PDF/X-4 and
CDR exports still operate on the original generation PNG via their
own dedicated endpoints (one-click buttons on each variant); they
intentionally don't go through Make print-ready because they're
quick raster exports, not high-quality multi-layer assemblies.

Audit shape: `export.psd.composable.created` carries the element
count, layer count, total generation cost, vectorization cost, and
the full per-element manifest. `export.svg.composable.created` is
written as a sibling event with vector/raster counts.

### Workspace `design_mode` toggle (slice 10d / 10e)

A boolean on `Workspace` chooses the entry framing:

- **False (default)** — "Analyze-existing" mode. User uploads a
  finished label; Make print-ready runs straight on it.
- **True** — "Design-on-product" mode. User uploads a plain product
  photo + style references + brief; the studio runs an initial Design
  Round generating label variants shown on the product. Once a
  variant is approved, `POST /workspaces/{slug}/design/flatten`
  re-runs gpt-image-2 with a fixed "render flat, no bottle, no
  perspective" prompt — output is a clean rectangular sticker that
  feeds the analyze pass.

The toggle is mutable post-creation via
`PATCH /workspaces/{slug}` — the studio can flip modes mid-flight.

## 13. Print PDF/X-4

Endpoint: `POST /api/packaging/workspaces/{slug}/exports/pdf`, body
`{source_asset_id, dpi, trim_marks, registration_marks}`.

The pipeline:

```
generation PNG
    │
    ▼
PIL.ImageCms.applyTransform(sRGB → ICC CMYK profile)
    │              ↓ (falls back to PIL .convert("CMYK") if ICC missing)
    ▼
JPEG @ 92% / 300 DPI    ← ReportLab `drawImage`
    │
    ▼
ReportLab Canvas (PDF 1.6+)
    ├── setTrimBox  (bleed_pt, bleed_pt, bleed+trim_w, bleed+trim_h)
    ├── setBleedBox (0, 0, media_w, media_h)
    ├── corner trim marks   (5 mm, 0.25 pt, 100% K)
    ├── registration marks  (bullseye, 2.5 mm radius, 0.4 pt)
    └── Catalog.OutputIntents = [{
            S: /GTS_PDFX,
            OutputConditionIdentifier: <icc name>,
            DestOutputProfile: <embedded ICC stream>,
        }]
    │
    ▼
file on disk + Asset(kind="export") + export.pdf.created audit
```

**The PDFCatalog patch**. ReportLab's `PDFCatalog` class has a fixed
`__NoDefault__` whitelist of attributes it'll emit. `OutputIntents`
isn't on it. We append the attribute at module import in
`export_pdf.py` — idempotent across reloads, scoped to one class
attribute.

**Why we don't claim strict PDF/X conformance**: full PDF/X-4 requires
specific XMP metadata blocks (`pdfx:GTS_PDFXVersion` etc.) that
ReportLab doesn't emit out of the box. Forme produces a
PDF/X-4-*compatible* file — accepted by every press we've tested,
carries OutputIntent + TrimBox + BleedBox correctly — without the
zero-conformance markers. A slice-5.5 polish can add them via
`pikepdf` post-processing if a specific press complains.

## 14. Slice 6 — Vector exports (PNG → SVG)

**Why a dispatcher** — Vector tracing has two very different cost /
quality profiles. Vectorizer.AI is the press-quality option, multi-colour
preserving and remote (1 credit/image); Inkscape's bundled potrace runs
locally and is free, but only emits a monochrome silhouette. The same
PNG should be trivially routable to either, and the choice is per-call,
not per-install.

`app/services/vector.py` implements Forme's standard provider-dispatcher
pattern:

- `vectorize(png_bytes, provider=None)` resolves the chosen provider
  (env default or per-call override), validates the credentials / binary
  for that path, and dispatches.
- Each provider helper raises `HTTPException` with a precise status:
  - 503 if credentials are missing or the local binary isn't installed
  - 502 for upstream HTTP errors and non-zero subprocess exits
  - 504 for `httpx.TimeoutException` and `asyncio.TimeoutError`
  - 400 for unknown provider names
- A `VectorResult` dataclass carries the SVG bytes, the resolved provider
  label, the vectorizer.ai *mode* if applicable, and size in bytes.

**Why non-auto fallback (revisited).** The dispatcher will never invoke
the configured fallback by itself; failures bubble up so the API caller
(the studio UI) can show the actual upstream error and let the user
choose to retry against the alternate provider. The CDR dispatcher uses
the same contract — see `_resolve_asset` →
`run_vectorize` → `save_export` in `routes.py`. The frontend stores a
single `vectorRetry` state slot containing the asset id + the alternate
provider name; clicking *"Try with <fallback>"* simply calls
`POST /exports/vector` again with that explicit provider, which the
backend treats no differently from a first attempt.

**Why the Vectorizer.AI mode toggle.** Production runs cost real credits;
exercising the UI flow during development should not. The
`FORME_VECTORIZER_AI_MODE` toggle (`production` / `test` / `preview`) is
exposed in Settings so a dev can flip to `test` mode, get a watermarked
SVG back at 0.1 credit, and verify the audit row / Exports table / retry
flow without burning balance.

**Why Inkscape over a direct potrace binding.** Inkscape's bundled
potrace integration handles colour reduction, smoothing, and SVG packing
in one CLI invocation: `inkscape --actions="select-all;trace-bitmap;
export-filename:<out>;export-do;FileQuit" <in.png>`. Doing the same with
the raw `potrace` binary would require shelling out to ImageMagick for
the PBM conversion first; using Inkscape keeps us at one shell-out per
export and reuses the binary we already need for the upcoming CDR slice.

**Audit shape.** `export.vector.created` records:

```jsonc
{
  "asset_id":          42,
  "source_asset_id":   17,
  "relative_path":     "exports/asset17_20260101-090000-000000.svg",
  "mime_type":         "image/svg+xml",
  "size_bytes":        18432,
  "provider":          "vectorizer_ai",
  "mode":              "production"  // null for inkscape_potrace
}
```

That's enough to reconstruct *what* was traced, *how*, and *what it
cost* (the mode determines vectorizer.ai's billing) from the JSONL
without touching SQLite.


## 16. Slice 7 — CDR export

**Honest scoping note.** The slice was originally specced as "CDR via
Inkscape CLI". On verification, Inkscape can *import* CDR (via libcdr)
but **cannot export** it — there's no `--export-type=cdr` flag in any
shipping Inkscape version. The two viable paths are:

* **UniConvertor 2** (sK1 Project) — open-source CLI, free, runs
  locally. Default in Forme. Unmaintained since 2019-ish but binaries
  still work on macOS/Linux and produce CorelDRAW X4-compatible output
  that modern CorelDRAW versions still open cleanly.
* **CloudConvert** — paid hosted API. Reliable, supports newer CDR
  flavours.

We ship both with the same non-auto-fallback discipline as slices 4.5,
6, and 6.5. UniConvertor is default because it matches the "single-tenant
local tool" ethos — zero cloud round-trips when the binary's there.

**Two-stage orchestration.** The CDR endpoint is the first export that
chains two services internally:

```
PNG  →  app.services.vector.vectorize     (slice 6)  →  SVG bytes
SVG  →  app.services.export_cdr.convert_svg_to_cdr  (slice 7)  →  CDR bytes
```

Both stages have independent provider overrides on the same request
body: `{vector_provider?, cdr_provider?}`. The UI tracks two separate
retry slots (`vectorRetry` + `cdrRetry`) so a vector-stage failure shows
"Try with Inkscape Potrace" while a CDR-stage failure shows
"Retry CDR via CloudConvert" — never both at once unless the user
provoked both.

**CloudConvert plumbing.** The `/v2/jobs` flow is asynchronous:

1. `POST /v2/jobs` with three tasks: `import-svg` (upload),
   `convert-cdr`, `export-cdr` (return-url).
2. `POST` the SVG bytes to the upload form URL CloudConvert returned.
3. Poll `GET /v2/jobs/{id}` every 1.5s until `export-cdr` is
   `finished` *or* any task transitions to `error`.
4. `GET` the final file URL → CDR bytes.

Tests stub the whole chain via a stateful `_StubCCClient` that counts
poll iterations, so we can verify the "still processing on poll 1,
finished on poll 2" handshake without sleeping in CI.

**Audit shape.** `export.cdr.created` records:

```jsonc
{
  "asset_id":         91,
  "source_asset_id":  17,
  "relative_path":    "exports/asset17_20260101-090000-000000.cdr",
  "mime_type":        "application/x-cdr",
  "size_bytes":       42301,
  "vector_provider":  "vectorizer_ai",
  "vector_mode":      "production",
  "svg_size_bytes":   18430,
  "cdr_provider":     "uniconvertor",
  "cdr_size_bytes":   42301
}
```

— enough to fully reconstruct the two-stage pipeline from the JSONL.

## 17. Deferred (post-MVP)

- **Audit-row → asset diffing** so the workspace page can show "what
  changed" between iterations.
- **Per-workspace cost dashboard.** Today the gallery shows per-asset
  cost; a workspace-level rollup would tell pilot clients exactly what
  the SKU has cost so far.
- **PDF/X-4 strict conformance.** Today's PDF carries OutputIntent +
  TrimBox + BleedBox but not the XMP `pdfx:GTS_PDFXVersion` block. A
  `pikepdf` post-pass would close the conformance gap if a specific
  press demands it.
- **Per-element regeneration in Composable.** Right now if one element
  comes out wrong the user has to assemble again. A "regenerate this
  one element" button on the review dialog (already on disk as its own
  `Asset(kind="generation")`) would close the loop without paying for
  the whole batch.
