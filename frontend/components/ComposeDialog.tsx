"use client";

import {
  CircleAlert,
  Download,
  Loader2,
  Plus,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input, Label, Select } from "@/components/ui/Field";
import {
  ApiError,
  api,
  type Asset,
  type ComposeAssembleResponse,
  type ElementKind,
  type ElementSpec,
  type Workspace,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Stage = "intro" | "discovering" | "review" | "assembling" | "done" | "error";

interface Props {
  workspace: Workspace;
  sourceAsset: Asset;
  onClose: () => void;
}

const KIND_OPTIONS: { value: ElementKind; label: string }[] = [
  { value: "graphic", label: "Graphic (illustration / photo)" },
  { value: "wordmark", label: "Wordmark (logo lockup)" },
  { value: "headline", label: "Headline (display text)" },
  { value: "ornament", label: "Ornament (decorative)" },
  { value: "seal", label: "Seal (badge / corner sticker)" },
  { value: "text", label: "Text (OCR / typeset)" },
  { value: "body_copy", label: "Body copy (skip during assembly)" },
];

/** OCR confidence below this is shown as a yellow warning chip. */
const LOW_OCR_CONFIDENCE = 75;

const SIZE_OPTIONS = [
  { value: "1024x1024" as const, label: "1024×1024 (square)" },
  { value: "1024x1536" as const, label: "1024×1536 (portrait)" },
  { value: "1536x1024" as const, label: "1536×1024 (landscape)" },
];

export function ComposeDialog({ workspace, sourceAsset, onClose }: Props) {
  const [stage, setStage] = useState<Stage>("intro");
  const [elements, setElements] = useState<ElementSpec[]>([]);
  const [extraHint, setExtraHint] = useState("");
  const [progress, setProgress] = useState<string>("");
  const [result, setResult] = useState<ComposeAssembleResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [quality, setQuality] = useState<"medium" | "high">("medium");
  // Slice 10c — auto-vectorize line-art elements (wordmarks, ornaments)
  // during assembly. Pays ~$0.80–$1.20 in Vectorizer.AI credits per
  // sticker but yields a clean SVG composite for CDR / Illustrator.
  const [vectorize, setVectorize] = useState(true);

  // Escape closes (unless we're mid-assemble).
  useEffect(() => {
    const isBlocking = stage === "discovering" || stage === "assembling";
    if (isBlocking) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stage, onClose]);

  async function handleDiscover() {
    setStage("discovering");
    setError(null);
    setProgress("Vision model is analysing the design (~5–10 s)…");
    try {
      const res = await api.composeDiscover(workspace.slug, {
        source_asset_id: sourceAsset.id,
        extra_hint: extraHint.trim() || undefined,
      });
      setElements(res.elements);
      setStage("review");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Element discovery failed.",
      );
      setStage("error");
    }
  }

  function updateElement(idx: number, patch: Partial<ElementSpec>) {
    setElements((prev) =>
      prev.map((e, i) => (i === idx ? { ...e, ...patch } : e)),
    );
  }

  function removeElement(idx: number) {
    setElements((prev) => prev.filter((_, i) => i !== idx));
  }

  function addElement() {
    setElements((prev) => [
      ...prev,
      {
        name: `new_element_${prev.length + 1}`,
        label: "New element",
        prompt:
          "Describe a single visual element. Transparent background. Isolated. No other elements.",
        position_mm: [10, 10, 50, 50],
        size_px: "1024x1024",
        kind: "graphic",
        text: null,
        confidence: null,
        vectorizable: false,
      },
    ]);
  }

  function addTextElement() {
    setElements((prev) => [
      ...prev,
      {
        name: `text_${prev.length + 1}`,
        label: "New text block",
        prompt: "User-supplied text",
        position_mm: [10, 10, 50, 12],
        size_px: "1536x1024",
        kind: "text",
        text: "",
        confidence: null,
        vectorizable: false,
      },
    ]);
  }

  async function handleAssemble() {
    setStage("assembling");
    setError(null);
    const renderable = elements.filter((e) => e.kind !== "body_copy");
    setProgress(
      `Generating ${renderable.length} elements at ${quality} quality (~${
        renderable.length * 60
      }s)…`,
    );
    try {
      const res = await api.composeAssemble(workspace.slug, {
        source_asset_id: sourceAsset.id,
        elements,
        quality,
        dpi: 300,
        color_space: "CMYK",
        vectorize,
      });
      setResult(res);
      setStage("done");
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Assembly failed.",
      );
      setStage("error");
    }
  }

  const renderableCount = elements.filter((e) => e.kind !== "body_copy").length;
  const bodyCopyCount = elements.length - renderableCount;
  const estimatedCost = renderableCount * (quality === "high" ? 0.2 : 0.1);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 backdrop-blur-sm p-4"
      onClick={() => stage !== "discovering" && stage !== "assembling" && onClose()}
    >
      <div
        className="w-full max-w-4xl rounded-xl bg-white shadow-xl border border-ink-200 overflow-hidden flex flex-col max-h-[90vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-ink-200/60 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Wand2 size={18} className="text-clay-600" />
            <h3 className="font-display text-lg text-ink-900">
              Make print-ready
            </h3>
            <Badge tone="clay">composable</Badge>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={stage === "discovering" || stage === "assembling"}
            className="text-ink-500 hover:text-ink-900 disabled:opacity-50"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {stage === "intro" && (
            <IntroStage
              extraHint={extraHint}
              setExtraHint={setExtraHint}
            />
          )}
          {(stage === "discovering" || stage === "assembling") && (
            <LoadingStage message={progress} />
          )}
          {stage === "review" && (
            <ReviewStage
              elements={elements}
              onUpdate={updateElement}
              onRemove={removeElement}
              onAdd={addElement}
              onAddText={addTextElement}
              vectorize={vectorize}
              setVectorize={setVectorize}
            />
          )}
          {stage === "done" && result && (
            <DoneStage result={result} workspace={workspace} />
          )}
          {stage === "error" && error && (
            <ErrorStage error={error} onRetry={() => setStage("intro")} />
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 bg-paper-100/40 border-t border-ink-200/60 flex items-center justify-between gap-3">
          {stage === "review" && (
            <div className="text-xs text-ink-500">
              <strong>{renderableCount}</strong> renderable element
              {renderableCount === 1 ? "" : "s"}
              {bodyCopyCount > 0 && (
                <>
                  {" · "}
                  <strong>{bodyCopyCount}</strong> body-copy block
                  {bodyCopyCount === 1 ? "" : "s"} (skipped)
                </>
              )}
              {" · "}est. <strong>${estimatedCost.toFixed(2)}</strong>
              {vectorize && (
                <span className="text-ink-400"> + vector credits</span>
              )}
            </div>
          )}
          {stage !== "review" && <div />}

          <div className="flex items-center gap-2">
            {stage === "intro" && (
              <>
                <Button
                  variant="ghost"
                  onClick={onClose}
                  className="text-ink-600"
                >
                  Cancel
                </Button>
                <Button onClick={handleDiscover}>
                  <Sparkles size={13} className="mr-1.5" />
                  Analyse design
                </Button>
              </>
            )}
            {stage === "review" && (
              <>
                <Select
                  value={quality}
                  onChange={(e) =>
                    setQuality(e.target.value as "medium" | "high")
                  }
                  className="w-auto text-xs"
                >
                  <option value="medium">medium (cheaper)</option>
                  <option value="high">high (press-grade)</option>
                </Select>
                <Button
                  onClick={handleAssemble}
                  disabled={renderableCount === 0}
                >
                  <Wand2 size={13} className="mr-1.5" />
                  Generate + assemble
                </Button>
              </>
            )}
            {stage === "done" && result && (
              <>
                <Button variant="ghost" onClick={onClose}>
                  Close
                </Button>
                <a
                  href={api.assetFileUrl(workspace.slug, result.asset.id)}
                  download={result.asset.filename}
                  className="inline-flex items-center gap-1.5 rounded-md bg-ink-900 text-paper-50 px-3.5 py-1.5 text-sm font-medium hover:bg-ink-800"
                >
                  <Download size={13} />
                  Download PSD
                </a>
              </>
            )}
            {stage === "error" && (
              <Button onClick={() => setStage("intro")}>Try again</Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────── stages

function IntroStage({
  extraHint,
  setExtraHint,
}: {
  extraHint: string;
  setExtraHint: (s: string) => void;
}) {
  return (
    <div className="space-y-4 text-sm text-ink-700 max-w-2xl mx-auto py-6">
      <p>
        Make print-ready turns this finished design into a layered,
        editable PSD plus a vector SVG composite. The pipeline:
      </p>
      <ol className="text-ink-700 text-sm space-y-1.5 list-decimal list-inside ml-2">
        <li>
          <strong>Analyse</strong> — vision discovers every graphic
          element; OCR discovers every text region.
        </li>
        <li>
          <strong>Review</strong> — edit prompts, fix any garbled OCR
          text, reposition, add or remove elements.
        </li>
        <li>
          <strong>Assemble</strong> — gpt-image-2 regenerates each
          graphic on a transparent canvas; Pillow renders the text
          fresh; line-art elements auto-vectorize; everything composites
          into one layered PSD + SVG with the correct CMYK / mm specs.
        </li>
      </ol>
      <p className="text-ink-500 text-xs">
        Every visual element becomes its own editable Photoshop layer.
        Designers can swap, restyle, or replace any single element
        without re-rendering the whole sticker.
      </p>
      <div>
        <Label
          htmlFor="extra-hint"
          hint="Optional — biases what the vision model isolates"
        >
          Extra hint for the analyser
        </Label>
        <Input
          id="extra-hint"
          value={extraHint}
          onChange={(e) => setExtraHint(e.target.value)}
          placeholder="e.g. isolate the brand mark, hero botanical, and corner ornament"
        />
      </div>
    </div>
  );
}

function LoadingStage({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 text-ink-700">
      <Loader2 size={36} className="animate-spin text-clay-600" />
      <p className="text-sm font-medium">{message}</p>
    </div>
  );
}

function ReviewStage({
  elements,
  onUpdate,
  onRemove,
  onAdd,
  onAddText,
  vectorize,
  setVectorize,
}: {
  elements: ElementSpec[];
  onUpdate: (idx: number, patch: Partial<ElementSpec>) => void;
  onRemove: (idx: number) => void;
  onAdd: () => void;
  onAddText: () => void;
  vectorize: boolean;
  setVectorize: (v: boolean) => void;
}) {
  const lowConfidenceCount = elements.filter(
    (e) =>
      e.kind === "text" &&
      e.confidence !== null &&
      e.confidence !== undefined &&
      e.confidence < LOW_OCR_CONFIDENCE,
  ).length;

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-clay-200 bg-clay-50/60 px-3 py-2 text-xs text-clay-900">
        <strong>Review + refine.</strong> Edit any element&apos;s text or
        prompt. Adjust position / size. Remove elements you don&apos;t
        want. Click <strong>+ Add element</strong> /{" "}
        <strong>+ Add text</strong> for anything the analyser missed.
        Then hit <strong>Generate + assemble</strong>.
      </div>

      {lowConfidenceCount > 0 && (
        <div className="rounded-md border border-amber-200 bg-amber-50/80 px-3 py-2 text-xs text-amber-900 flex items-start gap-2">
          <CircleAlert size={14} className="mt-0.5 shrink-0" />
          <span>
            <strong>{lowConfidenceCount}</strong> text element
            {lowConfidenceCount === 1 ? "" : "s"} have low OCR
            confidence (&lt; {LOW_OCR_CONFIDENCE}%). Check them carefully
            and edit the text before assembling — they may be garbled.
          </span>
        </div>
      )}

      {/* Auto-vectorize toggle (slice 10c) */}
      <label
        htmlFor="vectorize-toggle"
        className="flex items-start gap-3 cursor-pointer rounded-md border border-ink-200 bg-paper-50/50 px-3 py-2 hover:bg-paper-100/60"
      >
        <input
          id="vectorize-toggle"
          type="checkbox"
          checked={vectorize}
          onChange={(e) => setVectorize(e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-ink-300 text-clay-600 focus:ring-clay-500"
        />
        <div className="flex-1 text-xs text-ink-700">
          <span className="font-semibold text-ink-900">
            Auto-vectorize line-art elements
          </span>
          <p className="mt-0.5 text-ink-500 leading-relaxed">
            Logos / wordmarks / ornaments get traced into clean SVG
            paths during assembly so the SVG and CDR exports stay crisp
            at any print size. Adds ~$0.80–$1.20 in Vectorizer.AI credits
            per sticker. Photo illustrations stay raster regardless.
          </p>
        </div>
      </label>

      {elements.map((el, idx) => (
        <ElementRow
          key={idx}
          element={el}
          onUpdate={(patch) => onUpdate(idx, patch)}
          onRemove={() => onRemove(idx)}
        />
      ))}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <button
          type="button"
          onClick={onAdd}
          className="rounded-md border-2 border-dashed border-ink-300 px-3 py-3 text-sm text-ink-600 hover:bg-paper-100 hover:border-clay-400 transition-colors inline-flex items-center justify-center gap-1.5"
        >
          <Plus size={14} />
          Add element (graphic)
        </button>
        <button
          type="button"
          onClick={onAddText}
          className="rounded-md border-2 border-dashed border-ink-300 px-3 py-3 text-sm text-ink-600 hover:bg-paper-100 hover:border-clay-400 transition-colors inline-flex items-center justify-center gap-1.5"
        >
          <Plus size={14} />
          Add text block
        </button>
      </div>
    </div>
  );
}

function ElementRow({
  element,
  onUpdate,
  onRemove,
}: {
  element: ElementSpec;
  onUpdate: (patch: Partial<ElementSpec>) => void;
  onRemove: () => void;
}) {
  const isBodyCopy = element.kind === "body_copy";
  const isText = element.kind === "text";
  const lowConfidence =
    isText &&
    element.confidence !== null &&
    element.confidence !== undefined &&
    element.confidence < LOW_OCR_CONFIDENCE;

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2.5 space-y-2",
        isBodyCopy
          ? "border-ink-200 bg-paper-100/60 opacity-70"
          : lowConfidence
            ? "border-amber-300/80 bg-amber-50/40"
            : isText
              ? "border-ink-200 bg-paper-50/40"
              : "border-ink-200 bg-white",
      )}
    >
      <div className="flex items-center gap-2">
        <Input
          value={element.label}
          onChange={(e) => onUpdate({ label: e.target.value })}
          className="flex-1 font-medium text-sm"
          placeholder="Label"
        />
        {isText && element.confidence !== null && element.confidence !== undefined && (
          <Badge tone={lowConfidence ? "warning" : "sage"}>
            OCR {element.confidence.toFixed(0)}%
          </Badge>
        )}
        <Select
          value={element.kind}
          onChange={(e) =>
            onUpdate({ kind: e.target.value as ElementKind })
          }
          className="w-48 text-xs"
        >
          {KIND_OPTIONS.map((k) => (
            <option key={k.value} value={k.value}>
              {k.label}
            </option>
          ))}
        </Select>
        <button
          type="button"
          onClick={onRemove}
          className="text-ink-400 hover:text-clay-700 p-1"
          title="Remove this element"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {isText ? (
        <div>
          <label
            htmlFor={`text-${element.name}`}
            className="block text-[10px] font-semibold uppercase tracking-wider text-ink-500 mb-1"
          >
            Text content {lowConfidence && "— please verify, low OCR confidence"}
          </label>
          <textarea
            id={`text-${element.name}`}
            value={element.text ?? ""}
            onChange={(e) => onUpdate({ text: e.target.value })}
            rows={2}
            className={cn(
              "w-full rounded-md border px-2.5 py-1.5 text-sm resize-y",
              lowConfidence
                ? "border-amber-300 bg-amber-50/30"
                : "border-ink-200 bg-white",
            )}
            placeholder="Type the exact text that should appear in the final layer."
          />
        </div>
      ) : (
        <textarea
          value={element.prompt}
          onChange={(e) => onUpdate({ prompt: e.target.value })}
          rows={2}
          disabled={isBodyCopy}
          className="w-full rounded-md border border-ink-200 px-2.5 py-1.5 text-xs font-mono disabled:bg-paper-100 disabled:text-ink-400 resize-y"
          placeholder="Self-contained prompt for this element alone. End with: Transparent background. Isolated. No other elements."
        />
      )}

      <div className="grid grid-cols-5 gap-2 items-end">
        <FieldNumber
          label="x (mm)"
          value={element.position_mm[0]}
          onChange={(v) =>
            onUpdate({
              position_mm: [
                v,
                element.position_mm[1],
                element.position_mm[2],
                element.position_mm[3],
              ],
            })
          }
        />
        <FieldNumber
          label="y (mm)"
          value={element.position_mm[1]}
          onChange={(v) =>
            onUpdate({
              position_mm: [
                element.position_mm[0],
                v,
                element.position_mm[2],
                element.position_mm[3],
              ],
            })
          }
        />
        <FieldNumber
          label="w (mm)"
          value={element.position_mm[2]}
          onChange={(v) =>
            onUpdate({
              position_mm: [
                element.position_mm[0],
                element.position_mm[1],
                v,
                element.position_mm[3],
              ],
            })
          }
        />
        <FieldNumber
          label="h (mm)"
          value={element.position_mm[3]}
          onChange={(v) =>
            onUpdate({
              position_mm: [
                element.position_mm[0],
                element.position_mm[1],
                element.position_mm[2],
                v,
              ],
            })
          }
        />
        <Select
          value={element.size_px}
          onChange={(e) =>
            onUpdate({
              size_px: e.target.value as ElementSpec["size_px"],
            })
          }
          className="text-xs"
        >
          {SIZE_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </Select>
      </div>
    </div>
  );
}

function FieldNumber({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="block text-[10px] text-ink-500 mb-0.5">{label}</label>
      <input
        type="number"
        step="0.1"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full rounded-md border border-ink-200 px-2 py-1 text-xs font-mono"
      />
    </div>
  );
}

function DoneStage({
  result,
  workspace,
}: {
  result: ComposeAssembleResponse;
  workspace: Workspace;
}) {
  return (
    <div className="space-y-4 py-4">
      <div className="rounded-md border border-sage-300 bg-sage-50 px-4 py-3 text-sm text-sage-900">
        <strong>Composable PSD assembled.</strong> {result.layer_count} total
        layers ({result.element_count} elements + base canvas) at{" "}
        {result.width_px}×{result.height_px}px, {result.dpi} DPI,{" "}
        {result.color_space}.
      </div>

      <h4 className="font-medium text-sm text-ink-700">
        Generated elements ({result.elements.length})
      </h4>
      <div className="grid grid-cols-2 gap-2">
        {result.elements.map((e) => (
          <div
            key={e.asset_id}
            className="rounded-md border border-ink-200 bg-paper-50 p-2 flex items-center gap-3"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.assetFileUrl(workspace.slug, e.asset_id)}
              alt={e.label}
              className="w-12 h-12 object-contain bg-paper-200/50 rounded"
            />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-ink-900 truncate">
                {e.label}
              </p>
              <p className="text-[11px] font-mono text-ink-500 truncate">
                {e.name} · {e.width_px}×{e.height_px}px · $
                {e.cost_usd.toFixed(3)}
              </p>
            </div>
          </div>
        ))}
      </div>

      <p className="text-xs text-ink-500">
        Total OpenAI cost: <strong>${result.total_cost_usd.toFixed(3)}</strong>.
        The PSD is saved to{" "}
        <code className="font-mono text-[10px]">
          workspaces/{workspace.slug}/exports/
        </code>{" "}
        and is also available via the Exports table below the gallery.
      </p>
    </div>
  );
}

function ErrorStage({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <div className="py-8 text-center space-y-3">
      <CircleAlert size={32} className="mx-auto text-clay-600" />
      <p className="text-sm text-ink-700 max-w-md mx-auto">{error}</p>
      <button
        type="button"
        onClick={onRetry}
        className="text-xs text-clay-700 underline"
      >
        Start over
      </button>
    </div>
  );
}
