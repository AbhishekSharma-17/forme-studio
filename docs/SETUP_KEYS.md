# Getting the API keys for Forme Studio

All keys go in `backend/.env`. The backend is started with
`uvicorn --reload --reload-include '*.env'`, so saving the file
restarts the worker — refresh the page and the capability strip in
the header should flip from grey to **green**.

| Variable                              | Required for             | Paid?       |
| ------------------------------------- | ------------------------ | ----------- |
| `OPENAI_API_KEY`                      | Slice 2 — generate/edit  | **Yes**     |
| `VECTORIZER_AI_API_ID`                | Slice 6 — vector export  | Paid ($0.20/img, free demo with watermark) |
| `VECTORIZER_AI_API_KEY`               | (paired with the above)  | "           |
| `REPLICATE_API_TOKEN`                 | Slice 4 — SAM-2 segment. | Paid (~$0.01/img, $10 trial credit) |
| `FORME_SEGMENTATION_SELF_HOSTED_URL`  | Self-hosted SAM-2        | **Free** (your DGX Spark) |
| `FORME_INKSCAPE_PATH`                 | Vector fallback + CDR    | **Free** (local binary) |

## Provider architecture

Forme has **configurable primary + fallback** per pipeline stage.
**Fallbacks are NEVER auto-invoked** — when a primary fails, the UI
surfaces the error and gives the user an explicit "try fallback?"
button.

```
backend/.env:

  # Vector: paid primary, free local fallback
  FORME_VECTORIZER_PROVIDER=vectorizer_ai      # or: inkscape_potrace
  FORME_VECTORIZER_FALLBACK=inkscape_potrace   # or: none

  # Segmentation (SAM-2): pick one
  FORME_SEGMENTATION_PROVIDER=replicate        # or: self_hosted, none
  FORME_SEGMENTATION_SELF_HOSTED_URL=          # https://your-dgx-spark:9000/segment
  FORME_SEGMENTATION_SELF_HOSTED_TOKEN=        # optional bearer
```

The capability strip in the header shows the selected primary + fallback
explicitly, so you always know which path a generation is going to take.

---

## 1. OpenAI — `OPENAI_API_KEY`

**This is the only one you need to start generating today.**

1. Open <https://platform.openai.com/api-keys>
   - If it's your first time: sign in, then click "Start verifying" to
     verify your phone — required to use gpt-image-2.
2. Click **"Create new secret key"**.
   - Name: `forme-studio-dev` (so you can revoke it independently later)
   - Permissions: **All** (or at minimum, the Images permissions)
   - Project: pick one or create `Forme Studio`
3. Copy the key (`sk-proj-...` or `sk-...`). **You can't see it again** — paste it
   into `backend/.env` immediately:

   ```
   OPENAI_API_KEY=sk-proj-AAAAA...
   ```

4. Top up credits at <https://platform.openai.com/settings/organization/billing>
   if your account doesn't have any. gpt-image-2 high-quality runs are typically
   **$0.10–$0.20 per image** (we report the exact bill in the UI).

> The `image_model` shown in the header (`gpt-image-2-2026-04-21`) is the
> snapshot we pinned in `FORME_OPENAI_IMAGE_MODEL`. You can edit that to
> `gpt-image-2` for the rolling version, or to whatever newer pin OpenAI
> publishes.

---

## 2. Replicate — `REPLICATE_API_TOKEN`

You only need this when we wire up **slice 4 (layered PSD export)**. The
layering uses Replicate-hosted **SAM-2 (Segment Anything v2)** to slice the
generated artwork into product / background / typography layers.

1. Sign up / sign in at <https://replicate.com/signin>.
2. Open <https://replicate.com/account/api-tokens>.
3. Click **"Create token"**.
   - Name: `forme-studio-dev`
4. Copy the token (starts with `r8_...`) into `backend/.env`:

   ```
   REPLICATE_API_TOKEN=r8_AAAAAA...
   ```

Pricing for SAM-2: roughly **$0.005–$0.02 per image** depending on size and
mask count. Replicate gives you free trial credit at signup.

---

## 3. Vectorizer.AI — `VECTORIZER_AI_API_ID` + `VECTORIZER_AI_API_KEY`

Needed for **slice 6 (PNG → vector SVG / EPS / PDF)**. This is the cleanest
way to turn a generated logo / illustration into press-ready vector artwork.

1. Sign up at <https://vectorizer.ai/api>.
2. Click **"Subscribe"** and pick a tier:
   - **Free**: 100 previews/month (low-res, watermarked) — fine for testing
   - **Production**: ~$0.20/conversion or volume packs
3. Open the **API Credentials** page (top-right menu after login).
4. Copy both values into `backend/.env`:

   ```
   VECTORIZER_AI_API_ID=vk_...
   VECTORIZER_AI_API_KEY=...
   ```

The capability strip needs **both** present to light up `Vectorizer.AI` —
they come as a pair.

---

## 4. (Optional) Markup over the OpenAI bill — `FORME_PRICING_MARKUP_PERCENT`

We already proved both modes in opneai-image2. Forme currently shows:

- `provider_cost_usd` — the exact amount OpenAI charged
- `user_cost_usd`     — provider + markup

Set the markup with a plain percentage:

```
FORME_PRICING_MARKUP_PERCENT=10     # +10% on top of provider cost
FORME_PRICING_MARKUP_PERCENT=0      # report provider price as-is
```

Forme is currently set to **10%** — every generation card in the gallery
shows both numbers so you can see the spread.

---

## Reloading

The dev server is started with `uvicorn --reload --reload-include '*.env'`,
so saving `.env` triggers a worker restart. If you ever want to verify the
new values landed:

```
curl http://127.0.0.1:8002/api/health
```

`capabilities.openai_image` should flip to `true` once `OPENAI_API_KEY` is
present.
