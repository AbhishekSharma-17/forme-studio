# Getting the API keys for Forme Studio

All keys go in `backend/.env`. The backend is started with
`uvicorn --reload --reload-include '*.env'`, so saving the file
restarts the worker — refresh the page and the capability strip in
the header should flip from grey to **green**.

| Variable                              | Required for                     | Paid?       |
| ------------------------------------- | -------------------------------- | ----------- |
| `OPENAI_API_KEY`                      | Generate / edit / Composable PSD | **Yes**     |
| `VECTORIZER_AI_API_ID`                | Vector export (SVG)              | Paid ($0.20/img, free demo with watermark) |
| `VECTORIZER_AI_API_KEY`               | (paired with the above)          | "           |
| `CLOUDCONVERT_API_KEY`                | CDR export — hosted              | Paid (25 conv-min/day free tier) |
| `CLOUDCONVERT_SANDBOX_API_KEY`        | CDR sandbox — dev only           | **Free** (watermarked output) |
| `FORME_INKSCAPE_PATH`                 | Vector fallback                  | **Free** (local binary) |
| `FORME_UNICONVERTOR_PATH`             | CDR fallback                     | **Free** (sK1 local binary) |
| `FORME_TESSERACT_CMD`                 | OCR inside Make print-ready      | **Free** (local binary) |

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

  # CDR: paid primary, free local fallback
  FORME_CDR_PROVIDER=cloudconvert              # or: uniconvertor
  FORME_CDR_FALLBACK=uniconvertor              # or: none
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
   **$0.10–$0.20 per image** (we report the exact bill in the UI). The
   Composable PSD pipeline fires N image-gen calls per assemble
   (~$0.02–$0.10 each), so budget accordingly.

> The `image_model` shown in the header (`gpt-image-2-2026-04-21`) is the
> snapshot we pinned in `FORME_OPENAI_IMAGE_MODEL`. You can edit that to
> `gpt-image-2` for the rolling version, or to whatever newer pin OpenAI
> publishes.

---

## 2. Vectorizer.AI — `VECTORIZER_AI_API_ID` + `VECTORIZER_AI_API_KEY`

Needed for **PNG → vector SVG / EPS / PDF**. This is the cleanest
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

## 3. CloudConvert — `CLOUDCONVERT_API_KEY` + `CLOUDCONVERT_SANDBOX_API_KEY`

Needed for **SVG → CDR (CorelDRAW)** when you don't have UniConvertor
installed locally. The Settings dashboard's `Use CloudConvert sandbox`
toggle picks between the two keys.

1. Sign up at <https://cloudconvert.com>.
2. Open <https://cloudconvert.com/dashboard/api/v2/keys>.
3. Create one **production** key and one **sandbox** key — they 401
   against each other's host so Forme keeps two slots.
4. Paste both into `backend/.env`:

   ```
   CLOUDCONVERT_API_KEY=eyJ0eXAiOiJKV1QiLCJh...
   CLOUDCONVERT_SANDBOX_API_KEY=eyJ0eXAiOiJKV1QiLCJh...
   ```

Production gives 25 conversion-minutes/day free tier. Sandbox is fully
free but watermarks output — flip the toggle in Settings to switch.

---

## 4. Tesseract OCR (local, free)

The Make-print-ready pipeline runs OCR automatically as part of its
unified analyze pass. Each detected text region becomes an editable
row in the review dialog. If Tesseract isn't installed, the pipeline
silently falls back to graphics-only and you can still add text
blocks by hand.

```
brew install tesseract           # macOS
apt install tesseract-ocr        # Debian / Ubuntu
```

Verify with `tesseract --version`. The Settings dashboard's *OCR ·
Tesseract* card shows green when the binary is found.

---

## 5. (Optional) Markup over the OpenAI bill — `FORME_PRICING_MARKUP_PERCENT`

Forme currently shows:

- `provider_cost_usd` — the exact amount OpenAI charged
- `user_cost_usd`     — provider + markup

Set the markup with a plain percentage:

```
FORME_PRICING_MARKUP_PERCENT=10     # +10% on top of provider cost
FORME_PRICING_MARKUP_PERCENT=0      # report provider price as-is
```

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
