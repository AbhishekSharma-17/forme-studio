"use client";

import {
  Box,
  CheckCircle2,
  CircleAlert,
  Cpu,
  KeyRound,
  Layers,
  Loader2,
  Printer,
  Save,
  ServerCog,
  Sparkles,
  Wand2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Label, Select } from "@/components/ui/Field";
import {
  ApiError,
  api,
  type SecretField,
  type SettingsOut,
  type SettingsPatch,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  initial: SettingsOut;
}

export function SettingsForm({ initial }: Props) {
  const [snapshot, setSnapshot] = useState<SettingsOut>(initial);
  const [draft, setDraft] = useState<SettingsPatch>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const router = useRouter();

  const dirty = useMemo(() => {
    return Object.entries(draft).some(([k, v]) => {
      const current = (snapshot as unknown as Record<string, unknown>)[k];
      return v !== undefined && v !== current;
    });
  }, [draft, snapshot]);

  function update<K extends keyof SettingsPatch>(key: K, value: SettingsPatch[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  }

  async function handleSave() {
    if (!dirty || saving) return;
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await api.patchSettings(draft);
      setSnapshot(updated);
      setDraft({});
      setSaved(true);
      // Refresh the layout's health probe so the capability strip updates.
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  const v = (k: keyof SettingsPatch, fallback: unknown) => {
    const v = draft[k];
    return v === undefined ? fallback : v;
  };

  return (
    <div className="space-y-8">
      {/* ─── Provider routing ─── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Wand2 size={16} className="text-clay-600" />
            <CardTitle>Provider routing</CardTitle>
          </div>
          <Badge tone="neutral">writable</Badge>
        </CardHeader>
        <CardBody className="space-y-5">
          <div>
            <Label htmlFor="vec-prov" hint="The one used by default">
              Vectoriser primary
            </Label>
            <Select
              id="vec-prov"
              value={String(v("vectorizer_provider", snapshot.vectorizer_provider))}
              onChange={(e) =>
                update(
                  "vectorizer_provider",
                  e.target.value as "vectorizer_ai" | "inkscape_potrace",
                )
              }
            >
              <option value="vectorizer_ai">
                Vectorizer.AI — paid, best for photo/illustration
                {snapshot.vectorizer_ai_api_id.set ? "" : "  (no key)"}
              </option>
              <option value="inkscape_potrace">
                Inkscape Potrace — local + free, great for logos
                {snapshot.inkscape_present ? "" : "  (not installed)"}
              </option>
            </Select>
          </div>

          <div>
            <Label htmlFor="vec-fallback" hint="Offered to the user on error — never auto">
              Vectoriser fallback
            </Label>
            <Select
              id="vec-fallback"
              value={String(v("vectorizer_fallback", snapshot.vectorizer_fallback ?? "none"))}
              onChange={(e) =>
                update(
                  "vectorizer_fallback",
                  e.target.value as "vectorizer_ai" | "inkscape_potrace" | "none",
                )
              }
            >
              <option value="none">None — fail loudly without a backup</option>
              <option value="vectorizer_ai">Vectorizer.AI</option>
              <option value="inkscape_potrace">Inkscape Potrace</option>
            </Select>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <div>
              <Label htmlFor="vec-mode" hint="Vectorizer.AI billing mode">
                Vectorizer.AI mode
              </Label>
              <Select
                id="vec-mode"
                value={String(v("vectorizer_ai_mode", snapshot.vectorizer_ai_mode))}
                onChange={(e) =>
                  update(
                    "vectorizer_ai_mode",
                    e.target.value as "production" | "test" | "preview",
                  )
                }
              >
                <option value="production">Production — 1 credit, clean SVG</option>
                <option value="test">Test — 0.1 credit, watermark, for dev</option>
                <option value="preview">Preview — free, low-res</option>
              </Select>
              <p className="mt-1.5 text-xs text-ink-500">
                Switch to <code className="font-mono">test</code> while iterating
                on the UI to save credits; flip back to{" "}
                <code className="font-mono">production</code> for press output.
              </p>
            </div>
            <div>
              <Label htmlFor="vec-timeout" hint="Vectorizer.AI + Inkscape">
                Vector timeout (seconds)
              </Label>
              <Input
                id="vec-timeout"
                type="number"
                min={10}
                max={600}
                step={5}
                value={Number(
                  v("vectorizer_timeout_s", snapshot.vectorizer_timeout_s),
                )}
                onChange={(e) =>
                  update("vectorizer_timeout_s", Number(e.target.value))
                }
              />
            </div>
          </div>

          <div>
            <Label htmlFor="seg-prov" hint="SAM-2 for layered PSD (Tier B)">
              Segmentation provider
            </Label>
            <Select
              id="seg-prov"
              value={String(v("segmentation_provider", snapshot.segmentation_provider))}
              onChange={(e) =>
                update(
                  "segmentation_provider",
                  e.target.value as
                    | "replicate"
                    | "self_hosted"
                    | "sam3"
                    | "none",
                )
              }
            >
              <option value="none">Off — skip layered PSD</option>
              <option value="replicate">
                Replicate SAM-2 — paid (~$0.01/img)
                {snapshot.replicate_api_token.set ? "" : "  (no token)"}
              </option>
              <option value="self_hosted">
                Self-hosted (generic SAM-2/3 contract)
                {snapshot.segmentation_self_hosted_url ? "" : "  (URL missing)"}
              </option>
              <option value="sam3">
                SAM 3.1 self-hosted — image, concept-promptable
                {snapshot.sam3_endpoint_present ? "" : "  (URL missing)"}
              </option>
            </Select>
            <p className="mt-1.5 text-xs text-ink-500">
              <strong>SAM 3.1</strong> returns labelled, scored masks when
              you provide a concept prompt — Tier B PSD layers become{" "}
              <code className="font-mono">logo</code>,{" "}
              <code className="font-mono">wordmark</code>, …{" "}
              instead of <code className="font-mono">sam2_layer_01</code>.
              See <code className="font-mono">docs/SAM_UPGRADE.md</code> for
              the wire contract your DGX endpoint must implement.
            </p>
          </div>

          <div>
            <Label htmlFor="seg-url" hint="Generic SAM-2/3 wire contract">
              Self-hosted segmentation URL
            </Label>
            <Input
              id="seg-url"
              type="url"
              placeholder="https://your-dgx-spark:9000/segment"
              value={String(
                v(
                  "segmentation_self_hosted_url",
                  snapshot.segmentation_self_hosted_url ?? "",
                ),
              )}
              onChange={(e) => update("segmentation_self_hosted_url", e.target.value)}
            />
            <p className="mt-1.5 text-xs text-ink-500">
              The bearer token (if any) lives in{" "}
              <code className="font-mono">FORME_SEGMENTATION_SELF_HOSTED_TOKEN</code>{" "}
              — edit it directly in <code className="font-mono">.env</code>.
            </p>
          </div>

          <div>
            <Label htmlFor="sam3-url" hint="SAM 3.1 image inference endpoint">
              SAM 3.1 endpoint URL
            </Label>
            <Input
              id="sam3-url"
              type="url"
              placeholder="https://your-dgx-spark:9000/sam3/image"
              value={String(
                v("sam3_endpoint_url", snapshot.sam3_endpoint_url ?? ""),
              )}
              onChange={(e) => update("sam3_endpoint_url", e.target.value)}
            />
            <p className="mt-1.5 text-xs text-ink-500">
              Bearer token in{" "}
              <code className="font-mono">FORME_SAM3_ENDPOINT_TOKEN</code> — edit
              in <code className="font-mono">.env</code>. Required when{" "}
              <em>Segmentation provider</em> is set to{" "}
              <code className="font-mono">sam3</code>.
            </p>
          </div>

          <div>
            <Label
              htmlFor="sam3-prompt"
              hint="Comma-separated concepts — leave blank for AMG (anonymous masks)"
            >
              SAM 3.1 concept prompt
            </Label>
            <Input
              id="sam3-prompt"
              type="text"
              placeholder="logo, wordmark, bottle, label background"
              value={String(
                v("sam3_text_prompt", snapshot.sam3_text_prompt ?? ""),
              )}
              onChange={(e) => update("sam3_text_prompt", e.target.value)}
            />
            <p className="mt-1.5 text-xs text-ink-500">
              When set, SAM 3.1 runs in <em>text mode</em> and returns one
              mask per detected instance, named with the matched concept.
              Blank ⇒ automatic-mask-generation (every shape, anonymous
              names).
            </p>
          </div>
        </CardBody>
      </Card>

      {/* ─── Model + cost ─── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-clay-600" />
            <CardTitle>Model &amp; cost</CardTitle>
          </div>
        </CardHeader>
        <CardBody className="space-y-5">
          <div>
            <Label htmlFor="model" hint="The gpt-image-2 snapshot pin">
              Image model
            </Label>
            <Input
              id="model"
              value={String(v("openai_image_model", snapshot.openai_image_model))}
              onChange={(e) => update("openai_image_model", e.target.value)}
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <div>
              <Label htmlFor="markup" hint="Applied on top of provider cost">
                Markup percent
              </Label>
              <Input
                id="markup"
                type="number"
                min={0}
                max={1000}
                step={1}
                value={Number(
                  v("pricing_markup_percent", snapshot.pricing_markup_percent),
                )}
                onChange={(e) =>
                  update("pricing_markup_percent", Number(e.target.value))
                }
              />
            </div>
            <div>
              <Label htmlFor="timeout" hint="Per OpenAI request">
                Image timeout (seconds)
              </Label>
              <Input
                id="timeout"
                type="number"
                min={10}
                max={600}
                step={5}
                value={Number(
                  v("image_generation_timeout_s", snapshot.image_generation_timeout_s),
                )}
                onChange={(e) =>
                  update("image_generation_timeout_s", Number(e.target.value))
                }
              />
            </div>
          </div>
        </CardBody>
      </Card>

      {/* ─── PSD tiers (B + C) ─── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Layers size={16} className="text-clay-600" />
            <CardTitle>PSD tiers</CardTitle>
          </div>
          <Badge tone={snapshot.tier_c_enabled ? "sage" : "neutral"}>
            Tier C {snapshot.tier_c_enabled ? "on" : "off"}
          </Badge>
        </CardHeader>
        <CardBody className="space-y-5">
          <div>
            <Label htmlFor="sam2" hint="Replicate model identifier or version hash">
              SAM-2 model (Replicate)
            </Label>
            <Input
              id="sam2"
              value={String(v("replicate_sam2_model", snapshot.replicate_sam2_model))}
              onChange={(e) => update("replicate_sam2_model", e.target.value)}
              placeholder="meta/sam-2"
            />
            <p className="mt-1.5 text-xs text-ink-500">
              Powers Tier B layered PSD. Override if you want a community port
              or a pinned version hash.
            </p>
          </div>

          <div>
            <Label htmlFor="seg-timeout" hint="Per SAM-2 request">
              Segmentation timeout (seconds)
            </Label>
            <Input
              id="seg-timeout"
              type="number"
              min={10}
              max={600}
              step={5}
              value={Number(
                v("segmentation_timeout_s", snapshot.segmentation_timeout_s),
              )}
              onChange={(e) =>
                update("segmentation_timeout_s", Number(e.target.value))
              }
            />
          </div>

          <div className="border-t border-ink-200/70 pt-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-sm font-medium text-ink-800">
                  Tier C — editable text layers
                </div>
                <p className="text-xs text-ink-500 mt-0.5 max-w-md">
                  Layers Tier B + adds Tesseract-OCR'd text regions as
                  named layers + a JSON sidecar. Off by default — toggle
                  on after verifying Tesseract is installed below.
                </p>
              </div>
              <label className="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center">
                <input
                  type="checkbox"
                  className="peer sr-only"
                  checked={Boolean(v("tier_c_enabled", snapshot.tier_c_enabled))}
                  onChange={(e) => update("tier_c_enabled", e.target.checked)}
                />
                <span className="absolute inset-0 rounded-full bg-ink-200 transition-colors peer-checked:bg-sage-600" />
                <span className="absolute left-1 h-4 w-4 rounded-full bg-white shadow transition-transform peer-checked:translate-x-5" />
              </label>
            </div>
          </div>

          <div>
            <Label htmlFor="tess-cmd" hint="Used for Tier C OCR">
              Tesseract CLI path
            </Label>
            <div className="flex items-center gap-3">
              <Input
                id="tess-cmd"
                value={String(v("tesseract_cmd", snapshot.tesseract_cmd))}
                onChange={(e) => update("tesseract_cmd", e.target.value)}
                className="flex-1"
                placeholder="tesseract"
              />
              <Badge tone={snapshot.tesseract_present ? "sage" : "warning"}>
                {snapshot.tesseract_present ? "found" : "missing"}
              </Badge>
            </div>
            <p className="mt-1.5 text-xs text-ink-500">
              Install with <code className="font-mono">brew install tesseract</code>{" "}
              if missing.
            </p>
          </div>

          <div>
            <Label htmlFor="tess-lang" hint="e.g. 'eng', 'eng+spa', 'fra'">
              Tesseract languages
            </Label>
            <Input
              id="tess-lang"
              value={String(v("tesseract_lang", snapshot.tesseract_lang))}
              onChange={(e) => update("tesseract_lang", e.target.value)}
              placeholder="eng"
            />
          </div>
        </CardBody>
      </Card>

      {/* ─── Print PDF/X-4 ─── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Printer size={16} className="text-clay-600" />
            <CardTitle>Print PDF/X-4</CardTitle>
          </div>
          <Badge tone={snapshot.print_icc_present ? "sage" : "warning"}>
            ICC {snapshot.print_icc_present ? "loaded" : "missing"}
          </Badge>
        </CardHeader>
        <CardBody className="space-y-5">
          <div>
            <Label htmlFor="icc-path" hint="Absolute path to a .icc file">
              CMYK ICC profile path
            </Label>
            <div className="flex items-center gap-3">
              <Input
                id="icc-path"
                value={String(v("print_icc_path", snapshot.print_icc_path))}
                onChange={(e) => update("print_icc_path", e.target.value)}
                className="flex-1"
                placeholder="/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc"
              />
              <Badge tone={snapshot.print_icc_present ? "sage" : "warning"}>
                {snapshot.print_icc_present ? "found" : "missing"}
              </Badge>
            </div>
            <p className="mt-1.5 text-xs text-ink-500">
              The PDF's <code className="font-mono">OutputIntent</code> embeds
              this profile. Swap for{" "}
              <code className="font-mono">ISOcoated_v2_300_eci.icc</code> (free
              from <a className="underline" href="https://www.eci.org" target="_blank" rel="noreferrer">eci.org</a>) for European press-grade colour.
              If the file is missing Forme falls back to Pillow's baseline RGB→CMYK conversion.
            </p>
          </div>

          <div>
            <Label htmlFor="icc-name" hint="Shown to print shops in the PDF metadata">
              ICC profile label
            </Label>
            <Input
              id="icc-name"
              value={String(v("print_icc_name", snapshot.print_icc_name))}
              onChange={(e) => update("print_icc_name", e.target.value)}
              placeholder="Generic CMYK"
            />
          </div>
        </CardBody>
      </Card>

      {/* ─── CDR export (slice 7) ─── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Box size={16} className="text-clay-600" />
            <CardTitle>CDR export · CorelDRAW</CardTitle>
          </div>
          <Badge
            tone={
              Boolean(v("cdr_enabled", snapshot.cdr_enabled))
                ? "sage"
                : "neutral"
            }
          >
            {Boolean(v("cdr_enabled", snapshot.cdr_enabled))
              ? "enabled"
              : "disabled"}
          </Badge>
        </CardHeader>
        <CardBody className="space-y-5">
          {/* Master toggle — top of card, always visible */}
          <label
            htmlFor="cdr-enabled"
            className="flex items-start gap-3 cursor-pointer rounded-md border border-ink-200 bg-paper-50/50 px-3 py-2.5 hover:bg-paper-100/60"
          >
            <input
              id="cdr-enabled"
              type="checkbox"
              checked={Boolean(v("cdr_enabled", snapshot.cdr_enabled))}
              onChange={(e) => update("cdr_enabled", e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-ink-300 text-clay-600 focus:ring-clay-500"
            />
            <div className="flex-1">
              <span className="text-sm font-medium text-ink-800">
                Enable CDR exports
              </span>
              <p className="mt-0.5 text-xs text-ink-500">
                Off by default — most users don&apos;t have UniConvertor
                installed or a CloudConvert key. Flip on once at least one
                provider is configured below; the CDR button on variants is
                hidden while this is off.
              </p>
            </div>
          </label>

          <p className="text-xs text-ink-500">
            Inkscape&apos;s CLI <strong>cannot</strong> export CDR — only
            import it. Forme uses{" "}
            <strong>UniConvertor 2</strong> locally (free) or{" "}
            <strong>CloudConvert</strong> (paid, hosted). Fallback is{" "}
            <em>never</em> auto-invoked. CloudConvert offers a{" "}
            <strong>25 conversion-minutes/day free tier</strong> on
            production plus a fully free sandbox host — see the toggle
            below.
          </p>

          <div>
            <Label htmlFor="cdr-prov" hint="The default for new CDR exports">
              CDR primary
            </Label>
            <Select
              id="cdr-prov"
              value={String(v("cdr_provider", snapshot.cdr_provider))}
              onChange={(e) =>
                update(
                  "cdr_provider",
                  e.target.value as "cloudconvert" | "uniconvertor",
                )
              }
            >
              <option value="uniconvertor">
                UniConvertor — free, local
                {snapshot.uniconvertor_present ? "" : "  (not installed)"}
              </option>
              <option value="cloudconvert">
                CloudConvert — paid, hosted
                {snapshot.cloudconvert_api_key.set ? "" : "  (no API key)"}
              </option>
            </Select>
          </div>

          <div>
            <Label
              htmlFor="cdr-fallback"
              hint="Offered to the user on error — never auto"
            >
              CDR fallback
            </Label>
            <Select
              id="cdr-fallback"
              value={String(
                v("cdr_fallback", snapshot.cdr_fallback ?? "none"),
              )}
              onChange={(e) =>
                update(
                  "cdr_fallback",
                  e.target.value as "cloudconvert" | "uniconvertor" | "none",
                )
              }
            >
              <option value="none">None — fail loudly without a backup</option>
              <option value="cloudconvert">CloudConvert</option>
              <option value="uniconvertor">UniConvertor</option>
            </Select>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <div>
              <Label htmlFor="uni-path" hint="UniConvertor 2 CLI binary">
                UniConvertor path
              </Label>
              <div className="flex items-center gap-3">
                <Input
                  id="uni-path"
                  value={String(
                    v("uniconvertor_path", snapshot.uniconvertor_path),
                  )}
                  onChange={(e) => update("uniconvertor_path", e.target.value)}
                  className="flex-1"
                  placeholder="/opt/homebrew/bin/uniconvertor"
                />
                <Badge tone={snapshot.uniconvertor_present ? "sage" : "warning"}>
                  {snapshot.uniconvertor_present ? "found" : "missing"}
                </Badge>
              </div>
              <p className="mt-1.5 text-xs text-ink-500">
                Install from{" "}
                <a
                  className="underline"
                  href="https://sk1project.net/uc2/"
                  target="_blank"
                  rel="noreferrer"
                >
                  sK1 Project · UniConvertor 2
                </a>
                .
              </p>
            </div>
            <div>
              <Label htmlFor="cdr-timeout" hint="Either provider">
                CDR timeout (seconds)
              </Label>
              <Input
                id="cdr-timeout"
                type="number"
                min={10}
                max={600}
                step={5}
                value={Number(v("cdr_timeout_s", snapshot.cdr_timeout_s))}
                onChange={(e) => update("cdr_timeout_s", Number(e.target.value))}
              />
            </div>
          </div>

          {/* CloudConvert sandbox toggle — free for development */}
          <label
            htmlFor="cc-sandbox"
            className="flex items-start gap-3 cursor-pointer rounded-md border border-ink-200 bg-paper-50/50 px-3 py-2.5 hover:bg-paper-100/60"
          >
            <input
              id="cc-sandbox"
              type="checkbox"
              checked={Boolean(
                v("cloudconvert_sandbox", snapshot.cloudconvert_sandbox),
              )}
              onChange={(e) =>
                update("cloudconvert_sandbox", e.target.checked)
              }
              className="mt-0.5 h-4 w-4 rounded border-ink-300 text-clay-600 focus:ring-clay-500"
            />
            <div className="flex-1">
              <span className="text-sm font-medium text-ink-800">
                Use CloudConvert sandbox
              </span>
              <p className="mt-0.5 text-xs text-ink-500">
                Picks <strong>both</strong> the host (
                <code className="font-mono">api.sandbox.cloudconvert.com</code>{" "}
                vs <code className="font-mono">api.cloudconvert.com</code>) and
                which key to send. Forme keeps <em>two distinct slots</em> —{" "}
                <code className="font-mono">CLOUDCONVERT_SANDBOX_API_KEY</code>{" "}
                for sandbox,{" "}
                <code className="font-mono">CLOUDCONVERT_API_KEY</code> for
                live — because CloudConvert keys 401 against the wrong
                environment. Flip this toggle to swap; the rest is
                automatic.
              </p>
              <p className="mt-1 text-xs text-ink-500">
                Current state:{" "}
                {Boolean(
                  v("cloudconvert_sandbox", snapshot.cloudconvert_sandbox),
                )
                  ? snapshot.cloudconvert_sandbox_api_key.set
                    ? "sandbox key ✓"
                    : "⚠️ sandbox key missing"
                  : snapshot.cloudconvert_api_key.set
                    ? "live key ✓"
                    : "⚠️ live key missing"}
              </p>
            </div>
          </label>
        </CardBody>
      </Card>

      {/* ─── Credentials (read-only) ─── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <KeyRound size={16} className="text-clay-600" />
            <CardTitle>Credentials</CardTitle>
          </div>
          <Badge tone="warning">read-only · edit .env to rotate</Badge>
        </CardHeader>
        <CardBody className="space-y-3">
          <SecretRow label="OPENAI_API_KEY" secret={snapshot.openai_api_key} />
          <SecretRow label="REPLICATE_API_TOKEN" secret={snapshot.replicate_api_token} />
          <SecretRow
            label="VECTORIZER_AI_API_ID"
            secret={snapshot.vectorizer_ai_api_id}
          />
          <SecretRow
            label="VECTORIZER_AI_API_KEY"
            secret={snapshot.vectorizer_ai_api_key}
          />
          <SecretRow
            label="FORME_SEGMENTATION_SELF_HOSTED_TOKEN"
            secret={snapshot.segmentation_self_hosted_token}
          />
          <SecretRow
            label="FORME_SAM3_ENDPOINT_TOKEN"
            secret={snapshot.sam3_endpoint_token}
          />
          <SecretRow
            label="CLOUDCONVERT_API_KEY"
            secret={snapshot.cloudconvert_api_key}
          />
          <SecretRow
            label="CLOUDCONVERT_SANDBOX_API_KEY"
            secret={snapshot.cloudconvert_sandbox_api_key}
          />
          <p className="pt-2 text-xs text-ink-500">
            Rotate any of these by editing{" "}
            <code className="font-mono text-ink-700">{snapshot.env_file}</code>.
            The backend hot-reloads on file save.
          </p>
        </CardBody>
      </Card>

      {/* ─── Local tools + server ─── */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Cpu size={16} className="text-clay-600" />
            <CardTitle>Local tools &amp; server</CardTitle>
          </div>
        </CardHeader>
        <CardBody className="space-y-5">
          <div>
            <Label htmlFor="inkscape" hint="Used for Potrace vector fallback + CDR">
              Inkscape CLI path
            </Label>
            <div className="flex items-center gap-3">
              <Input
                id="inkscape"
                value={String(v("inkscape_path", snapshot.inkscape_path))}
                onChange={(e) => update("inkscape_path", e.target.value)}
                className="flex-1"
              />
              <Badge tone={snapshot.inkscape_present ? "sage" : "warning"}>
                {snapshot.inkscape_present ? "found" : "missing"}
              </Badge>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <div>
              <Label htmlFor="log" hint="Backend verbosity">
                Log level
              </Label>
              <Select
                id="log"
                value={String(v("log_level", snapshot.log_level))}
                onChange={(e) =>
                  update(
                    "log_level",
                    e.target.value as "debug" | "info" | "warning" | "error",
                  )
                }
              >
                <option value="debug">debug</option>
                <option value="info">info</option>
                <option value="warning">warning</option>
                <option value="error">error</option>
              </Select>
            </div>
            <ReadOnlyRow
              label="Workspaces directory"
              value={snapshot.workspaces_dir}
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <ReadOnlyRow label="Database path" value={snapshot.db_path} />
            <ReadOnlyRow label="Host" value={`${snapshot.host}:${snapshot.port}`} />
          </div>

          <ReadOnlyRow
            label="Allowed CORS origins"
            value={snapshot.cors_origins.join(", ")}
          />

          <ReadOnlyRow
            label="ENV file location"
            value={snapshot.env_file}
            mono
          />
        </CardBody>
      </Card>

      {/* ─── Save bar ─── */}
      <div
        className={cn(
          "sticky bottom-4 z-30 rounded-2xl border px-6 py-4 flex items-center justify-between gap-4 transition-all duration-300 backdrop-blur-lg shadow-[0_8px_32px_rgba(12,10,9,0.08)]",
          dirty || error || saved
            ? "opacity-100 border-clay-200/80 bg-white/95 shadow-[0_12px_40px_rgba(215,88,39,0.08)]"
            : "opacity-85 border-ink-200/60 bg-white/80 shadow-[0_8px_32px_rgba(12,10,9,0.04)]",
        )}
      >
        <div className="text-xs font-semibold uppercase tracking-wider">
          {error ? (
            <span className="text-clay-800 flex items-center gap-2">
              <CircleAlert size={15} /> {error}
            </span>
          ) : saved ? (
            <span className="text-sage-750 flex items-center gap-2">
              <CheckCircle2 size={15} /> Saved &middot; backend will hot-reload.
            </span>
          ) : dirty ? (
            <span className="text-clay-650 bg-clay-50/60 border border-clay-200/40 px-2.5 py-1 rounded-md shadow-sm">
              {Object.keys(draft).length} unsaved change
              {Object.keys(draft).length === 1 ? "" : "s"}
            </span>
          ) : (
            <span className="text-ink-400 bg-paper-100/60 border border-ink-200/30 px-2.5 py-1 rounded-md">No unsaved changes</span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {dirty && !saving && (
            <Button variant="ghost" onClick={() => setDraft({})} className="text-xs font-bold uppercase tracking-wider">
              Discard
            </Button>
          )}
          <Button onClick={handleSave} disabled={!dirty || saving} loading={saving} className="shadow-md hover:shadow-lg text-xs font-bold uppercase tracking-wider">
            {saving ? null : <Save size={13} />}
            Save changes
          </Button>
        </div>
      </div>
    </div>
  );
}

function SecretRow({
  label,
  secret,
}: {
  label: string;
  secret: SecretField;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-ink-200 bg-paper-50/60 px-3 py-2.5 min-w-0">
      {/* Label can wrap if needed but stays in its column */}
      <span className="font-mono text-xs text-ink-700 truncate min-w-0 flex-shrink">
        {label}
      </span>
      <span className="flex items-center gap-2 min-w-0 flex-shrink-0">
        {secret.set ? (
          <>
            <Badge tone="sage">set</Badge>
            <code
              className="font-mono text-xs text-ink-500 max-w-[160px] truncate inline-block align-middle"
              title={secret.preview ?? undefined}
            >
              {secret.preview}
            </code>
          </>
        ) : (
          <Badge tone="warning">missing</Badge>
        )}
      </span>
    </div>
  );
}

function ReadOnlyRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-ink-700">{label}</div>
      <div
        className={cn(
          "rounded-lg border border-ink-200/60 bg-paper-50/40 px-3.5 py-2.5 text-xs text-ink-600 break-all shadow-sm",
          mono && "font-mono",
        )}
      >
        {value || "—"}
      </div>
    </div>
  );
}

// Required for icon import to render this file as a client component proper.
export const _icons = ServerCog;
