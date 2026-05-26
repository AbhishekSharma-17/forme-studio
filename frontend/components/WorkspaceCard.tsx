import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import type { Workspace } from "@/lib/api";
import { formatDate } from "@/lib/utils";

const PRODUCT_LABELS: Record<string, string> = {
  lotion_bottle_label: "Lotion bottle label",
  cream_jar_label: "Cream jar label",
  cream_box_tuck_end: "Cream box (tuck-end)",
  serum_dropper_label: "Serum dropper label",
  shampoo_pouch: "Shampoo pouch",
};

export function WorkspaceCard({ ws }: { ws: Workspace }) {
  const specs = ws.specs as {
    trim_mm?: { w: number; h: number };
    bleed_mm?: number;
    dpi?: number;
    color_space?: string;
  };
  const trim = specs.trim_mm
    ? `${specs.trim_mm.w} × ${specs.trim_mm.h} mm`
    : "—";
  const productLabel = PRODUCT_LABELS[ws.product_type] ?? ws.product_type;

  return (
    <Link href={`/workspaces/${ws.slug}`} className="group block">
      <Card className="h-full transition-all duration-300 hover:scale-[1.015] hover:shadow-[0_12px_36px_-12px_rgba(12,10,9,0.08)] hover:border-ink-300/80">
        <div className="p-5 flex flex-col h-full gap-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="font-display text-lg leading-tight text-ink-900 group-hover:text-clay-700 transition-colors truncate font-medium">
                {ws.name}
              </h3>
              <p className="text-[11px] text-ink-400 mt-0.5 truncate font-mono">
                {ws.slug}
              </p>
            </div>
            <Badge tone="clay" className="shrink-0">{productLabel}</Badge>
          </div>

          {ws.description && (
            <p className="text-sm text-ink-600 line-clamp-2 leading-relaxed">{ws.description}</p>
          )}

          <dl className="mt-auto grid grid-cols-3 gap-2.5 pt-3.5 border-t border-ink-200/40 text-[11px] bg-paper-100/40 -mx-5 p-5">
            <div>
              <dt className="text-ink-400 uppercase tracking-wider text-[9px] font-semibold">Trim</dt>
              <dd className="text-ink-850 font-mono font-medium mt-0.5">{trim}</dd>
            </div>
            <div>
              <dt className="text-ink-400 uppercase tracking-wider text-[9px] font-semibold">DPI</dt>
              <dd className="text-ink-850 font-mono font-medium mt-0.5">{specs.dpi ?? "—"}</dd>
            </div>
            <div>
              <dt className="text-ink-400 uppercase tracking-wider text-[9px] font-semibold">Color</dt>
              <dd className="text-ink-850 font-mono font-medium mt-0.5">{specs.color_space ?? "—"}</dd>
            </div>
          </dl>

          <div className="text-[10px] text-ink-400/85 flex items-center justify-between px-5 pb-5 -mx-5 bg-paper-100/40 rounded-b-2xl">
            <span>Created {formatDate(ws.created_at)}</span>
            <span className="font-mono opacity-70">#{ws.id}</span>
          </div>
        </div>
      </Card>
    </Link>
  );
}
