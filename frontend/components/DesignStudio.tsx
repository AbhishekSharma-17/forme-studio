"use client";

import {
  Box,
  ChevronDown,
  CircleAlert,
  Download,
  FileImage,
  FileText,
  Loader2,
  PauseCircle,
  Pencil,
  Printer,
  Shapes,
  Sparkles,
  Sprout,
  Type,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ComposeDialog } from "@/components/ComposeDialog";
import { ReferencesPanel } from "@/components/ReferencesPanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { Label, Select, Textarea } from "@/components/ui/Field";
import {
  ApiError,
  api,
  type Asset,
  type CdrProvider,
  type ProvidersSelected,
  type PsdTier,
  type TierAvailability,
  type VectorProvider,
  type Workspace,
} from "@/lib/api";
import { streamPost, type SseEvent } from "@/lib/sse";
import { cn, formatDate } from "@/lib/utils";

type Quality = "low" | "medium" | "high" | "auto";
type StudioMode = "generate" | "edit";
type StudioState = "idle" | "streaming" | "error";

interface Props {
  workspace: Workspace;
  initialGenerations: Asset[];
  initialReferences: Asset[];
  initialExports: Asset[];
  canGenerate: boolean;
  tiers: TierAvailability;
  providers: ProvidersSelected;
  /** When false, the CDR button is hidden from variant tiles and the
   *  gallery. Comes from /api/health → capabilities.cdr_enabled, which
   *  reflects the FORME_CDR_ENABLED master toggle. */
  cdrEnabled: boolean;
}

interface VariantState {
  index: number;
  partial_b64?: string;
  asset?: Asset;
}

interface CostInfo {
  provider_cost_usd: number;
  user_cost_usd: number;
  markup_percent: number;
}

const N_OPTIONS = [1, 2, 3, 4] as const;
const QUALITY_OPTIONS: { value: Quality; label: string; hint: string }[] = [
  { value: "low", label: "Draft", hint: "Fast preview" },
  { value: "medium", label: "Medium", hint: "Balanced" },
  { value: "high", label: "High", hint: "Production" },
  { value: "auto", label: "Auto", hint: "Let OpenAI choose" },
];

const EXPECTED_SECONDS: Record<string, number> = {
  low: 12,
  medium: 28,
  high: 55,
  auto: 35,
};

