import Link from "next/link";
import { notFound } from "next/navigation";

import { DesignStudio } from "@/components/DesignStudio";
import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { ApiError, api } from "@/lib/api";
import { formatDate } from "@/lib/utils";

export const dynamic = "force-dynamic";

interface Props {
  params: { slug: string };
}

export default async function WorkspaceDetailPage({ params }: Props) {
  let ws;
  try {
    ws = await api.getWorkspace(params.slug);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }

  // Load gallery + references + exports + capability in parallel.
  const [generations, references, exports, health] = await Promise.all([
    api.listAssets(params.slug, "generation").catch(() => []),
    api.listAssets(params.slug, "reference").catch(() => []),
    api.listAssets(params.slug, "export").catch(() => []),
    api.health().catch(() => null),
  ]);
  const canGenerate = !!health?.capabilities.openai_image;

  const specs = ws.specs as {
    trim_mm?: { w: number; h: number };
    bleed_mm?: number;
    dpi?: number;
    color_space?: string;
    generation_size?: string;
    notes?: string;
  };

  return (
    <div className="space-y-8">
      {/* Crumb + title */}
      <div>
        <Link
          href="/workspaces"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-ink-200/60 bg-white/85 text-[10.5px] font-bold uppercase tracking-wider text-ink-500 hover:text-clay-650 hover:border-ink-300 hover:bg-white shadow-sm transition-all duration-200"
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M10 3L5 8l5 5" />
          </svg>
          Workspaces
        </Link>
        <div className="mt-4 flex flex-col sm:flex-row sm:items-end justify-between gap-4 pb-4 border-b border-ink-200/40">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-ink-400 mb-1.5">
              Packaging · {ws.product_type.replaceAll("_", " ")}
            </p>
            <h1 className="font-display text-4xl text-ink-900 font-medium tracking-tight">{ws.name}</h1>
            <p className="text-ink-450 mt-1 font-mono text-xs">{ws.slug}</p>
          </div>
          <div className="flex sm:flex-col items-center sm:items-end gap-2.5 shrink-0">
            <Badge tone={generations.length > 0 ? "sage" : "clay"} className="shadow-sm">
              {generations.length > 0 ? "Active" : "Draft"}
            </Badge>
            <span className="text-xs text-ink-400">
              Created {formatDate(ws.created_at)}
            </span>
          </div>
        </div>
      </div>

      {/* Print specs strip — compact, always visible */}
      <Card className="shadow-md">
        <CardHeader className="border-b border-ink-200/30">
          <CardTitle className="text-base font-semibold tracking-tight">Print specifications</CardTitle>
          <Badge tone="neutral" className="border-ink-200">frozen spec</Badge>
        </CardHeader>
        <CardBody className="grid grid-cols-2 sm:grid-cols-5 gap-4 bg-paper-100/30 rounded-b-2xl py-4.5">
          <SpecCell
            label="Trim"
            value={specs.trim_mm ? `${specs.trim_mm.w} × ${specs.trim_mm.h} mm` : "—"}
          />
          <SpecCell
            label="Bleed"
            value={specs.bleed_mm != null ? `${specs.bleed_mm} mm` : "—"}
          />
          <SpecCell label="DPI" value={specs.dpi?.toString() ?? "—"} />
          <SpecCell label="Color" value={specs.color_space ?? "—"} />
          <SpecCell label="Gen size" value={specs.generation_size ?? "—"} />
        </CardBody>
      </Card>

      {/* === The actual design studio === */}
      <DesignStudio
        workspace={ws}
        initialGenerations={generations}
        initialReferences={references}
        initialExports={exports}
        canGenerate={canGenerate}
        tiers={
          health?.tiers ?? {
            tier_a: true,
            tier_a_ocr: false,
          }
        }
        providers={
          health?.providers ?? {
            vectorizer_primary: "vectorizer_ai",
            vectorizer_fallback: "inkscape_potrace",
            cdr_primary: "cloudconvert",
            cdr_fallback: "uniconvertor",
          }
        }
        cdrEnabled={!!health?.capabilities.cdr_enabled}
      />

      {/* Workspace folder — tucked at the bottom now */}
      <details className="rounded-lg border border-ink-200 bg-white/70 p-4">
        <summary className="cursor-pointer font-medium text-ink-800">
          Workspace folder
        </summary>
        <div className="mt-3 space-y-2 text-sm text-ink-600">
          <code className="block break-all rounded-md bg-ink-900 text-paper-100 px-3 py-2 text-xs font-mono">
            {ws.folder_path}
          </code>
          <p className="text-ink-500">
            All references, generations and exports for this SKU live under this
            folder. The audit JSONL travels with it.
          </p>
        </div>
      </details>
    </div>
  );
}

function SpecCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white/60 border border-ink-200/50 rounded-xl p-3.5 shadow-[0_1px_2.5px_rgba(12,10,9,0.015)] hover:bg-white transition-all duration-300">
      <div className="text-[9px] font-semibold uppercase tracking-wider text-ink-400">{label}</div>
      <div className="font-mono text-xs font-semibold text-ink-850 mt-1">{value}</div>
    </div>
  );
}
