"use client";

import { CircleAlert, ImagePlus, Loader2, Trash2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { ApiError, api, type Asset, type Workspace } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  workspace: Workspace;
  references: Asset[];
  /** IDs that are currently checked in the edit panel. */
  selectedIds: number[];
  onSelectedChange: (ids: number[]) => void;
  /** Called when uploads complete so the parent can refresh. */
  onUploaded: (added: Asset[]) => void;
}

export function ReferencesPanel({
  workspace,
  references,
  selectedIds,
  onSelectedChange,
  onUploaded,
}: Props) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const arr = Array.from(files).filter((f) => f.type.startsWith("image/"));
      if (arr.length === 0) {
        setError("Only image files are accepted as references.");
        return;
      }
      setError(null);
      setUploading(true);
      try {
        const res = await api.uploadReferences(workspace.slug, arr);
        onUploaded(res.references);
        // Pre-select the freshly uploaded ones so they're immediately usable.
        onSelectedChange([
          ...selectedIds,
          ...res.references.map((r) => r.id).filter((id) => !selectedIds.includes(id)),
        ]);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Upload failed.");
      } finally {
        setUploading(false);
      }
    },
    [workspace.slug, onUploaded, onSelectedChange, selectedIds],
  );

  function toggleSelected(id: number) {
    onSelectedChange(
      selectedIds.includes(id)
        ? selectedIds.filter((x) => x !== id)
        : [...selectedIds, id],
    );
  }

  return (
    <Card className="shadow-md">
      <CardHeader className="border-b border-ink-200/30">
        <CardTitle className="text-base font-semibold tracking-tight">References</CardTitle>
        <Badge tone="neutral" className="border-ink-200">
          {references.length} on file
          {selectedIds.length > 0 && ` · ${selectedIds.length} selected`}
        </Badge>
      </CardHeader>
      <CardBody className="space-y-5 p-6">
        {/* Drop zone */}
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (e.dataTransfer.files.length) void handleFiles(e.dataTransfer.files);
          }}
          className={cn(
            "w-full rounded-xl border-2 border-dashed p-6 text-left transition-all duration-300",
            dragging
              ? "border-clay-500 bg-clay-50/50 shadow-[0_0_12px_rgba(215,88,39,0.06)]"
              : "border-ink-200 bg-paper-50/40 hover:border-ink-300/80 hover:bg-paper-50 shadow-sm",
          )}
          disabled={uploading}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              if (e.target.files) void handleFiles(e.target.files);
              e.target.value = ""; // allow re-selecting the same file
            }}
          />
          <div className="flex items-center gap-4">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-white border border-ink-200/50 text-clay-600 shadow-sm shrink-0">
              {uploading ? (
                <Loader2 size={18} className="animate-spin text-clay-600" />
              ) : (
                <ImagePlus size={18} />
              )}
            </div>
            <div className="text-sm leading-snug">
              <div className="font-semibold text-ink-900 tracking-tight">
                {uploading
                  ? "Uploading references…"
                  : "Drop images here or click to browse"}
              </div>
              <div className="text-xs text-ink-500 mt-1 leading-relaxed">
                Logos, product photos, mood boards — <code className="font-mono text-[10.5px] bg-paper-200/60 px-1.5 py-0.5 rounded text-ink-700">gpt-image-2</code> sees up to
                16 references per run, in generate or edit. PNG, JPEG, WEBP up to 25 MB each.
              </div>
            </div>
          </div>
        </button>

        {error && (
          <div className="rounded-lg border border-clay-200 bg-clay-50 px-3 py-2 text-sm text-clay-800 flex items-start gap-2">
            <CircleAlert size={14} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Thumbnails */}
        {references.length > 0 && (
          <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
            {references.map((r) => {
              const selected = selectedIds.includes(r.id);
              return (
                <button
                  key={r.id}
                  type="button"
                  onClick={() => toggleSelected(r.id)}
                  title={
                    selected
                      ? "Click to deselect"
                      : "Click to use this as reference context"
                  }
                  className={cn(
                    "relative aspect-square overflow-hidden rounded-xl border transition-all duration-300 shadow-sm",
                    selected
                      ? "border-clay-500 ring-2 ring-clay-500/30 scale-[1.03] shadow-md"
                      : "border-ink-200 hover:border-ink-300 hover:scale-[1.015] hover:shadow",
                  )}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={api.assetFileUrl(workspace.slug, r.id)}
                    alt={r.filename}
                    className="h-full w-full object-cover transition-transform group-hover:scale-105"
                    loading="lazy"
                  />
                  {selected && (
                    <div className="absolute inset-0 bg-clay-950/15 backdrop-blur-[0.5px] transition-all flex items-center justify-center">
                      <div className="flex h-6 w-6 items-center justify-center rounded-full bg-clay-650 text-white text-[11px] font-bold shadow-md animate-scale-up">
                        ✓
                      </div>
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </CardBody>
    </Card>
  );
}