export function DesignStudio({
  workspace,
  initialGenerations,
  initialReferences,
  initialExports,
  canGenerate,
  tiers,
  providers,
  cdrEnabled,
}: Props) {
  const [prompt, setPrompt] = useState(workspace.description ?? "");
  const [n, setN] = useState(1);
  const [quality, setQuality] = useState<Quality>("high");
  const [mode, setMode] = useState<StudioMode>("generate");
  const [editBase, setEditBase] = useState<Asset | null>(null);
  const [selectedRefIds, setSelectedRefIds] = useState<number[]>([]);

  const [state, setState] = useState<StudioState>("idle");
  const [variants, setVariants] = useState<VariantState[]>([]);
  const [cost, setCost] = useState<CostInfo | null>(null);
  const [generations, setGenerations] = useState<Asset[]>(initialGenerations);
  const [references, setReferences] = useState<Asset[]>(initialReferences);
  const [exports, setExports] = useState<Asset[]>(initialExports);
  // Compose-dialog state — when set, the modal opens for that asset.
  const [composingAsset, setComposingAsset] = useState<Asset | null>(null);
  const [exporting, setExporting] = useState<Record<number, boolean>>({});
  const [exportError, setExportError] = useState<string | null>(null);
  // After a vector export fails, surface a "Try with <fallback>?" affordance
  // — never auto-retry. Cleared on the next vector call (success or failure).
  const [vectorRetry, setVectorRetry] = useState<{
    assetId: number;
    fallback: VectorProvider;
  } | null>(null);
  // Same shape for CDR — separate slot because the CDR pipeline has its
  // own primary + fallback, independent of the vector stage.
  const [cdrRetry, setCdrRetry] = useState<{
    assetId: number;
    fallback: CdrProvider;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const cdrPrimary = providers.cdr_primary;
  const cdrFallback = providers.cdr_fallback;
  const [elapsed, setElapsed] = useState(0);
  const startRef = useRef<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Elapsed-time ticker
  useEffect(() => {
    if (state !== "streaming") return;
    const tick = () =>
      setElapsed(((Date.now() - (startRef.current ?? Date.now())) / 1000));
    tick();
    const id = window.setInterval(tick, 200);
    return () => window.clearInterval(id);
  }, [state]);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // Stash the generate-mode prompt so exiting edit mode restores it.
  const generatePromptRef = useRef("");

  function enterEditMode(base: Asset) {
    if (mode === "generate") generatePromptRef.current = prompt;
    setMode("edit");
    setEditBase(base);
    // Edit instructions are *diffs*, not the original brief. Start empty
    // so the user is forced to write the delta; show the original brief
    // as a read-only caption under the textarea for reference.
    setPrompt("");
    setVariants([]);
    setError(null);
  }

  function exitEditMode() {
    setMode("generate");
    setEditBase(null);
    setPrompt(generatePromptRef.current);
    setVariants([]);
  }

  async function handleSubmit() {
    if (!canGenerate || state === "streaming") return;
    const trimmed = prompt.trim();
    if (trimmed.length < 4) {
      setError("Brief must be at least 4 characters.");
      return;
    }
    if (mode === "edit" && !editBase) {
      setError("No base variant selected.");
      return;
    }

    // Generate routes through images.edit on the server when references
    // are selected — gpt-image-2 can take up to 16 of them.
    const url =
      mode === "edit"
        ? api.editStreamUrl(workspace.slug)
        : api.generateStreamUrl(workspace.slug);
    const body =
      mode === "edit" && editBase
        ? {
            prompt: trimmed,
            n,
            quality,
            base_asset_id: editBase.id,
            reference_asset_ids: selectedRefIds,
          }
        : {
            prompt: trimmed,
            n,
            quality,
            reference_asset_ids: selectedRefIds,
          };

    setState("streaming");
    setError(null);
    setCost(null);
    setVariants(Array.from({ length: n }, (_, i) => ({ index: i })));
    setElapsed(0);
    startRef.current = Date.now();
    abortRef.current = new AbortController();

    await streamPost({
      url,
      body,
      signal: abortRef.current.signal,
      onEvent: (ev: SseEvent) => {
        if (ev.event === "partial") {
          const idx = Number(ev.data.variant_index ?? 0);
          const b64 = String(ev.data.image_b64 ?? "");
          setVariants((prev) =>
            prev.map((v) => (v.index === idx ? { ...v, partial_b64: b64 } : v)),
          );
        } else if (ev.event === "asset") {
          const idx = Number(ev.data.variant_index ?? 0);
          const asset = ev.data.asset as Asset;
          setVariants((prev) =>
            prev.map((v) =>
              v.index === idx ? { ...v, partial_b64: undefined, asset } : v,
            ),
          );
        } else if (ev.event === "cost") {
          setCost({
            provider_cost_usd: Number(ev.data.provider_cost_usd ?? 0),
            user_cost_usd: Number(ev.data.user_cost_usd ?? 0),
            markup_percent: Number(ev.data.markup_percent ?? 0),
          });
        } else if (ev.event === "error") {
          setError(String(ev.data.message ?? "Unknown error"));
          setState("error");
        } else if (ev.event === "done") {
          setState("idle");
          api
            .listAssets(workspace.slug, "generation")
            .then(setGenerations)
            .catch(() => {});
        }
      },
      onError: (err) => {
        if ((err as { name?: string }).name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
        setState("error");
      },
    });
  }

  function handleCancel() {
    abortRef.current?.abort();
    setState("idle");
  }

  function handleUploaded(added: Asset[]) {
    setReferences((prev) => [...added, ...prev]);
  }

  async function handleExportPsd(
    asset: Asset,
    tier: PsdTier = "A",
    colorSpace: "CMYK" | "RGB" = "CMYK",
  ) {
    // Composable is interactive (multi-step dialog), not a direct export.
    // Intercept before any API call and open the dialog instead.
    if (tier === "COMPOSABLE") {
      setComposingAsset(asset);
      return;
    }
    if (exporting[asset.id]) return;
    setExporting((prev) => ({ ...prev, [asset.id]: true }));
    setExportError(null);
    try {
      const res = await api.exportPsd(workspace.slug, {
        source_asset_id: asset.id,
        tier,
        color_space: colorSpace,
        dpi: 300,
      });
      setExports((prev) => [res.asset, ...prev]);
      downloadAsset(res.asset);
    } catch (err) {
      setExportError(
        err instanceof ApiError ? err.message : "PSD export failed.",
      );
    } finally {
      setExporting((prev) => {
        const next = { ...prev };
        delete next[asset.id];
        return next;
      });
    }
  }

  async function handleExportPdf(asset: Asset) {
    if (exporting[asset.id]) return;
    setExporting((prev) => ({ ...prev, [asset.id]: true }));
    setExportError(null);
    try {
      const res = await api.exportPdf(workspace.slug, {
        source_asset_id: asset.id,
        dpi: 300,
        trim_marks: true,
        registration_marks: true,
      });
      setExports((prev) => [res.asset, ...prev]);
      downloadAsset(res.asset);
    } catch (err) {
      setExportError(
        err instanceof ApiError ? err.message : "PDF export failed.",
      );
    } finally {
      setExporting((prev) => {
        const next = { ...prev };
        delete next[asset.id];
        return next;
      });
    }
  }

  async function handleExportCdr(asset: Asset, cdrProvider?: CdrProvider) {
    if (exporting[asset.id]) return;
    setExporting((prev) => ({ ...prev, [asset.id]: true }));
    setExportError(null);
    setVectorRetry(null);
    setCdrRetry(null);
    try {
      const res = await api.exportCdr(workspace.slug, {
        source_asset_id: asset.id,
        ...(cdrProvider ? { cdr_provider: cdrProvider } : {}),
      });
      setExports((prev) => [res.asset, ...prev]);
      downloadAsset(res.asset);
    } catch (err) {
      const detail =
        err instanceof ApiError ? err.message : "CDR export failed.";
      setExportError(detail);
      // Non-auto fallback. The error message may reference either stage
      // (vector or CDR). For the CDR retry, we just swap to the
      // configured alternate of whichever CDR provider was last used.
      const triedCdr = cdrProvider ?? cdrPrimary;
      const fallback = cdrFallback;
      if (fallback && fallback !== triedCdr) {
        setCdrRetry({ assetId: asset.id, fallback });
      }
    } finally {
      setExporting((prev) => {
        const next = { ...prev };
        delete next[asset.id];
        return next;
      });
    }
  }

  async function handleExportVector(asset: Asset, provider?: VectorProvider) {
    if (exporting[asset.id]) return;
    setExporting((prev) => ({ ...prev, [asset.id]: true }));
    setExportError(null);
    setVectorRetry(null);
    try {
      const res = await api.exportVector(workspace.slug, {
        source_asset_id: asset.id,
        ...(provider ? { provider } : {}),
      });
      setExports((prev) => [res.asset, ...prev]);
      downloadAsset(res.asset);
    } catch (err) {
      const detail =
        err instanceof ApiError ? err.message : "Vector export failed.";
      setExportError(detail);
      // Non-auto fallback: if a different provider is configured and we
      // didn't already just try it, surface a retry affordance.
      const tried = provider ?? providers.vectorizer_primary;
      const fallback = providers.vectorizer_fallback;
      if (fallback && fallback !== tried) {
        setVectorRetry({ assetId: asset.id, fallback });
      }
    } finally {
      setExporting((prev) => {
        const next = { ...prev };
        delete next[asset.id];
        return next;
      });
    }
  }

  function downloadAsset(asset: Asset) {
    const url = api.assetFileUrl(workspace.slug, asset.id);
    const a = document.createElement("a");
    a.href = url;
    a.download = asset.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  const expected = EXPECTED_SECONDS[quality] ?? 35;
  const progress = state === "streaming" ? Math.min(1, elapsed / expected) : 0;
  const completedVariants = variants.filter((v) => v.asset).length;
  const submitLabel = mode === "edit" ? "Run edit" : "Generate";
  const SubmitIcon = mode === "edit" ? Pencil : Sparkles;

  return (
    <div className="space-y-8">
      {/* ============ References panel ============ */}
      <ReferencesPanel
        workspace={workspace}
        references={references}
        selectedIds={selectedRefIds}
        onSelectedChange={setSelectedRefIds}
        onUploaded={handleUploaded}
      />

      {/* ============ Control panel ============ */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Sprout size={16} className="text-clay-600" />
            <CardTitle>Design studio</CardTitle>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone="neutral">
              {(workspace.specs as { generation_size?: string }).generation_size ?? "—"}
            </Badge>
            <Badge tone="clay">gpt-image-2</Badge>
          </div>
        </CardHeader>
        <CardBody className="space-y-5">
          {!canGenerate && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-900 flex items-start gap-2">
              <CircleAlert size={16} className="mt-0.5 shrink-0" />
              <span>
                <strong>OPENAI_API_KEY isn&apos;t configured.</strong> Add it to{" "}
                <code className="font-mono text-xs">backend/.env</code> and reload.
              </span>
            </div>
          )}

          {/* References-in-generate-mode hint */}
          {mode === "generate" && selectedRefIds.length > 0 && (
            <div className="rounded-md border border-sage-200 bg-sage-50 px-3 py-2 text-xs text-sage-800 flex items-center justify-between">
              <span>
                <strong>{selectedRefIds.length}</strong> reference
                {selectedRefIds.length === 1 ? "" : "s"} selected — gpt-image-2 will
                use {selectedRefIds.length === 1 ? "it" : "them"} as visual context.
              </span>
              <button
                type="button"
                onClick={() => setSelectedRefIds([])}
                className="text-sage-700 hover:text-sage-900 font-medium"
              >
                Clear
              </button>
            </div>
          )}

          {/* Over-cap warning */}
          {selectedRefIds.length > 16 && (
            <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-xs text-clay-800">
              gpt-image-2 caps references at 16 — please deselect{" "}
              {selectedRefIds.length - 16} before running.
            </div>
          )}

          {/* Edit-mode banner */}
          {mode === "edit" && editBase && (
            <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2.5 flex items-center gap-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={api.assetFileUrl(workspace.slug, editBase.id)}
                alt="Base variant"
                className="h-12 w-12 rounded object-cover border border-clay-200"
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-clay-900 font-medium">
                  Editing <span className="font-mono">{editBase.filename}</span>
                </p>
                <p className="text-xs text-clay-700">
                  Output will reference this variant +{" "}
                  {selectedRefIds.length === 0
                    ? "no extra references"
                    : `${selectedRefIds.length} selected reference${selectedRefIds.length === 1 ? "" : "s"}`}
                  .
                </p>
              </div>
              <button
                type="button"
                onClick={exitEditMode}
                className="text-clay-700 hover:text-clay-900 p-1"
                title="Cancel edit; return to plain generate"
              >
                <X size={16} />
              </button>
            </div>
          )}

          <div>
            <Label htmlFor="brief" hint={mode === "edit" ? "What should change? (the diff, not the whole brief)" : "What does this product need to communicate?"}>
              {mode === "edit" ? "Edit instruction" : "Brief"}
            </Label>
            <Textarea
              id="brief"
              rows={4}
              placeholder={
                mode === "edit"
                  ? "e.g. Keep the layout. Change the background to deep sage. Make the wordmark thinner."
                  : "e.g. Minimal label for a 250 ml sage-scented lotion. Warm cream background, hand-drawn botanical line art."
              }
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={state === "streaming"}
            />
            {mode === "edit" && editBase?.prompt && (
              <p className="mt-2 rounded-md border border-ink-200 bg-paper-50 px-3 py-2 text-xs text-ink-600">
                <span className="font-medium text-ink-500 uppercase tracking-wide text-[10px]">
                  Original brief
                </span>
                <br />
                <span className="italic">{editBase.prompt}</span>
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <Label htmlFor="n-picker">Variants</Label>
              <div
                id="n-picker"
                role="radiogroup"
                className="flex gap-1 rounded-lg border border-ink-200 bg-paper-100/50 p-1"
              >
                {N_OPTIONS.map((opt) => {
                  const active = opt === n;
                  return (
                    <button
                      key={opt}
                      role="radio"
                      type="button"
                      aria-checked={active}
                      onClick={() => setN(opt)}
                      disabled={state === "streaming"}
                      className={cn(
                        "flex-1 rounded-md px-2 py-1.5 text-xs font-semibold uppercase tracking-wider transition-all duration-200",
                        active
                          ? "bg-ink-900 text-paper-50 shadow-sm"
                          : "text-ink-500 hover:bg-white hover:text-ink-900 hover:shadow-sm/10",
                      )}
                    >
                      {opt}
                    </button>
                  );
                })}
              </div>
            </div>

            <div>
              <Label htmlFor="quality">Quality</Label>
              <Select
                id="quality"
                value={quality}
                onChange={(e) => setQuality(e.target.value as Quality)}
                disabled={state === "streaming"}
              >
                {QUALITY_OPTIONS.map((q) => (
                  <option key={q.value} value={q.value}>
                    {q.label} — {q.hint}
                  </option>
                ))}
              </Select>
            </div>

            <div className="flex items-end justify-end md:col-span-1 col-span-2">
              {state === "streaming" ? (
                <Button variant="secondary" onClick={handleCancel} className="w-full md:w-auto">
                  <PauseCircle size={16} /> Cancel
                </Button>
              ) : (
                <Button
                  onClick={handleSubmit}
                  disabled={
                    !canGenerate ||
                    prompt.trim().length < 4 ||
                    (mode === "edit" && !editBase) ||
                    selectedRefIds.length > 16
                  }
                  className="w-full md:w-auto"
                >
                  <SubmitIcon size={16} /> {submitLabel}
                </Button>
              )}
            </div>
          </div>

          {(state === "streaming" || error) && (
            <StatusStrip
              state={state}
              mode={mode}
              elapsed={elapsed}
              expected={expected}
              progress={progress}
              cost={cost}
              completed={completedVariants}
              total={variants.length}
              error={error}
            />
          )}
        </CardBody>
      </Card>

      {/* ============ This run ============ */}
      {variants.length > 0 && (
        <section>
          <h2 className="font-display text-xl text-ink-900 mb-3">
            {mode === "edit" ? "Edit results" : "This run"}
          </h2>
          <VariantsGrid
            workspace={workspace}
            variants={variants}
            onEdit={(asset) => {
              if (state === "streaming") return;
              enterEditMode(asset);
            }}
            onExportPsd={handleExportPsd}
            onExportPdf={handleExportPdf}
            onExportVector={handleExportVector}
            onExportCdr={handleExportCdr}
            cdrEnabled={cdrEnabled}
            tiers={tiers}
            exporting={exporting}
          />
          {cost && (
            <p className="mt-2 text-xs text-ink-500 font-mono">
              Cost: ${cost.provider_cost_usd.toFixed(4)} provider
              {cost.markup_percent > 0 &&
                ` · $${cost.user_cost_usd.toFixed(4)} user (+${cost.markup_percent}%)`}
            </p>
          )}
        </section>
      )}

      {/* ============ Gallery ============ */}
      <section>
        <div className="flex items-end justify-between mb-3">
          <h2 className="font-display text-xl text-ink-900">Gallery</h2>
          {generations.length > 0 && (
            <span className="text-xs text-ink-500">
              {generations.length} generation{generations.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        {generations.length === 0 ? (
          <div className="rounded-xl border border-dashed border-ink-200 bg-white/60 p-10 text-center">
            <p className="text-ink-500">
              No generations yet. Write a brief above and hit{" "}
              <span className="font-medium text-ink-800">Generate</span>.
            </p>
          </div>
        ) : (
          <AssetGallery
            workspace={workspace}
            assets={generations}
            onEdit={enterEditMode}
            onExportPsd={handleExportPsd}
            onExportPdf={handleExportPdf}
            onExportVector={handleExportVector}
            onExportCdr={handleExportCdr}
            cdrEnabled={cdrEnabled}
            isStreaming={state === "streaming"}
            activeBaseId={editBase?.id}
            exporting={exporting}
            tiers={tiers}
          />
        )}
      </section>

      {/* ============ Exports ============ */}
      {(exports.length > 0 || exportError) && (
        <section>
          <div className="flex items-end justify-between mb-3">
            <h2 className="font-display text-xl text-ink-900">Exports</h2>
            {exports.length > 0 && (
              <span className="text-xs text-ink-500">
                {exports.length} file{exports.length === 1 ? "" : "s"} in{" "}
                <span className="font-mono">exports/</span>
              </span>
            )}
          </div>
          {exportError && (
            <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-sm text-clay-800 mb-3 flex items-start gap-2">
              <CircleAlert size={14} className="mt-0.5 shrink-0" />
              <div className="flex-1">
                <p>{exportError}</p>
                {vectorRetry && (() => {
                  const target =
                    generations.find((g) => g.id === vectorRetry.assetId) ??
                    variants.find((v) => v.asset?.id === vectorRetry.assetId)
                      ?.asset;
                  if (!target) return null;
                  const label =
                    vectorRetry.fallback === "vectorizer_ai"
                      ? "Vectorizer.AI"
                      : "Inkscape Potrace";
                  return (
                    <button
                      type="button"
                      onClick={() =>
                        handleExportVector(target, vectorRetry.fallback)
                      }
                      disabled={!!exporting[target.id]}
                      className={cn(
                        "mt-2 inline-flex items-center gap-1.5 rounded-md border border-clay-300 bg-white px-3 py-1 text-xs font-medium text-clay-800 hover:bg-clay-100",
                        exporting[target.id] && "cursor-not-allowed opacity-60",
                      )}
                    >
                      {exporting[target.id] ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <Shapes size={12} />
                      )}
                      Try with {label}
                    </button>
                  );
                })()}
                {cdrRetry && (() => {
                  const target =
                    generations.find((g) => g.id === cdrRetry.assetId) ??
                    variants.find((v) => v.asset?.id === cdrRetry.assetId)
                      ?.asset;
                  if (!target) return null;
                  const label =
                    cdrRetry.fallback === "cloudconvert"
                      ? "CloudConvert"
                      : "UniConvertor (local)";
                  return (
                    <button
                      type="button"
                      onClick={() => handleExportCdr(target, cdrRetry.fallback)}
                      disabled={!!exporting[target.id]}
                      className={cn(
                        "mt-2 ml-2 inline-flex items-center gap-1.5 rounded-md border border-clay-300 bg-white px-3 py-1 text-xs font-medium text-clay-800 hover:bg-clay-100",
                        exporting[target.id] && "cursor-not-allowed opacity-60",
                      )}
                    >
                      {exporting[target.id] ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <Box size={12} />
                      )}
                      Retry CDR via {label}
                    </button>
                  );
                })()}
              </div>
            </div>
          )}
          {exports.length > 0 && (
            <ExportsList workspace={workspace} exports={exports} />
          )}
        </section>
      )}

      {/* Compose dialog — opens when "Tier Composable" is picked */}
      {composingAsset && (
        <ComposeDialog
          workspace={workspace}
          sourceAsset={composingAsset}
          onClose={() => {
            setComposingAsset(null);
            // Refresh the exports list so the assembled PSD shows up.
            api
              .listAssets(workspace.slug, "export")
              .then(setExports)
              .catch(() => undefined);
          }}
        />
      )}
    </div>
  );
}

/* ---------------- subcomponents ---------------- */

function StatusStrip({
  state,
  mode,
  elapsed,
  expected,
  progress,
  cost,
  completed,
  total,
  error,
}: {
  state: StudioState;
  mode: StudioMode;
  elapsed: number;
  expected: number;
  progress: number;
  cost: CostInfo | null;
  completed: number;
  total: number;
  error: string | null;
}) {
  return (
    <div className="rounded-xl border border-ink-200 bg-paper-50/50 p-4 shadow-[inset_0_1px_2.5px_rgba(12,10,9,0.015)]">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-2.5 text-ink-700">
          {state === "streaming" ? (
            <>
              <Loader2 size={15} className="animate-spin text-clay-650" />
              <span className="font-medium">
                {mode === "edit" ? "Applying edits" : "Generating packaging canvas"} &middot; <span className="text-clay-650 font-bold">{completed}/{total}</span> done
              </span>
            </>
          ) : (
            <>
              <CircleAlert size={15} className="text-clay-600" />
              <span className="font-semibold text-clay-800">{error}</span>
            </>
          )}
        </div>
        <div className="font-mono text-[11px] font-semibold text-ink-600 bg-white border border-ink-200/50 px-2 py-0.5 rounded shadow-[0_1px_2px_rgba(12,10,9,0.01)]">
          {elapsed.toFixed(1)}s
          {state === "streaming" && (
            <span className="text-ink-400 font-normal"> / ~{expected}s</span>
          )}
        </div>
      </div>
      <div className="mt-3.5 h-1.5 overflow-hidden rounded-full bg-paper-200/80 border border-ink-200/20">
        <div
          className={cn(
            "h-full transition-all duration-300 ease-out rounded-full",
            state === "error" ? "bg-clay-600" : "bg-gradient-to-r from-clay-500 to-clay-600 animate-pulse",
          )}
          style={{
            width: state === "error" ? "100%" : `${Math.round(progress * 100)}%`,
          }}
        />
      </div>
      {cost && (
        <p className="mt-2.5 font-mono text-[10px] text-ink-400 uppercase tracking-wide">
          API cost incurred: <span className="font-semibold text-ink-750">${cost.provider_cost_usd.toFixed(4)}</span>
        </p>
      )}
    </div>
  );
}

function VariantsGrid({
  workspace,
  variants,
  onEdit,
  onExportPsd,
  onExportPdf,
  onExportVector,
  onExportCdr,
  cdrEnabled,
  tiers,
  exporting,
}: {
  workspace: Workspace;
  variants: VariantState[];
  onEdit: (asset: Asset) => void;
  onExportPsd: (asset: Asset, tier?: PsdTier) => void;
  onExportPdf: (asset: Asset) => void;
  onExportVector: (asset: Asset, provider?: VectorProvider) => void;
  onExportCdr: (asset: Asset, cdrProvider?: CdrProvider) => void;
  cdrEnabled: boolean;
  tiers: TierAvailability;
  exporting: Record<number, boolean>;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {variants.map((v) => (
        <VariantTile
          key={v.index}
          workspace={workspace}
          variant={v}
          onEdit={onEdit}
          onExportPsd={onExportPsd}
          onExportPdf={onExportPdf}
          onExportVector={onExportVector}
          onExportCdr={onExportCdr}
          cdrEnabled={cdrEnabled}
          tiers={tiers}
          exporting={exporting}
        />
      ))}
    </div>
  );
}

function VariantTile({
  workspace,
  variant,
  onEdit,
  onExportPsd,
  onExportPdf,
  onExportVector,
  onExportCdr,
  cdrEnabled,
  tiers,
  exporting,
}: {
  workspace: Workspace;
  variant: VariantState;
  onEdit: (asset: Asset) => void;
  onExportPsd: (asset: Asset, tier?: PsdTier) => void;
  onExportPdf: (asset: Asset) => void;
  onExportVector: (asset: Asset, provider?: VectorProvider) => void;
  onExportCdr: (asset: Asset, cdrProvider?: CdrProvider) => void;
  cdrEnabled: boolean;
  tiers: TierAvailability;
  exporting: Record<number, boolean>;
}) {
  const partialSrc = variant.partial_b64
    ? `data:image/png;base64,${variant.partial_b64}`
    : null;
  const finalSrc = variant.asset
    ? api.assetFileUrl(workspace.slug, variant.asset.id)
    : null;

  return (
    <div className="group relative overflow-hidden rounded-lg border border-ink-200 bg-paper-100 shadow-card">
      <div className="aspect-[2/3] flex items-center justify-center bg-paper-200">
        {finalSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={finalSrc}
            alt={`Variant ${variant.index + 1}`}
            className="h-full w-full object-cover"
          />
        ) : partialSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={partialSrc}
            alt={`Variant ${variant.index + 1} (partial)`}
            className="h-full w-full object-cover blur-sm opacity-90"
          />
        ) : (
          <div className="text-ink-300">
            <Loader2 size={20} className="animate-spin" />
          </div>
        )}
      </div>
      <div className="absolute top-2 left-2 flex items-center gap-2">
        <Badge tone="neutral">#{variant.index + 1}</Badge>
        {variant.asset ? (
          <Badge tone="sage">final</Badge>
        ) : partialSrc ? (
          <Badge tone="clay">partial</Badge>
        ) : (
          <Badge tone="neutral">queued</Badge>
        )}
      </div>
      {variant.asset && (
        <div className="absolute top-2 right-2 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <PsdTierMenu
            tiers={tiers}
            onPick={(tier) => onExportPsd(variant.asset!, tier)}
            disabled={!!exporting[variant.asset.id]}
            isExporting={!!exporting[variant.asset.id]}
          />
          <PdfExportButton
            onClick={() => onExportPdf(variant.asset!)}
            disabled={!!exporting[variant.asset.id]}
            isExporting={!!exporting[variant.asset.id]}
          />
          <VectorExportButton
            onClick={() => onExportVector(variant.asset!)}
            disabled={!!exporting[variant.asset.id]}
            isExporting={!!exporting[variant.asset.id]}
          />
          {cdrEnabled && (
            <CdrExportButton
              onClick={() => onExportCdr(variant.asset!)}
              disabled={!!exporting[variant.asset.id]}
              isExporting={!!exporting[variant.asset.id]}
            />
          )}
          <button
            type="button"
            onClick={() => onEdit(variant.asset!)}
            className="rounded-md bg-ink-900/85 text-paper-100 px-2 py-1 text-[11px] font-medium inline-flex items-center gap-1 hover:bg-ink-900"
            title="Iterate on this variant"
          >
            <Pencil size={12} /> Edit
          </button>
        </div>
      )}
      {variant.asset && (
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-ink-900/85 via-ink-900/40 to-transparent p-3 text-paper-100 opacity-0 group-hover:opacity-100 transition-opacity">
          <p className="text-xs font-mono opacity-80">{variant.asset.filename}</p>
          <p className="text-[11px] font-mono opacity-60">
            ${variant.asset.provider_cost_usd.toFixed(4)} · {variant.asset.image_size}
          </p>
        </div>
      )}
    </div>
  );
}

function AssetGallery({
  workspace,
  assets,
  onEdit,
  onExportPsd,
  onExportPdf,
  onExportVector,
  onExportCdr,
  cdrEnabled,
  isStreaming,
  activeBaseId,
  exporting,
  tiers,
}: {
  workspace: Workspace;
  assets: Asset[];
  onEdit: (asset: Asset) => void;
  onExportPsd: (asset: Asset, tier?: PsdTier) => void;
  onExportPdf: (asset: Asset) => void;
  onExportVector: (asset: Asset, provider?: VectorProvider) => void;
  onExportCdr: (asset: Asset, cdrProvider?: CdrProvider) => void;
  cdrEnabled: boolean;
  isStreaming: boolean;
  activeBaseId?: number;
  exporting: Record<number, boolean>;
  tiers: TierAvailability;
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-6">
      {assets.map((a) => {
        const isBase = activeBaseId === a.id;
        const isExporting = !!exporting[a.id];
        return (
          <div
            key={a.id}
            className={cn(
              "group relative overflow-hidden rounded-2xl border bg-white/70 shadow-sm transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_12px_32px_-10px_rgba(12,10,9,0.12)] hover:border-ink-300",
              isBase
                ? "border-clay-500 ring-4 ring-clay-500/10 shadow-md"
                : "border-ink-200/70",
            )}
          >
            <a
              href={api.assetFileUrl(workspace.slug, a.id)}
              target="_blank"
              rel="noreferrer"
              className="block"
            >
              <div className="aspect-[2/3] bg-paper-200 overflow-hidden">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={api.assetFileUrl(workspace.slug, a.id)}
                  alt={a.filename}
                  className="h-full w-full object-cover transition-all duration-500 group-hover:scale-[1.04] group-hover:rotate-[-0.5deg]"
                  loading="lazy"
                />
              </div>
            </a>
            <div
              className={cn(
                "absolute top-2.5 right-2.5 flex items-center gap-1.5 transition-all duration-300",
                isBase || isExporting ? "opacity-100 translate-y-0" : "opacity-0 translate-y-[-4px] group-hover:opacity-100 group-hover:translate-y-0",
              )}
            >
              <PsdTierMenu
                tiers={tiers}
                onPick={(tier) => onExportPsd(a, tier)}
                disabled={isStreaming || isExporting}
                isExporting={isExporting}
              />
              <PdfExportButton
                onClick={() => onExportPdf(a)}
                disabled={isStreaming || isExporting}
                isExporting={isExporting}
              />
              <VectorExportButton
                onClick={() => onExportVector(a)}
                disabled={isStreaming || isExporting}
                isExporting={isExporting}
              />
              {cdrEnabled && (
                <CdrExportButton
                  onClick={() => onExportCdr(a)}
                  disabled={isStreaming || isExporting}
                  isExporting={isExporting}
                />
              )}
              <button
                type="button"
                onClick={() => onEdit(a)}
                disabled={isStreaming || isExporting}
                className={cn(
                  "rounded-lg px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider inline-flex items-center gap-1 transition-all duration-200 shadow-sm",
                  isBase
                    ? "bg-clay-600 text-white border border-clay-700"
                    : "bg-ink-900/85 text-paper-100 hover:bg-ink-900 border border-ink-950",
                  (isStreaming || isExporting) && "cursor-not-allowed opacity-60",
                )}
                title="Use as base for an edit"
              >
                <Pencil size={11} />
                {isBase ? "Editing" : "Edit"}
              </button>
            </div>
            <div className="p-3.5 bg-white/50 border-t border-ink-100/50 rounded-b-2xl">
              <div className="flex items-center justify-between gap-2.5">
                <span className="font-mono text-[10.5px] text-ink-500 truncate">
                  {a.filename}
                </span>
                <span className="font-mono text-[10.5px] text-ink-800 font-bold bg-paper-200/50 px-1.5 py-0.5 rounded">
                  ${a.provider_cost_usd.toFixed(4)}
                </span>
              </div>
              <p className="mt-1 text-[9.5px] font-semibold text-ink-400 uppercase tracking-wide">
                {formatDate(a.created_at)} &middot; variant {a.variant_index + 1}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function PdfExportButton({
  onClick,
  disabled,
  isExporting,
}: {
  onClick: () => void;
  disabled: boolean;
  isExporting: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-md px-2 py-1 text-[11px] font-medium inline-flex items-center gap-1 transition-colors",
        "bg-ink-700 text-paper-100 hover:bg-ink-800",
        disabled && "cursor-not-allowed opacity-60",
      )}
      title="Export as PDF/X-4 (CMYK · 300 DPI · trim + bleed + marks)"
    >
      {isExporting ? (
        <Loader2 size={12} className="animate-spin" />
      ) : (
        <Printer size={12} />
      )}
      PDF
    </button>
  );
}

function VectorExportButton({
  onClick,
  disabled,
  isExporting,
}: {
  onClick: () => void;
  disabled: boolean;
  isExporting: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-md px-2 py-1 text-[11px] font-medium inline-flex items-center gap-1 transition-colors",
        "bg-clay-700 text-paper-100 hover:bg-clay-800",
        disabled && "cursor-not-allowed opacity-60",
      )}
      title="Vectorize as SVG (Vectorizer.AI primary · Inkscape Potrace fallback)"
    >
      {isExporting ? (
        <Loader2 size={12} className="animate-spin" />
      ) : (
        <Shapes size={12} />
      )}
      SVG
    </button>
  );
}

function CdrExportButton({
  onClick,
  disabled,
  isExporting,
}: {
  onClick: () => void;
  disabled: boolean;
  isExporting: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-md px-2 py-1 text-[11px] font-medium inline-flex items-center gap-1 transition-colors",
        "bg-sage-800 text-paper-100 hover:bg-sage-900",
        disabled && "cursor-not-allowed opacity-60",
      )}
      title="Export as CorelDRAW .cdr (UniConvertor local · CloudConvert paid)"
    >
      {isExporting ? (
        <Loader2 size={12} className="animate-spin" />
      ) : (
        <Box size={12} />
      )}
      CDR
    </button>
  );
}

function PsdTierMenu({
  tiers,
  onPick,
  disabled,
  isExporting,
}: {
  tiers: TierAvailability;
  onPick: (tier: PsdTier) => void;
  disabled: boolean;
  isExporting: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <div className="flex">
        <button
          type="button"
          onClick={() => onPick("A")}
          disabled={disabled}
          className={cn(
            "rounded-l-md px-2 py-1 text-[11px] font-medium inline-flex items-center gap-1 transition-colors",
            "bg-sage-700 text-paper-100 hover:bg-sage-800 border-r border-sage-800/40",
            disabled && "cursor-not-allowed opacity-60",
          )}
          title="Tier A — flat CMYK at 300 DPI"
        >
          {isExporting ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Download size={12} />
          )}
          PSD
        </button>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          disabled={disabled}
          className={cn(
            "rounded-r-md px-1.5 py-1 transition-colors",
            "bg-sage-700 text-paper-100 hover:bg-sage-800",
            disabled && "cursor-not-allowed opacity-60",
          )}
          aria-label="Choose PSD tier"
          aria-expanded={open}
        >
          <ChevronDown size={12} />
        </button>
      </div>

      {open && (
        <div className="absolute top-full right-0 mt-1 z-20 w-64 rounded-md border border-ink-200 bg-white shadow-card overflow-hidden">
          <TierMenuItem
            available={tiers.tier_a}
            label="Tier A · Flat"
            description="Single layer, CMYK, 300 DPI"
            icon={<Download size={14} />}
            onClick={() => {
              setOpen(false);
              onPick("A");
            }}
          />
          <TierMenuItem
            available={tiers.tier_a_ocr}
            label="Tier A+OCR · Editable text"
            description={
              tiers.tier_a_ocr
                ? "Flat + Tesseract text overlays + .ocr.json sidecar"
                : "Enable Tesseract + Tier A+OCR toggle in Settings"
            }
            icon={<Type size={14} />}
            onClick={() => {
              setOpen(false);
              if (tiers.tier_a_ocr) onPick("A+OCR");
            }}
          />
          <TierMenuItem
            available={true}
            label="Composable · Best quality"
            description="Per-element regeneration into a multi-layered editable PSD. Opens designer flow."
            icon={<Sparkles size={14} />}
            onClick={() => {
              setOpen(false);
              onPick("COMPOSABLE");
            }}
          />
        </div>
      )}
    </div>
  );
}

function TierMenuItem({
  available,
  label,
  description,
  icon,
  onClick,
}: {
  available: boolean;
  label: string;
  description: string;
  icon: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!available}
      className={cn(
        "block w-full text-left px-3 py-2.5 border-b border-ink-100 last:border-0 transition-colors",
        available
          ? "hover:bg-paper-100 cursor-pointer"
          : "opacity-50 cursor-not-allowed",
      )}
    >
      <div className="flex items-center gap-2 text-ink-900 text-sm font-medium">
        <span className="text-clay-600">{icon}</span>
        {label}
        {!available && (
          <span className="ml-auto text-[10px] uppercase tracking-wider text-ink-400">
            unavailable
          </span>
        )}
      </div>
      <p className="mt-0.5 text-xs text-ink-500 ml-5">{description}</p>
    </button>
  );
}

function ExportsList({
  workspace,
  exports,
}: {
  workspace: Workspace;
  exports: Asset[];
}) {
  return (
    <div className="rounded-xl border border-ink-200 bg-white/90 shadow-card overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-paper-100 border-b border-ink-200">
          <tr className="text-left text-ink-500">
            <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">
              File
            </th>
            <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">
              Tier
            </th>
            <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">
              Size
            </th>
            <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">
              Created
            </th>
            <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide text-right">
              Download
            </th>
          </tr>
        </thead>
        <tbody>
          {exports.map((e) => {
            const isPdf = e.mime_type === "application/pdf";
            const isJson = e.mime_type === "application/json";
            const isSvg = e.mime_type === "image/svg+xml";
            const isCdr = e.mime_type === "application/x-cdr";
            const kindLabel = isPdf
              ? "PDF · X-4"
              : isJson
                ? "OCR JSON"
                : isSvg
                  ? "SVG · vector"
                  : isCdr
                    ? "CDR · CorelDRAW"
                    : "PSD";
            const tone = isPdf
              ? "clay"
              : isJson
                ? "neutral"
                : isCdr
                  ? "neutral"
                  : "sage";
            return (
              <tr
                key={e.id}
                className="border-b border-ink-100 last:border-0 hover:bg-paper-50/60"
              >
                <td className="px-4 py-2.5 font-mono text-xs">
                  <span className="inline-flex items-center gap-1.5">
                    {isPdf ? (
                      <FileText size={13} className="text-ink-700" />
                    ) : isJson ? (
                      <FileText size={13} className="text-ink-400" />
                    ) : isSvg ? (
                      <Shapes size={13} className="text-clay-700" />
                    ) : isCdr ? (
                      <Box size={13} className="text-sage-800" />
                    ) : (
                      <FileImage size={13} className="text-clay-600" />
                    )}
                    {e.filename}
                  </span>
                </td>
                <td className="px-4 py-2.5">
                  <Badge tone={tone}>{kindLabel}</Badge>
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-ink-600">
                  {(e.size_bytes / 1024).toFixed(1)} KB
                </td>
                <td className="px-4 py-2.5 text-xs text-ink-500">
                  {formatDate(e.created_at)}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <a
                    href={api.assetFileUrl(workspace.slug, e.id)}
                    download={e.filename}
                    className="text-xs text-clay-700 font-medium hover:text-clay-800 inline-flex items-center gap-1"
                  >
                    <Download size={12} /> Download
                  </a>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
