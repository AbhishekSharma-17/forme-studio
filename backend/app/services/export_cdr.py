"""SVG → CDR export.

CorelDRAW's ``.cdr`` is the format Eastern European / older print shops
still ask for. Two interchangeable providers, mirroring the slice 6
vector dispatcher pattern:

* ``cloudconvert``  — paid, hosted. Three-step REST flow against
  ``https://api.cloudconvert.com``: import-upload → convert → export.
* ``uniconvertor``  — free, local. Shells out to the ``uniconvertor`` CLI
  from the sK1 project.

**Important honest note**: Inkscape's CLI **cannot export CDR** — it
only imports it via ``libcdr``. The original roadmap line "CDR via
Inkscape CLI" was wishful thinking; the real options are CloudConvert
or UniConvertor and that's what this slice ships.

The dispatcher (:func:`convert_svg_to_cdr`) **never** auto-falls-back.
Failures bubble up with the provider's actual error in ``detail`` so
the UI can show a "Try with <fallback>?" button — the user explicitly
picks the alternate.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
from fastapi import HTTPException, status

from app.config import Settings, get_settings

log = structlog.get_logger(__name__)

ProviderName = Literal["cloudconvert", "uniconvertor"]
_VALID_PROVIDERS: frozenset[str] = frozenset({"cloudconvert", "uniconvertor"})


@dataclass(frozen=True)
class CdrResult:
    """One CDR file produced by either provider."""

    cdr_bytes: bytes
    provider: ProviderName
    size_bytes: int


# --------------------------------------------------------------- dispatcher


async def convert_svg_to_cdr(
    svg_bytes: bytes,
    *,
    provider: str | None = None,
) -> CdrResult:
    """Convert ``svg_bytes`` to a CDR file using the configured provider.

    Args:
        svg_bytes: input SVG (the slice 6 vector output, typically).
        provider: optional explicit override of ``FORME_CDR_PROVIDER``;
            the UI passes this when the user clicks "Try with fallback?"
            after a failure. ``None`` uses the env-configured primary.

    Raises:
        HTTPException 400 if the provider name is unknown.
        HTTPException 503 if the chosen provider has no credentials /
            binary.
        HTTPException 502/504 if the provider call itself fails.
    """
    settings = get_settings()

    if not settings.cdr_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "CDR exports are disabled. Toggle FORME_CDR_ENABLED on in "
                "Settings → CDR export, after installing UniConvertor or "
                "configuring a CloudConvert API key."
            ),
        )

    chosen = provider or settings.cdr_provider

    if chosen not in _VALID_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown CDR provider '{chosen}'. "
                f"Choose one of: {', '.join(sorted(_VALID_PROVIDERS))}."
            ),
        )

    if chosen == "cloudconvert":
        # Sandbox vs live keys are environment-scoped — pick whichever
        # matches the current FORME_CLOUDCONVERT_SANDBOX toggle.
        active_key = settings.cloudconvert_active_key
        if not active_key:
            env_label = "sandbox" if settings.cloudconvert_sandbox else "live"
            env_var = (
                "CLOUDCONVERT_SANDBOX_API_KEY"
                if settings.cloudconvert_sandbox
                else "CLOUDCONVERT_API_KEY"
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"CloudConvert {env_label} API key is missing. "
                    f"Set {env_var} in .env, flip the sandbox toggle in "
                    "Settings, or pick the 'uniconvertor' provider."
                ),
            )
        return await _convert_via_cloudconvert(svg_bytes, settings)

    # uniconvertor
    if not Path(settings.uniconvertor_path).is_file():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"UniConvertor CLI not found at {settings.uniconvertor_path}. "
                "Install the sK1 / UniConvertor 2 package, update "
                "FORME_UNICONVERTOR_PATH, or pick the 'cloudconvert' provider."
            ),
        )
    return await _convert_via_uniconvertor(svg_bytes, settings)


# --------------------------------------------------------------- CloudConvert


_CC_API_BASE_PROD = "https://api.cloudconvert.com/v2"
_CC_API_BASE_SANDBOX = "https://api.sandbox.cloudconvert.com/v2"


async def _convert_via_cloudconvert(
    svg_bytes: bytes, settings: Settings
) -> CdrResult:
    """Run the three-step CloudConvert /v2/jobs flow.

    A single ``/v2/jobs`` request bundles import-upload + convert +
    export-url into one async job. We poll for completion, then download
    the resulting CDR.

    When ``FORME_CLOUDCONVERT_SANDBOX`` is true, we hit the free sandbox
    host at ``api.sandbox.cloudconvert.com`` — same surface, no billing.
    """
    api_key = settings.cloudconvert_active_key
    assert api_key is not None  # guarded by the dispatcher
    api_base = (
        _CC_API_BASE_SANDBOX if settings.cloudconvert_sandbox else _CC_API_BASE_PROD
    )
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    timeout = settings.cdr_timeout_s

    log.info(
        "cloudconvert_call_start",
        bytes=len(svg_bytes),
        sandbox=settings.cloudconvert_sandbox,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as http:
            # 1. Create the job — import (upload) → convert (svg→cdr) → export (url).
            job_payload: dict[str, Any] = {
                "tasks": {
                    "import-svg": {"operation": "import/upload"},
                    "convert-cdr": {
                        "operation": "convert",
                        "input": "import-svg",
                        "input_format": "svg",
                        "output_format": "cdr",
                    },
                    "export-cdr": {
                        "operation": "export/url",
                        "input": "convert-cdr",
                    },
                }
            }
            job_resp = await http.post(
                f"{api_base}/jobs", json=job_payload, headers=headers
            )
            _raise_for_cc(job_resp, "create-job")
            job = job_resp.json()["data"]

            # 2. Find the upload task — POST the SVG bytes to its `form.url`.
            import_task = _task_by_name(job, "import-svg")
            upload_form = import_task["result"]["form"]
            upload_url: str = upload_form["url"]
            upload_params: dict[str, str] = upload_form.get("parameters", {})

            upload_resp = await http.post(
                upload_url,
                data=upload_params,
                files={"file": ("input.svg", svg_bytes, "image/svg+xml")},
            )
            if upload_resp.status_code not in (200, 201, 204):
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        f"CloudConvert upload returned {upload_resp.status_code}: "
                        f"{upload_resp.text[:300]}"
                    ),
                )

            # 3. Poll the job until the export task finishes (or fails / times out).
            job_id = job["id"]
            export_url = await _poll_cloudconvert_job(
                http, headers, job_id, timeout, api_base
            )

            # 4. Download the CDR.
            dl = await http.get(export_url)
            if dl.status_code != 200:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        f"CloudConvert download returned {dl.status_code}: "
                        f"{dl.text[:300]}"
                    ),
                )
            cdr = dl.content
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"CloudConvert timed out after {timeout}s.",
        ) from exc
    except Exception as exc:
        log.exception("cloudconvert_failed")
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"CloudConvert request failed: {exc}",
        ) from exc

    if not cdr:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="CloudConvert returned an empty CDR payload.",
        )

    log.info("cloudconvert_done", bytes=len(cdr))
    return CdrResult(cdr_bytes=cdr, provider="cloudconvert", size_bytes=len(cdr))


def _raise_for_cc(resp: httpx.Response, step: str) -> None:
    if resp.status_code not in (200, 201):
        # CloudConvert error envelope: {"message": "...", "errors": {...}}
        detail = (resp.text or "").strip()[:300] or f"HTTP {resp.status_code}"
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"CloudConvert {step} returned {resp.status_code}: {detail}",
        )


def _task_by_name(job: dict[str, Any], name: str) -> dict[str, Any]:
    for t in job.get("tasks", []):
        if t.get("name") == name:
            return dict(t)
    raise HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        detail=f"CloudConvert job is missing task '{name}'.",
    )


async def _poll_cloudconvert_job(
    http: httpx.AsyncClient,
    headers: dict[str, str],
    job_id: str,
    timeout: float,
    api_base: str,
) -> str:
    """Poll the job until export task is ``finished``; return the export URL.

    Polls every 1.5s. Raises 504 if total elapsed exceeds ``timeout``,
    or 502 if any task transitions to ``error``.
    """
    elapsed = 0.0
    interval = 1.5
    while elapsed < timeout:
        resp = await http.get(f"{api_base}/jobs/{job_id}", headers=headers)
        _raise_for_cc(resp, "poll-job")
        job = resp.json()["data"]
        tasks = job.get("tasks", [])

        # Any task in error → bail with that task's message.
        for t in tasks:
            if t.get("status") == "error":
                err = t.get("message") or "unknown error"
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    detail=(
                        f"CloudConvert task '{t.get('name')}' failed: {err}"
                    ),
                )

        # Export finished → grab the URL.
        export_task = _task_by_name(job, "export-cdr")
        if export_task.get("status") == "finished":
            files = export_task.get("result", {}).get("files", [])
            if not files or not files[0].get("url"):
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    detail="CloudConvert export task finished without a file URL.",
                )
            return str(files[0]["url"])

        await asyncio.sleep(interval)
        elapsed += interval

    raise HTTPException(
        status.HTTP_504_GATEWAY_TIMEOUT,
        detail=f"CloudConvert job did not finish within {timeout}s.",
    )


# --------------------------------------------------------------- UniConvertor


async def _convert_via_uniconvertor(
    svg_bytes: bytes, settings: Settings
) -> CdrResult:
    """Shell out to the UniConvertor 2 CLI: ``uniconvertor input.svg output.cdr``.

    UniConvertor 2 is from the sK1 project. It writes CorelDRAW X4-era
    binaries that recent CorelDRAW versions still open cleanly; older
    Corel users (a meaningful share of European print shops) are the
    target audience.
    """
    binary = settings.uniconvertor_path

    with tempfile.TemporaryDirectory(prefix="forme_cdr_") as tmp:
        tmp_path = Path(tmp)
        input_path = tmp_path / "input.svg"
        output_path = tmp_path / "output.cdr"
        input_path.write_bytes(svg_bytes)

        cmd = [binary, str(input_path), str(output_path)]
        log.info("uniconvertor_call", cmd=cmd[0], bytes=len(svg_bytes))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=settings.cdr_timeout_s,
            )
        except TimeoutError as exc:
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT,
                detail=(
                    f"UniConvertor timed out after "
                    f"{settings.cdr_timeout_s}s."
                ),
            ) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"UniConvertor CLI not executable: {exc}",
            ) from exc
        except Exception as exc:
            log.exception("uniconvertor_failed")
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"UniConvertor call failed: {exc}",
            ) from exc

        if proc.returncode != 0 or not output_path.is_file():
            err_tail = (stderr or b"").decode("utf-8", errors="replace")[:400]
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=(
                    f"UniConvertor exited {proc.returncode}: "
                    f"{err_tail or 'no CDR produced'}"
                ),
            )

        cdr = output_path.read_bytes()

    log.info("uniconvertor_done", bytes=len(cdr))
    return CdrResult(
        cdr_bytes=cdr, provider="uniconvertor", size_bytes=len(cdr)
    )


# ----------------------------------------------------------------- naming


def derive_export_filename(asset_id: int) -> str:
    """Filename rule: ``assetX_<utc-stamp>.cdr``."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"asset{asset_id}_{ts}.cdr"
