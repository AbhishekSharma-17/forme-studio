"""Settings dashboard — read the env-derived config, toggle safe fields.

The dashboard exposes a curated view of what's in ``.env``. Secrets are
**redacted** in the response (only last 4 characters visible) so users can
verify a key is present without exposing it. The PATCH endpoint can only
modify a whitelisted set of *non-secret* fields — actual API keys must be
edited in ``.env`` directly.

The PATCH handler writes back to the same ``.env`` the backend was booted
with; uvicorn's ``--reload-include '*.env'`` then triggers a worker
restart so the next request sees the new values.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.config import get_settings

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


# Whitelist of env keys that PATCH /api/settings can update.
# Secret keys are deliberately omitted — they must be edited in .env.
WRITABLE_KEYS: tuple[str, ...] = (
    "FORME_VECTORIZER_PROVIDER",
    "FORME_VECTORIZER_FALLBACK",
    "FORME_VECTORIZER_AI_MODE",
    "FORME_VECTORIZER_TIMEOUT_S",
    "FORME_PRICING_MARKUP_PERCENT",
    "FORME_OPENAI_IMAGE_MODEL",
    "FORME_IMAGE_TIMEOUT_S",
    "FORME_INKSCAPE_PATH",
    "FORME_UNICONVERTOR_PATH",
    "FORME_CDR_ENABLED",
    "FORME_CDR_PROVIDER",
    "FORME_CDR_FALLBACK",
    "FORME_CDR_TIMEOUT_S",
    "FORME_CLOUDCONVERT_SANDBOX",
    "FORME_LOG_LEVEL",
    "FORME_TIER_C_ENABLED",
    "FORME_TESSERACT_CMD",
    "FORME_TESSERACT_LANG",
    "FORME_PRINT_ICC_PATH",
    "FORME_PRINT_ICC_NAME",
)


def _redact(secret: str | None) -> str | None:
    """Show a fixed-length redacted preview: 8 bullets + last 4 real chars.

    Returns ``None`` if not set. The fixed mask length keeps the dashboard
    rows tidy (some secrets are 1000+ chars JWTs) AND avoids leaking the
    true key length back to the UI.
    """
    if not secret:
        return None
    if len(secret) <= 6:
        # Too short to safely show a tail — just bullet the whole thing.
        return "•" * len(secret)
    return "••••••••" + secret[-4:]


# ---------------------------- schemas ----------------------------


class SecretField(BaseModel):
    """Redacted view of a secret + presence flag."""

    set: bool
    preview: str | None  # e.g. ••••••2A1F


class SettingsOut(BaseModel):
    """Full, redacted snapshot of the active config."""

    # Server
    host: str
    port: int
    log_level: str
    cors_origins: list[str]

    # Storage
    workspaces_dir: str
    db_path: str

    # AI credentials (redacted)
    openai_api_key: SecretField
    vectorizer_ai_api_id: SecretField
    vectorizer_ai_api_key: SecretField
    cloudconvert_api_key: SecretField
    cloudconvert_sandbox_api_key: SecretField

    # Provider routing
    vectorizer_provider: Literal["vectorizer_ai", "inkscape_potrace"]
    vectorizer_fallback: Literal["vectorizer_ai", "inkscape_potrace", "none"] | None
    vectorizer_ai_mode: Literal["production", "test", "preview"]
    vectorizer_timeout_s: float

    # Models + pricing + timeouts
    openai_image_model: str
    pricing_markup_percent: float
    image_generation_timeout_s: float

    # Local binaries
    inkscape_path: str
    inkscape_present: bool
    uniconvertor_path: str
    uniconvertor_present: bool
    tesseract_cmd: str
    tesseract_present: bool
    tesseract_lang: str

    # CDR export
    cdr_enabled: bool
    cdr_provider: Literal["cloudconvert", "uniconvertor"]
    cdr_fallback: Literal["cloudconvert", "uniconvertor", "none"] | None
    cdr_timeout_s: float
    cloudconvert_sandbox: bool

    # OCR / Tier A+OCR
    tier_c_enabled: bool

    # Print PDF/X-4 ICC
    print_icc_path: str
    print_icc_present: bool
    print_icc_name: str

    # Which fields the dashboard is allowed to modify
    writable_keys: list[str]
    env_file: str


class SettingsPatch(BaseModel):
    """Partial update — only fields listed in WRITABLE_KEYS are accepted."""

    vectorizer_provider: Literal["vectorizer_ai", "inkscape_potrace"] | None = None
    vectorizer_fallback: Literal["vectorizer_ai", "inkscape_potrace", "none"] | None = None
    vectorizer_ai_mode: Literal["production", "test", "preview"] | None = None
    vectorizer_timeout_s: float | None = Field(default=None, ge=10.0, le=600.0)
    pricing_markup_percent: float | None = Field(default=None, ge=0.0, le=1000.0)
    openai_image_model: str | None = Field(default=None, max_length=80)
    image_generation_timeout_s: float | None = Field(default=None, ge=10.0, le=600.0)
    inkscape_path: str | None = None
    uniconvertor_path: str | None = None
    cdr_enabled: bool | None = None
    cdr_provider: Literal["cloudconvert", "uniconvertor"] | None = None
    cdr_fallback: Literal["cloudconvert", "uniconvertor", "none"] | None = None
    cdr_timeout_s: float | None = Field(default=None, ge=10.0, le=600.0)
    cloudconvert_sandbox: bool | None = None
    log_level: Literal["debug", "info", "warning", "error"] | None = None
    # OCR / Tier A+OCR
    tier_c_enabled: bool | None = None
    tesseract_cmd: str | None = None
    tesseract_lang: str | None = Field(default=None, max_length=40)
    # Print PDF/X-4
    print_icc_path: str | None = None
    print_icc_name: str | None = Field(default=None, max_length=80)


# ---------------------------- helpers ----------------------------


def _env_file_path() -> Path:
    """Best-effort path to the .env we should write back to."""
    here = Path(__file__).resolve().parent.parent.parent  # backend/
    return here / ".env"


_KV_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")


def _update_env_file(env_path: Path, updates: dict[str, str]) -> None:
    """Rewrite ``env_path`` with the new values for keys present in updates.

    Only updates lines that already exist; appends new entries at the end.
    Preserves comments + blank lines.
    """
    existing = env_path.read_text().splitlines(keepends=False) if env_path.exists() else []
    found: set[str] = set()
    new_lines: list[str] = []

    for raw_line in existing:
        m = _KV_RE.match(raw_line)
        if m and m.group(1) in updates:
            key = m.group(1)
            new_lines.append(f"{key}={updates[key]}")
            found.add(key)
        else:
            new_lines.append(raw_line)

    # Append any unseen keys
    appendix = [
        f"{k}={v}" for k, v in updates.items() if k not in found
    ]
    if appendix:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append("# Added by /api/settings PATCH")
        new_lines.extend(appendix)

    env_path.write_text("\n".join(new_lines) + "\n")


def _patch_to_env_updates(patch: SettingsPatch) -> dict[str, str]:
    """Translate a SettingsPatch into env-key → string-value dict."""
    mapping = {
        "vectorizer_provider": "FORME_VECTORIZER_PROVIDER",
        "vectorizer_fallback": "FORME_VECTORIZER_FALLBACK",
        "vectorizer_ai_mode": "FORME_VECTORIZER_AI_MODE",
        "vectorizer_timeout_s": "FORME_VECTORIZER_TIMEOUT_S",
        "pricing_markup_percent": "FORME_PRICING_MARKUP_PERCENT",
        "openai_image_model": "FORME_OPENAI_IMAGE_MODEL",
        "image_generation_timeout_s": "FORME_IMAGE_TIMEOUT_S",
        "inkscape_path": "FORME_INKSCAPE_PATH",
        "uniconvertor_path": "FORME_UNICONVERTOR_PATH",
        "cdr_enabled": "FORME_CDR_ENABLED",
        "cdr_provider": "FORME_CDR_PROVIDER",
        "cdr_fallback": "FORME_CDR_FALLBACK",
        "cdr_timeout_s": "FORME_CDR_TIMEOUT_S",
        "cloudconvert_sandbox": "FORME_CLOUDCONVERT_SANDBOX",
        "log_level": "FORME_LOG_LEVEL",
        "tier_c_enabled": "FORME_TIER_C_ENABLED",
        "tesseract_cmd": "FORME_TESSERACT_CMD",
        "tesseract_lang": "FORME_TESSERACT_LANG",
        "print_icc_path": "FORME_PRINT_ICC_PATH",
        "print_icc_name": "FORME_PRINT_ICC_NAME",
    }
    updates: dict[str, str] = {}
    for field_name, env_key in mapping.items():
        value = getattr(patch, field_name)
        if value is None:
            continue
        updates[env_key] = str(value)
    return updates


# ---------------------------- routes ----------------------------


@router.get(
    "",
    response_model=SettingsOut,
    summary="Read current settings (secrets redacted)",
)
async def read_settings() -> SettingsOut:
    s = get_settings()
    env_path = _env_file_path()
    return SettingsOut(
        host=s.host,
        port=s.port,
        log_level=s.log_level,
        cors_origins=s.cors_origin_list,
        workspaces_dir=str(s.workspaces_dir),
        db_path=str(s.db_path),
        openai_api_key=SecretField(
            set=bool(s.openai_api_key), preview=_redact(s.openai_api_key)
        ),
        vectorizer_ai_api_id=SecretField(
            set=bool(s.vectorizer_ai_api_id), preview=_redact(s.vectorizer_ai_api_id)
        ),
        vectorizer_ai_api_key=SecretField(
            set=bool(s.vectorizer_ai_api_key), preview=_redact(s.vectorizer_ai_api_key)
        ),
        cloudconvert_api_key=SecretField(
            set=bool(s.cloudconvert_api_key),
            preview=_redact(s.cloudconvert_api_key),
        ),
        cloudconvert_sandbox_api_key=SecretField(
            set=bool(s.cloudconvert_sandbox_api_key),
            preview=_redact(s.cloudconvert_sandbox_api_key),
        ),
        vectorizer_provider=s.vectorizer_provider,
        vectorizer_fallback=s.vectorizer_fallback,
        vectorizer_ai_mode=s.vectorizer_ai_mode,
        vectorizer_timeout_s=s.vectorizer_timeout_s,
        openai_image_model=s.openai_image_model,
        pricing_markup_percent=s.pricing_markup_percent,
        image_generation_timeout_s=s.image_generation_timeout_s,
        inkscape_path=s.inkscape_path,
        inkscape_present=Path(s.inkscape_path).is_file(),
        uniconvertor_path=s.uniconvertor_path,
        uniconvertor_present=Path(s.uniconvertor_path).is_file(),
        cdr_enabled=s.cdr_enabled,
        cdr_provider=s.cdr_provider,
        cdr_fallback=s.cdr_fallback,
        cdr_timeout_s=s.cdr_timeout_s,
        cloudconvert_sandbox=s.cloudconvert_sandbox,
        tesseract_cmd=s.tesseract_cmd,
        tesseract_present=shutil.which(s.tesseract_cmd) is not None,
        tesseract_lang=s.tesseract_lang,
        tier_c_enabled=s.tier_c_enabled,
        print_icc_path=s.print_icc_path,
        print_icc_present=Path(s.print_icc_path).is_file(),
        print_icc_name=s.print_icc_name,
        writable_keys=list(WRITABLE_KEYS),
        env_file=str(env_path),
    )


@router.patch(
    "",
    response_model=SettingsOut,
    summary="Update non-secret settings (writes back to .env)",
)
async def patch_settings(patch: SettingsPatch) -> SettingsOut:
    """Update the curated allow-list of fields by writing to ``backend/.env``.

    Secrets (API keys, tokens) are *not* writable here — edit ``.env``
    directly to rotate them. ``--reload-include '*.env'`` makes the
    backend restart automatically.
    """
    updates = _patch_to_env_updates(patch)
    if not updates:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No writable fields provided.",
        )

    env_path = _env_file_path()
    try:
        _update_env_file(env_path, updates)
    except OSError as exc:
        log.exception("settings_write_failed", env_path=str(env_path))
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not write {env_path}: {exc}",
        ) from exc

    log.info("settings_updated", keys=list(updates.keys()))

    # Settings is cached at module level for the lifetime of the process.
    # Without uvicorn --reload we'd need to bust it ourselves; in dev
    # the file-watch picks it up. For correctness here either way, refresh
    # the cache so the response reflects the new state.
    import app.config as config_module
    config_module._settings = None

    return await read_settings()
