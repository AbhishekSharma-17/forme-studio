"""Vision element-discovery — analyzes an approved design and returns a
JSON manifest of its visual elements so each can be regenerated cleanly
on a transparent canvas via gpt-image-2.

Uses OpenAI chat.completions with gpt-4o-mini (vision-capable, cheap:
~$0.001-0.005 per analysis). The system prompt forces strict JSON output.

This is the planner half of the "composable PSD" workflow. The other half
(per-element generation) lives in :mod:`app.services.compose`. See
``docs/COMPOSABLE_PSD.md`` for the full architecture.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from fastapi import HTTPException, status
from openai import AsyncOpenAI

log = structlog.get_logger(__name__)

ElementKind = Literal[
    "graphic",
    "wordmark",
    "headline",
    "ornament",
    "seal",
    "body_copy",
    "text",  # OCR-discovered text region; rendered via Pillow, not gpt-image-2
]


@dataclass(frozen=True)
class ElementSpec:
    """One visual element the design contains.

    Attributes
    ----------
    name : str
        URL-safe identifier, e.g. ``"sandalwood_botanical"`` or
        ``"imara_wordmark"``. Used as the layer name in the assembled PSD.
    label : str
        Human-friendly label, e.g. ``"Sandalwood + saffron botanical"``.
    prompt : str
        Self-contained prompt to feed gpt-image-2 to regenerate this
        element alone on a transparent background. Must NOT reference
        "the whole sticker" — only the element itself. For ``kind="text"``
        this is informational only (the actual render comes from ``text``).
    position_mm : tuple[float, float, float, float]
        ``(x, y, width, height)`` in millimetres, measured from the
        top-left of the **trim** (NOT the bleed-extended canvas).
    size_px : str
        gpt-image-2 native size to render at: ``"1024x1024"``,
        ``"1024x1536"``, or ``"1536x1024"``. The assembler downscales
        as needed when placing into the PSD. For ``kind="text"`` this is
        the *target render size* the Pillow text-layer is rasterised to.
    kind : ElementKind
        Coarse category. ``text`` elements come from OCR and are
        rendered via Pillow; everything else routes through gpt-image-2.
    text : str | None
        The exact string content. ONLY populated for ``kind="text"``
        elements. The unified analyzer extracts this from OCR. The user
        can edit it in the review UI before assembly.
    confidence : float | None
        OCR confidence (0-100). ONLY populated for ``kind="text"``.
        The review UI flags entries below ~75 as potentially garbled.
    vectorizable : bool
        Hint from the analyzer: should this element be auto-vectorized
        (Vectorizer.AI) during assembly so SVG/CDR exports stay crisp?
        True for line art (logos, wordmarks, ornaments), False for
        photo-realistic illustrations. ``text`` elements are rendered
        directly so the flag is informational; assemble paths handle
        text specially regardless.
    """

    name: str
    label: str
    prompt: str
    position_mm: tuple[float, float, float, float]
    size_px: str
    kind: ElementKind
    text: str | None = None
    confidence: float | None = None
    vectorizable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "prompt": self.prompt,
            "position_mm": list(self.position_mm),
            "size_px": self.size_px,
            "kind": self.kind,
            "text": self.text,
            "confidence": self.confidence,
            "vectorizable": self.vectorizable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ElementSpec:
        pos = data["position_mm"]
        return cls(
            name=str(data["name"]),
            label=str(data["label"]),
            prompt=str(data["prompt"]),
            position_mm=(
                float(pos[0]),
                float(pos[1]),
                float(pos[2]),
                float(pos[3]),
            ),
            size_px=str(data.get("size_px", "1024x1024")),
            kind=str(data.get("kind", "graphic")),  # type: ignore[arg-type]
            text=(str(data["text"]) if data.get("text") is not None else None),
            confidence=(
                float(data["confidence"])
                if data.get("confidence") is not None
                else None
            ),
            vectorizable=bool(data.get("vectorizable", False)),
        )


_VISION_MODEL = "gpt-4o-mini"  # cheap, vision-capable, fast

_SYSTEM_PROMPT = """You are a packaging-design analyst. Given an image of a
finished product sticker/label and its physical trim dimensions, identify
every DISTINCT VISUAL ELEMENT in the design and emit a strict-JSON
manifest. Each element will later be regenerated independently as a
transparent PNG and re-assembled into a layered PSD for designers.

