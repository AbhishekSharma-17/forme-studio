# SAM upgrade — going from SAM 2 to SAM 3.1

Forme Studio ships with **Replicate-hosted SAM 2** as the default
segmentation provider because it's the only first-party SAM Meta has
published on Replicate (as of May 2026). When you're ready to use
SAM 3.1 — for higher mask quality and, more importantly, **semantically
named layers** in Tier B PSDs — you have one path: self-host SAM 3.1
yourself and point Forme at it.

This document is the wire contract your self-hosted endpoint must
implement so Forme's `sam3` provider can talk to it.

---

## Why bother with SAM 3.1

| | SAM 2 (Replicate, today) | SAM 3.1 (self-hosted) |
| --- | --- | --- |
| Concept prompts | ❌ — anonymous masks only | ✅ — text prompts like `"logo, bottle"` |
| Per-mask confidence | ❌ | ✅ — `score` field |
| Layer naming | `sam2_layer_01`, `sam2_layer_02`, … | `logo`, `wordmark`, `bottle silhouette`, … |
| Cost | ~$0.01 / image (pay-per-call) | Free at the margin (your GPU power) |
| Latency | 6–15 s round-trip via Replicate | 1–3 s on a local DGX Spark |
| Hosting | Meta on Replicate | You, on your own box |

The killer feature for packaging design is **named layers**. A Tier B
PSD with layers called *logo* and *bottle silhouette* is dramatically
easier to swap and recompose than `sam2_layer_03` and `sam2_layer_07`.

---

## Wire contract

Your service must accept exactly this:

```
POST <FORME_SAM3_ENDPOINT_URL>
Headers:
  Authorization: Bearer <FORME_SAM3_ENDPOINT_TOKEN>      (optional)
Content-Type: multipart/form-data
Body:
  image       = <PNG bytes>                              (required)
  mode        = "auto" | "text"                          (optional, default "auto")
  text_prompt = "logo, wordmark, bottle"                 (required when mode="text")
```

and respond with status 200 + JSON:

```jsonc
{
  "width":  1024,                       // source image width in pixels
  "height": 1536,                       // source image height in pixels
  "model":  "sam3.1-image",             // informational, surfaces in /api/health
  "masks": [
    {
      "png_b64": "iVBORw0K…",           // single-channel PNG, base64
                                        //   255 = inside the mask
                                        //     0 = outside
                                        //   same dimensions as the source
      "bbox":    [x1, y1, x2, y2],      // PIL-style pixel coords
      "area_px": 12345,                 // non-zero-pixel count
      "score":   0.95,                  // optional, SAM 3 confidence
      "label":   "logo"                 // optional, only when text-prompted
    },
    …
  ]
}
```

### Field rules

- `png_b64` — **required**. Base64-encoded single-channel (L mode) PNG.
- `bbox` — required; integer pixel coords, `[left, top, right, bottom]`.
- `area_px` — required for sort ordering (Forme places larger regions
  on top in Tier B PSDs).
- `label` — optional. Present when the model is **text-prompted**;
  becomes the Tier B PSD layer name verbatim. Forme handles
  disambiguation for duplicates (`"logo" + "logo" → "logo_1" +
  "logo_2"`).
- `score` — optional. Float in `[0, 1]`. Recorded in the audit row but
  not currently shown in the UI.

### Errors

Forme treats anything other than `HTTP 200` with a JSON body as a
provider failure. Specifically:

