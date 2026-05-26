"""Forme Studio settings — loaded from environment via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    All settings are loaded from environment variables prefixed with `FORME_`
    or, when noted, from the conventional unprefixed names (OPENAI_API_KEY etc.).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Server ---
    host: str = Field("127.0.0.1", alias="FORME_HOST")
    port: int = Field(8002, alias="FORME_PORT", ge=1, le=65535)
    log_level: str = Field("info", alias="FORME_LOG_LEVEL")
    cors_origins: str = Field(
        "http://localhost:2002,http://127.0.0.1:2002",
        alias="FORME_CORS_ORIGINS",
        description="Comma-separated list of allowed CORS origins.",
    )

    # --- Storage ---
    workspaces_dir: Path = Field(..., alias="FORME_WORKSPACES_DIR")
    db_path: Path = Field(..., alias="FORME_DB_PATH")

    # --- AI providers ---
    openai_api_key: str | None = Field(None, alias="OPENAI_API_KEY")

    # Vectorizer.AI — paid primary provider for PNG → SVG/EPS/PDF.
    # Per Vectorizer.AI HTTP Basic auth: -u <API_ID>:<API_KEY>.
    vectorizer_ai_api_id: str | None = Field(None, alias="VECTORIZER_AI_API_ID")
    vectorizer_ai_api_key: str | None = Field(None, alias="VECTORIZER_AI_API_KEY")
    # 'production' (1 credit) | 'test' (0.1 credit, dev-only watermark) |
    # 'preview' (free, low-res preview only).
    vectorizer_ai_mode: str = Field(
        "production",
        alias="FORME_VECTORIZER_AI_MODE",
        description="vectorizer.ai billing mode: production | test | preview.",
    )

    # Replicate-hosted SAM-2 (one option for segmentation).
    replicate_api_token: str | None = Field(None, alias="REPLICATE_API_TOKEN")

    # --- Provider selection (configurable; fallback is NOT automatic) ---
    vectorizer_provider: str = Field(
        "vectorizer_ai",
        alias="FORME_VECTORIZER_PROVIDER",
        description="'vectorizer_ai' (paid, best) | 'inkscape_potrace' (free, local).",
    )
    vectorizer_fallback: str | None = Field(
        "inkscape_potrace",
        alias="FORME_VECTORIZER_FALLBACK",
        description=(
            "Offered to the user IF the primary fails — never auto-invoked. "
            "'inkscape_potrace' | 'none'."
        ),
    )

    segmentation_provider: str = Field(
        "replicate",
        alias="FORME_SEGMENTATION_PROVIDER",
        description=(
            "'replicate' (SAM-2 hosted, default) | "
            "'self_hosted' (generic SAM-2/SAM-3 wire contract) | "
            "'sam3' (SAM 3.1 self-hosted, richer schema with label + score) | "
            "'none'."
        ),
    )
    segmentation_self_hosted_url: str | None = Field(
        None,
        alias="FORME_SEGMENTATION_SELF_HOSTED_URL",
        description="HTTPS endpoint for a generic SAM (v2/v3) deployment.",
    )
    segmentation_self_hosted_token: str | None = Field(
        None,
        alias="FORME_SEGMENTATION_SELF_HOSTED_TOKEN",
        description="Optional bearer token for the generic self-hosted endpoint.",
    )
    # --- SAM 3.1 self-hosted (image only) -------------------------------
    # Separate URL/token slots so users can keep a SAM-2 deployment running
    # in parallel with their new SAM 3.1 box during the cutover.
    sam3_endpoint_url: str | None = Field(
        None,
        alias="FORME_SAM3_ENDPOINT_URL",
        description=(
            "HTTPS endpoint for your SAM 3.1 image inference service. "
            "Contract: POST <URL> multipart with 'image' (PNG bytes); "
            "response JSON {width, height, model, masks: [{png_b64, "
            "bbox: [x1,y1,x2,y2], area_px, score?, label?}]}. "
            "score + label are optional — label is set when the model is "
            "text-prompted so Tier B PSDs can name layers semantically."
        ),
    )
    sam3_endpoint_token: str | None = Field(
        None,
        alias="FORME_SAM3_ENDPOINT_TOKEN",
        description="Optional bearer token for the SAM 3.1 endpoint.",
    )
    sam3_text_prompt: str | None = Field(
        None,
        alias="FORME_SAM3_TEXT_PROMPT",
        description=(
            "Optional comma-separated concepts to ask SAM 3.1 to segment "
            "(e.g. 'logo, wordmark, bottle'). When unset, the endpoint "
            "should run automatic mask generation."
        ),
    )

    # Replicate model identifier for SAM-2 automatic mask generation.
    # Override if you want a community port or a pinned version hash.
    replicate_sam2_model: str = Field(
        "meta/sam-2",
        alias="FORME_REPLICATE_SAM2_MODEL",
    )

    # Tier C controls (OCR-driven editable text layers).
    tier_c_enabled: bool = Field(
        False,
        alias="FORME_TIER_C_ENABLED",
        description="Allow Tier C PSD exports (SAM-2 + Tesseract OCR).",
    )
    tesseract_cmd: str = Field(
        "tesseract",
        alias="FORME_TESSERACT_CMD",
        description="Tesseract CLI binary (absolute path or PATH-resolvable).",
    )
    tesseract_lang: str = Field(
        "eng",
        alias="FORME_TESSERACT_LANG",
        description="Comma-separated language codes (e.g. 'eng', 'eng+spa').",
    )

    # Max time we'll wait on a single segmentation call.
    segmentation_timeout_s: float = Field(
        180.0, alias="FORME_SEGMENTATION_TIMEOUT_S", ge=10.0, le=600.0
    )

    # Max time we'll wait for a single vector-export call (network or CLI).
    vectorizer_timeout_s: float = Field(
        180.0, alias="FORME_VECTORIZER_TIMEOUT_S", ge=10.0, le=600.0
    )

    # Path to the CMYK ICC profile used for PDF/X-4 print exports.
    # macOS ships a generic one at /System/Library/ColorSync/Profiles/.
    # For press-perfect output, drop in something like ISO Coated v2.
    print_icc_path: str = Field(
        "/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc",
        alias="FORME_PRINT_ICC_PATH",
    )
    print_icc_name: str = Field(
        "Generic CMYK",
        alias="FORME_PRINT_ICC_NAME",
        description="Human label for the ICC used as output intent.",
    )

    # Local CLI binaries that show as capabilities when present.
    inkscape_path: str = Field(
        "/opt/homebrew/bin/inkscape", alias="FORME_INKSCAPE_PATH"
    )
    uniconvertor_path: str = Field(
        "/opt/homebrew/bin/uniconvertor",
        alias="FORME_UNICONVERTOR_PATH",
        description=(
            "UniConvertor 2.x CLI binary used for the free, local SVG → CDR "
            "fallback. Install via the sK1 project. Inkscape cannot export "
            "CDR — only import it — so a separate converter is required."
        ),
    )

    # --- CDR export (slice 7) -------------------------------------------
    # Default OFF — most users won't have either provider wired. Toggle
    # on from the Settings dashboard once you've installed UniConvertor
    # or pasted a CLOUDCONVERT_API_KEY.
    cdr_enabled: bool = Field(
        False,
        alias="FORME_CDR_ENABLED",
        description=(
            "Master switch for CDR exports. When off, the endpoint returns "
            "503 and the frontend hides the CDR button. UniConvertor is "
            "unmaintained / hard to install on Apple Silicon, so default off."
        ),
    )
    cdr_provider: str = Field(
        "cloudconvert",
        alias="FORME_CDR_PROVIDER",
        description="'cloudconvert' (paid, reliable) | 'uniconvertor' (free, local).",
    )
    cdr_fallback: str | None = Field(
        "uniconvertor",
        alias="FORME_CDR_FALLBACK",
        description="'cloudconvert' | 'uniconvertor' | 'none'.",
    )
    cdr_timeout_s: float = Field(
        180.0, alias="FORME_CDR_TIMEOUT_S", ge=10.0, le=600.0
    )
    cloudconvert_api_key: str | None = Field(
        None,
        alias="CLOUDCONVERT_API_KEY",
        description=(
            "Live key from cloudconvert.com — used against api.cloudconvert.com "
            "when FORME_CLOUDCONVERT_SANDBOX is false. Counts against your "
            "free 10/day quota or paid plan."
        ),
    )
    cloudconvert_sandbox_api_key: str | None = Field(
        None,
        alias="CLOUDCONVERT_SANDBOX_API_KEY",
        description=(
            "Sandbox key from cloudconvert.com — used against "
            "api.sandbox.cloudconvert.com when FORME_CLOUDCONVERT_SANDBOX "
            "is true. Always free; outputs are watermarked / demo-only. "
            "Sandbox keys 401 against the production host, hence two slots."
        ),
    )
    cloudconvert_sandbox: bool = Field(
        False,
        alias="FORME_CLOUDCONVERT_SANDBOX",
        description=(
            "Dashboard-toggleable switch between sandbox (free, "
            "watermarked) and live (paid quota). Picks BOTH the host "
            "and which key to authenticate with."
        ),
    )

    @property
    def cloudconvert_active_key(self) -> str | None:
        """Resolve the key matching the current sandbox toggle.

        Empty strings (pydantic's stand-in for "unset via env var") are
        treated as missing so the caller can rely on a truthy check.
        """
        key = (
            self.cloudconvert_sandbox_api_key
            if self.cloudconvert_sandbox
            else self.cloudconvert_api_key
        )
        return key or None

    # --- Models ---
    openai_image_model: str = Field(
        "gpt-image-2-2026-04-21", alias="FORME_OPENAI_IMAGE_MODEL"
    )

    # --- Pricing ---
    pricing_markup_percent: float = Field(
        0.0, alias="FORME_PRICING_MARKUP_PERCENT", ge=0.0, le=1000.0
    )

    # --- Image generation ---
    image_generation_timeout_s: float = Field(
        180.0, alias="FORME_IMAGE_TIMEOUT_S", ge=10.0, le=600.0
    )

    @field_validator("cors_origins")
    @classmethod
    def _strip_cors(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached settings accessor."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
