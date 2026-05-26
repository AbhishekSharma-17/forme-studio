"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardFooter, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Label, Select, Textarea } from "@/components/ui/Field";
import { ApiError, api, type PackagingPreset } from "@/lib/api";

interface Props {
  presets: PackagingPreset[];
}

export function CreateWorkspaceForm({ presets }: Props) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [productType, setProductType] = useState(presets[0]?.id ?? "");
  // Slice 10d toggle: false = "I have a finished label, recreate it";
  // true = "I have the bottle, design something fresh on it".
  const [designMode, setDesignMode] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedPreset = presets.find((p) => p.id === productType);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const ws = await api.createWorkspace({
        name: name.trim(),
        description: description.trim() || undefined,
        product_type: productType,
        design_mode: designMode,
      });
      router.push(`/workspaces/${ws.slug}`);
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
      else setError("Could not create workspace. Is the backend running?");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <Card className="shadow-lg">
        <CardHeader className="border-b border-ink-200/30">
          <CardTitle>New packaging workspace</CardTitle>
          <Badge tone="clay">packaging</Badge>
        </CardHeader>
        <CardBody className="space-y-6 p-6">
          <div>
            <Label htmlFor="name" required hint="The product/SKU name">
              Workspace name
            </Label>
            <Input
              id="name"
              required
              autoFocus
              minLength={1}
              maxLength={200}
              placeholder="e.g. Glow Serenity Lotion 250 ml"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div>
            <Label htmlFor="product_type" required hint="Determines trim, bleed, DPI">
              Product type
            </Label>
            <Select
              id="product_type"
              required
              value={productType}
              onChange={(e) => setProductType(e.target.value)}
            >
              {presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </Select>
            {selectedPreset && <PresetPreview preset={selectedPreset} />}
          </div>

          <div>
            <Label htmlFor="description" hint="Optional">
              Brief / description
            </Label>
            <Textarea
              id="description"
              maxLength={2000}
              placeholder="What is this product? Brand mood, audience, key visual cues…"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {/* ── Design-mode toggle (slice 10d) ─────────────────────── */}
          <div className="rounded-xl border border-ink-200/60 bg-paper-50/50 p-4 space-y-3 shadow-[inset_0_1px_2px_rgba(12,10,9,0.02)]">
            <div className="text-xs font-semibold uppercase tracking-wider text-ink-700">
              Starting point
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <ModeCard
                active={!designMode}
                title="I have the design"
                description="Upload a finished front + back label, we analyse, recreate every element, and assemble."
                onClick={() => setDesignMode(false)}
              />
              <ModeCard
                active={designMode}
                title="I have the bottle"
                description="Upload a plain product + style reference + brief. We design the label on the product first, then iterate, approve, and assemble."
                onClick={() => setDesignMode(true)}
              />
            </div>
          </div>

          {error && (
            <div className="rounded-lg border border-clay-200 bg-clay-50 px-3.5 py-2.5 text-sm text-clay-800 shadow-sm animate-fade-in">
              {error}
            </div>
          )}
        </CardBody>
        <CardFooter className="border-t border-ink-200/30 bg-paper-50/20 px-6 py-4.5">
          <Button
            type="button"
            variant="ghost"
            onClick={() => router.push("/workspaces")}
          >
            Cancel
          </Button>
          <Button type="submit" loading={submitting} disabled={!name.trim()} className="shadow-md">
            Create workspace
          </Button>
        </CardFooter>
      </Card>
    </form>
  );
}

function PresetPreview({ preset }: { preset: PackagingPreset }) {
  return (
    <div className="mt-3 rounded-xl tactile-sheet p-4 shadow-[inset_0_1px_4px_rgba(12,10,9,0.015)]">
      <p className="text-xs text-ink-600 mb-3 leading-relaxed font-medium">{preset.description}</p>
      <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs bg-white/60 p-3 rounded-lg border border-ink-200/30">
        <Spec label="Trim" value={`${preset.trim_mm.w} × ${preset.trim_mm.h} mm`} />
        <Spec label="Bleed" value={`${preset.bleed_mm} mm`} />
        <Spec label="DPI" value={String(preset.dpi)} />
        <Spec label="Color" value={preset.color_space} />
      </dl>
      {preset.notes && (
        <p className="mt-3 text-[11px] italic text-ink-500/90 leading-normal">
          * {preset.notes}
        </p>
      )}
    </div>
  );
}

function Spec({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] text-ink-400 uppercase tracking-wider font-semibold">{label}</dt>
      <dd className="text-ink-850 font-medium mt-0.5 font-mono text-xs">{value}</dd>
    </div>
  );
}

function ModeCard({
  active,
  title,
  description,
  onClick,
}: {
  active: boolean;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        "text-left rounded-lg border px-4 py-3.5 transition-all duration-200 " +
        (active
          ? "border-clay-500 bg-clay-50/60 shadow-[0_2px_8px_rgba(215,88,39,0.08)] ring-2 ring-clay-500/20"
          : "border-ink-200/70 bg-white/70 hover:border-ink-300 hover:bg-white shadow-sm")
      }
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-ink-900">{title}</span>
        <span
          className={
            "size-3.5 rounded-full border-2 " +
            (active
              ? "border-clay-600 bg-clay-600"
              : "border-ink-300 bg-white")
          }
          aria-hidden
        />
      </div>
      <p className="mt-1 text-xs text-ink-500 leading-relaxed">{description}</p>
    </button>
  );
}
