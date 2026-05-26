"use client";

import { Loader2, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { ApiError, api, type Workspace } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";

const PRODUCT_LABELS: Record<string, string> = {
  lotion_bottle_label: "Lotion bottle label",
  cream_jar_label: "Cream jar label",
  cream_box_tuck_end: "Cream box (tuck-end)",
  serum_dropper_label: "Serum dropper label",
  shampoo_pouch: "Shampoo pouch",
};

export function WorkspaceCard({ ws }: { ws: Workspace }) {
  const [confirming, setConfirming] = useState(false);
  const [deleteFiles, setDeleteFiles] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

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

  // Escape key + click-outside close the confirm modal.
  useEffect(() => {
    if (!confirming) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !deleting) setConfirming(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirming, deleting]);

  async function handleDelete() {
    if (deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteWorkspace(ws.slug, { delete_files: deleteFiles });
      setConfirming(false);
      // Re-fetch the list — the RSC parent re-reads /api/packaging/workspaces.
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Delete failed.",
      );
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="relative group">
      <Link href={`/workspaces/${ws.slug}`} className="block">
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

      {/* Delete button overlay — corner, only shows on hover */}
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setConfirming(true);
          setError(null);
        }}
        className="absolute top-3 right-3 z-10 inline-flex items-center justify-center size-7 rounded-md bg-white/90 border border-ink-200/70 text-ink-500 opacity-0 group-hover:opacity-100 hover:bg-clay-50 hover:border-clay-300 hover:text-clay-700 transition-all duration-200 shadow-sm"
        title={`Delete ${ws.name}`}
        aria-label={`Delete workspace ${ws.name}`}
      >
        <Trash2 size={13} />
      </button>

      {/* Confirm modal */}
      {confirming && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 backdrop-blur-sm p-4"
          onClick={() => !deleting && setConfirming(false)}
        >
          <div
            className="w-full max-w-md rounded-xl bg-white shadow-xl border border-ink-200 overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-4 border-b border-ink-200/60">
              <h3 className="font-display text-lg text-ink-900">
                Delete <span className="text-clay-700">{ws.name}</span>?
              </h3>
              <p className="mt-1 text-xs text-ink-500 font-mono">{ws.slug}</p>
            </div>
            <div className="px-5 py-4 space-y-3 text-sm text-ink-700">
              <p>
                This removes the workspace and all its database rows —
                generations, references, exports, and audit events.{" "}
                <strong>This cannot be undone.</strong>
              </p>
              <label
                htmlFor={`delete-files-${ws.id}`}
                className="flex items-start gap-2.5 cursor-pointer rounded-md border border-ink-200 bg-paper-50/60 px-3 py-2.5 hover:bg-paper-100/80"
              >
                <input
                  id={`delete-files-${ws.id}`}
                  type="checkbox"
                  checked={deleteFiles}
                  onChange={(e) => setDeleteFiles(e.target.checked)}
                  disabled={deleting}
                  className="mt-0.5 h-4 w-4 rounded border-ink-300 text-clay-600"
                />
                <div className="flex-1">
                  <span className="text-sm font-medium text-ink-800">
                    Also delete files on disk
                  </span>
                  <p className="mt-0.5 text-xs text-ink-500">
                    Removes{" "}
                    <code className="font-mono text-[11px]">
                      workspaces/{ws.slug}/
                    </code>{" "}
                    including generations, references, exports, and the
                    audit JSONL. Leave unchecked to keep the folder for
                    recovery.
                  </p>
                </div>
              </label>
              {error && (
                <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-xs text-clay-800">
                  {error}
                </div>
              )}
            </div>
            <div className="px-5 py-3 bg-paper-100/40 border-t border-ink-200/60 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={deleting}
                className="px-3 py-1.5 text-sm font-medium text-ink-600 rounded-md hover:bg-paper-200 disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleting}
                className={cn(
                  "px-3.5 py-1.5 text-sm font-medium rounded-md inline-flex items-center gap-1.5 transition-colors",
                  "bg-clay-700 text-white hover:bg-clay-800",
                  deleting && "cursor-not-allowed opacity-70",
                )}
              >
                {deleting ? (
                  <>
                    <Loader2 size={13} className="animate-spin" />
                    Deleting…
                  </>
                ) : (
                  <>
                    <Trash2 size={13} />
                    {deleteFiles ? "Delete + remove files" : "Delete"}
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