- `HTTP 5xx` → Forme surfaces a 502 with your response body's first 300
  chars in `detail` (your error message becomes the user's error message).
- `httpx.TimeoutException` (no response in `FORME_SEGMENTATION_TIMEOUT_S`,
  default 180s) → Forme surfaces a 504.

Forme **never** auto-falls-back to SAM 2 / Replicate on failure. The
user explicitly picks a different provider from the Settings dashboard
after seeing the error. This is by design — fallbacks that surprise the
user are worse than failures that ask them to choose.

---

## A reference adapter (Python + FastAPI)

This is roughly the wrapper you'd run alongside Meta's `facebook/sam3`
checkpoint on your DGX Spark. The actual mask-tensor → PNG translation
depends on your batch shape — adjust the `_mask_tensor_to_png` helper.

```python
# adapter.py — minimum viable Forme-compatible SAM 3.1 server
from __future__ import annotations

import base64
import io
import os
from typing import Literal

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image
from sam3 import SAM3ImagePredictor, SAM3AutomaticMaskGenerator   # facebookresearch/sam3

app = FastAPI()
predictor = SAM3ImagePredictor.from_pretrained("facebook/sam3")
amg = SAM3AutomaticMaskGenerator(predictor.model)

@app.post("/sam3/image")
async def segment(
    image: UploadFile = File(...),
    mode: Literal["auto", "text"] = Form("auto"),
    text_prompt: str | None = Form(None),
) -> dict:
    pil = Image.open(io.BytesIO(await image.read())).convert("RGB")
    w, h = pil.size

    if mode == "text":
        if not text_prompt:
            raise HTTPException(400, "text mode needs text_prompt")
        concepts = [c.strip() for c in text_prompt.split(",") if c.strip()]
        results = predictor.predict(pil, text=concepts)  # returns dict per concept
        masks_out = _from_predicted(results, w, h, concepts)
    else:
        results = amg.generate(pil)  # AMG → list of dicts
        masks_out = _from_amg(results, w, h)

    return {"width": w, "height": h, "model": "sam3.1-image", "masks": masks_out}


def _mask_tensor_to_png(mask_bool: torch.Tensor) -> str:
    """SAM 3 returns torch.Tensor masks; turn one into a base64 PNG."""
    arr = (mask_bool.cpu().numpy() * 255).astype("uint8")
    im = Image.fromarray(arr, mode="L")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _from_predicted(results, w, h, concepts):
    out = []
    for concept, hits in zip(concepts, results, strict=True):
        for mask, box, score in zip(hits["masks"], hits["boxes"], hits["scores"]):
            x1, y1, x2, y2 = [int(v) for v in box.tolist()]
            out.append({
                "png_b64": _mask_tensor_to_png(mask),
                "bbox":    [x1, y1, x2, y2],
                "area_px": int(mask.sum().item()),
                "score":   float(score.item()),
                "label":   concept,
            })
    return out


def _from_amg(results, w, h):
    out = []
    for i, r in enumerate(results):
        x, y, bw, bh = r["bbox"]
        out.append({
            "png_b64": _mask_tensor_to_png(torch.from_numpy(r["segmentation"])),
            "bbox":    [int(x), int(y), int(x + bw), int(y + bh)],
            "area_px": int(r["area"]),
            "score":   float(r["predicted_iou"]),
        })
    return out
```

Run it with:

```bash
uvicorn adapter:app --host 0.0.0.0 --port 9000 \
    --workers 1   # SAM 3 holds the model in GPU memory — keep workers=1
```

Then in Forme's `backend/.env`:

```ini
FORME_SAM3_ENDPOINT_URL=http://<your-dgx-host>:9000/sam3/image
FORME_SAM3_ENDPOINT_TOKEN=                       # leave blank if no auth
FORME_SAM3_TEXT_PROMPT=logo, wordmark, bottle, label background
```

Open the Settings dashboard → **Provider routing** → set *Segmentation
provider* to **SAM 3.1 self-hosted**. Save. The capability dot in the
top-right turns green and Tier B PSDs now route through SAM 3.1.

---

## Migration checklist

1. Deploy `facebook/sam3` on your DGX Spark with the adapter above (or
   your own equivalent). Verify with `curl`:
   ```bash
   curl -F image=@sample.png -F mode=auto http://localhost:9000/sam3/image | jq '.masks | length'
   ```
2. Set `FORME_SAM3_ENDPOINT_URL` (and `FORME_SAM3_ENDPOINT_TOKEN` if you
   added auth) in `backend/.env`.
3. Optional: set `FORME_SAM3_TEXT_PROMPT` to a comma-separated list of
   concepts you usually want segmented. Leave blank for AMG mode.
4. In the dashboard, flip *Segmentation provider* → **SAM 3.1
   self-hosted**.
5. Run a Tier B PSD export on a real generation. Check the resulting
   PSD's layer panel — semantic names should appear.
6. Audit verification: `tail -1 workspaces/<slug>/audit.log.jsonl |
   jq '.payload'` should now show `mask_labels: ["logo", "bottle"]`.

---

## Reverting

The default stays SAM 2 / Replicate, so reverting is one click in the
Settings dashboard — *Segmentation provider* → **Replicate SAM-2**. You
don't have to tear down your SAM 3.1 deployment; it just stops being
called.

## What's NOT in this slice

- **Video segmentation** — SAM 3.1's video Object-Multiplex mode is
  irrelevant for static packaging assets and intentionally not wired up.
- **Replicate-hosted SAM 3** — Meta hasn't shipped `meta/sam-3` on
  Replicate. When they do, we'll add a new `replicate_sam3` provider
  with its own output-shape adapter, since the Python `output["masks"]`
  shape Meta uses internally differs from the PNG-URL shape Replicate's
  community SAM-2 ports return.