OUTPUT FORMAT: a single JSON object with one key, "elements", whose value
is an array. Each element object MUST contain:

  name        — snake_case identifier (e.g. "imara_wordmark")
  label       — short human-readable label
  prompt      — a SELF-CONTAINED prompt to regenerate this element
                ALONE on a transparent background via an image model.
                NEVER reference "the whole sticker" or other elements.
                Describe colour, style, dimensions, and any text inside.
                Always end with "Transparent background. Isolated. No
                other elements."
  position_mm — [x, y, width, height] of this element's bounding box
                in millimetres relative to the TRIM top-left corner.
                Be precise — use the trim dimensions you're given.
  size_px     — one of "1024x1024" (square), "1024x1536" (portrait),
                or "1536x1024" (landscape). Pick the closest match
                to the element's aspect.
  kind        — one of: "graphic" (illustration/photo), "wordmark"
                (logo lockup), "headline" (large display text),
                "ornament" (decorative shape/divider/frame),
                "seal" (badge/sticker-within-sticker), "body_copy"
                (small dense paragraphs — better handled by OCR).
  vectorizable — boolean. true if this element is line art / clean
                shapes / flat colours that would benefit from being
                vectorized (logos, wordmarks, simple ornaments, icons).
                false for photo-realistic illustrations, gradients,
                watercolour textures, or anything where vector tracing
                would produce thousands of messy paths. Headlines and
                body_copy are always false (text is rendered separately,
                not vectorized).

GUIDELINES:
- Decompose into 5-12 elements. Too few = loss of editability;
  too many = visual noise + cost.
- DO NOT include a separate "background" element; the assembler builds
  the background canvas itself.
- DO NOT include text regions in this manifest — text is handled by a
  separate OCR pass. Only describe genuinely visual graphic elements.
- For "body_copy" elements (long ingredients / directions lists),
  do NOT emit them either; OCR will pick them up.
- The output MUST be valid JSON. No prose, no markdown, no code fences.
"""


async def discover_elements(
    client: AsyncOpenAI,
    image_bytes: bytes,
    trim_mm: tuple[float, float],
    *,
    extra_hint: str | None = None,
) -> list[ElementSpec]:
    """Ask the vision model to break a finished sticker into elements.

    Args:
        client: an authenticated AsyncOpenAI client (uses the same key
            as image generation).
        image_bytes: PNG/JPEG bytes of the approved finished design.
        trim_mm: ``(width, height)`` of the trim in millimetres so the
            model can output accurate position_mm coords.
        extra_hint: optional designer hint to bias the decomposition,
            e.g. ``"This is for a shampoo bottle; bias toward isolating
            the brand mark and hero illustration."``

    Raises:
        HTTPException 502 if the upstream call fails or returns invalid JSON.
    """
    trim_w, trim_h = trim_mm
    user_text = (
        f"The sticker's trim dimensions are {trim_w:.0f} × {trim_h:.0f} mm "
        f"(portrait orientation, width first). Analyse the attached "
        f"finished design and emit the element manifest as instructed."
    )
    if extra_hint:
        user_text += f"\n\nExtra hint from the designer: {extra_hint}"

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

    log.info("vision_discover_call", bytes=len(image_bytes), model=_VISION_MODEL)
    try:
        resp = await client.chat.completions.create(
            model=_VISION_MODEL,
            response_format={"type": "json_object"},
            temperature=0.2,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )
    except Exception as exc:  # SDK can raise APIConnectionError, etc.
        log.exception("vision_discover_failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Vision element discovery failed: {exc}",
        ) from exc

    content = resp.choices[0].message.content if resp.choices else None
    if not content:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Vision model returned an empty response.",
        )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        log.warning("vision_invalid_json", content_preview=content[:300])
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Vision model returned invalid JSON: {exc}",
        ) from exc

    raw_elements = parsed.get("elements")
    if not isinstance(raw_elements, list) or not raw_elements:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Vision model returned no 'elements' array. "
                f"Got: {json.dumps(parsed)[:200]}"
            ),
        )

    specs: list[ElementSpec] = []
    for i, raw in enumerate(raw_elements):
        if not isinstance(raw, dict):
            log.warning("vision_skipping_non_dict_element", index=i)
            continue
        try:
            specs.append(ElementSpec.from_dict(raw))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("vision_invalid_element", index=i, error=str(exc), raw=raw)
            continue

    if not specs:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Vision model returned elements but none parsed cleanly.",
        )

    log.info("vision_discovered", count=len(specs))
    return specs
