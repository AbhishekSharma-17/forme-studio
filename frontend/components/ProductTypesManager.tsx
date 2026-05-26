"use client";

import {
  Box,
  CircleAlert,
  Lock,
  Pencil,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { Input, Label, Select } from "@/components/ui/Field";
import {
  ApiError,
  api,
  type PackagingPreset,
  type ProductTypeCreate,
} from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  initial: PackagingPreset[];
}

type DraftState = ProductTypeCreate & { editingKey: string | null };

const EMPTY_DRAFT: DraftState = {
  editingKey: null,
  key: "",
  label: "",
  description: "",
  trim_w_mm: 70,
  trim_h_mm: 100,
  bleed_mm: 3,
  dpi: 300,
  color_space: "CMYK",
  generation_size: "1024x1536",
  notes: "",
};

export function ProductTypesManager({ initial }: Props) {
  const [rows, setRows] = useState<PackagingPreset[]>(initial);
  const [draft, setDraft] = useState<DraftState | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);

  // Escape-to-close edit modal
  useEffect(() => {
    if (!draft) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !saving) setDraft(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [draft, saving]);

  async function refresh() {
    try {
      const next = await api.listProductTypes();
      setRows(next);
    } catch {
      /* keep last good state */
    }
  }

  function openCreate() {
    setError(null);
    setDraft({ ...EMPTY_DRAFT });
  }

  function openEdit(pt: PackagingPreset) {
    setError(null);
    setDraft({
      editingKey: pt.id,
      key: pt.id,
      label: pt.label,
      description: pt.description,
      trim_w_mm: pt.trim_mm.w,
      trim_h_mm: pt.trim_mm.h,
      bleed_mm: pt.bleed_mm,
      dpi: pt.dpi,
      color_space: pt.color_space,
      generation_size: pt.generation_size,
      notes: pt.notes,
    });
  }

  async function handleSave() {
    if (!draft || saving) return;
    setSaving(true);
    setError(null);
    try {
      if (draft.editingKey) {
        await api.updateProductType(draft.editingKey, {
          label: draft.label,
          description: draft.description,
          trim_w_mm: draft.trim_w_mm,
          trim_h_mm: draft.trim_h_mm,
          bleed_mm: draft.bleed_mm,
          dpi: draft.dpi,
          color_space: draft.color_space,
          generation_size: draft.generation_size,
          notes: draft.notes,
        });
      } else {
        await api.createProductType({
          key: draft.key,
          label: draft.label,
          description: draft.description,
          trim_w_mm: draft.trim_w_mm,
          trim_h_mm: draft.trim_h_mm,
          bleed_mm: draft.bleed_mm,
          dpi: draft.dpi,
          color_space: draft.color_space,
          generation_size: draft.generation_size,
          notes: draft.notes,
        });
      }
      await refresh();
      setDraft(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(pt: PackagingPreset) {
    if (pt.is_builtin) return; // UI should disable this
    if (
      !window.confirm(
        `Delete product type "${pt.label}"?\n\n` +
          "This cannot be undone. Workspaces using it must be removed first.",
      )
    ) {
      return;
    }
    setBusyKey(pt.id);
    setError(null);
    try {
      await api.deleteProductType(pt.id);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Delete failed.");
    } finally {
      setBusyKey(null);
    }
  }

  const builtins = rows.filter((r) => r.is_builtin);
  const custom = rows.filter((r) => !r.is_builtin);

  return (
    <div className="space-y-6">
      {/* Header + add button */}
      <div className="flex items-end justify-between gap-4">
        <p className="text-sm text-ink-600 max-w-2xl">
          Product types pin the print specs (trim, bleed, DPI, colour
          space) every workspace freezes at create time. Built-ins ship
          with the studio and can't be edited or deleted. Custom rows are
          all yours — create, edit, remove freely. <strong>Editing a
          product type never changes existing workspaces</strong> — their
          specs are frozen.
        </p>
        <Button onClick={openCreate} className="shrink-0">
          <Plus size={14} className="mr-1.5" />
          New product type
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-sm text-clay-800 flex items-start gap-2">
          <CircleAlert size={14} className="mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Built-ins */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Lock size={16} className="text-ink-500" />
            <CardTitle>Built-in ({builtins.length})</CardTitle>
          </div>
          <Badge tone="neutral">read-only</Badge>
        </CardHeader>
        <CardBody className="p-0">
          <ProductTypeTable
            rows={builtins}
            onEdit={openEdit}
            onDelete={handleDelete}
            busyKey={busyKey}
          />
        </CardBody>
      </Card>

      {/* Custom */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Box size={16} className="text-clay-600" />
            <CardTitle>Custom ({custom.length})</CardTitle>
          </div>
          <Badge tone="clay">editable</Badge>
        </CardHeader>
        <CardBody className="p-0">
          {custom.length === 0 ? (
            <div className="px-5 py-10 text-center text-sm text-ink-500">
              No custom product types yet. Click{" "}
              <strong>+ New product type</strong> to add one.
            </div>
          ) : (
            <ProductTypeTable
              rows={custom}
              onEdit={openEdit}
              onDelete={handleDelete}
              busyKey={busyKey}
            />
          )}
        </CardBody>
      </Card>

      {/* Create / edit modal */}
      {draft && (
        <DraftModal
          draft={draft}
          setDraft={setDraft}
          saving={saving}
          error={error}
          onSave={handleSave}
          onClose={() => !saving && setDraft(null)}
        />
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────── subcomponents

function ProductTypeTable({
  rows,
  onEdit,
  onDelete,
  busyKey,
}: {
  rows: PackagingPreset[];
  onEdit: (pt: PackagingPreset) => void;
  onDelete: (pt: PackagingPreset) => void;
  busyKey: string | null;
}) {
  return (
    <table className="w-full text-sm">
      <thead className="bg-paper-100 border-b border-ink-200/60">
        <tr className="text-left text-ink-500">
          <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">Label</th>
          <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">Key</th>
          <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">Trim</th>
          <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">Bleed</th>
          <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">DPI</th>
          <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide">Color</th>
          <th className="px-4 py-2.5 font-medium text-xs uppercase tracking-wide text-right">
            Actions
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((pt) => (
          <tr key={pt.id} className="border-b border-ink-100 last:border-0 hover:bg-paper-50/60">
            <td className="px-4 py-2.5 text-ink-900 font-medium">{pt.label}</td>
            <td className="px-4 py-2.5 font-mono text-xs text-ink-500">{pt.id}</td>
            <td className="px-4 py-2.5 font-mono text-xs">
              {pt.trim_mm.w} × {pt.trim_mm.h} mm
            </td>
            <td className="px-4 py-2.5 font-mono text-xs">{pt.bleed_mm} mm</td>
            <td className="px-4 py-2.5 font-mono text-xs">{pt.dpi}</td>
            <td className="px-4 py-2.5">
              <Badge tone="neutral">{pt.color_space}</Badge>
            </td>
            <td className="px-4 py-2.5 text-right whitespace-nowrap">
              <button
                type="button"
                onClick={() => onEdit(pt)}
                disabled={pt.is_builtin}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                  pt.is_builtin
                    ? "text-ink-300 cursor-not-allowed"
                    : "text-ink-700 hover:bg-paper-200",
                )}
                title={
                  pt.is_builtin
                    ? "Built-in — clone to a new key to customise"
                    : `Edit ${pt.label}`
                }
              >
                <Pencil size={12} />
                Edit
              </button>
              <button
                type="button"
                onClick={() => onDelete(pt)}
                disabled={pt.is_builtin || busyKey === pt.id}
                className={cn(
                  "ml-1 inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                  pt.is_builtin
                    ? "text-ink-300 cursor-not-allowed"
                    : "text-clay-700 hover:bg-clay-50",
                  busyKey === pt.id && "opacity-60 cursor-not-allowed",
                )}
                title={
                  pt.is_builtin
                    ? "Built-in — cannot be deleted"
                    : `Delete ${pt.label}`
                }
              >
                <Trash2 size={12} />
                Delete
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DraftModal({
  draft,
  setDraft,
  saving,
  error,
  onSave,
  onClose,
}: {
  draft: DraftState;
  setDraft: (d: DraftState) => void;
  saving: boolean;
  error: string | null;
  onSave: () => void;
  onClose: () => void;
}) {
  const isEdit = draft.editingKey !== null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-xl bg-white shadow-xl border border-ink-200 overflow-hidden max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-ink-200/60 flex items-center justify-between">
          <h3 className="font-display text-lg text-ink-900">
            {isEdit ? `Edit ${draft.label}` : "New product type"}
          </h3>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="text-ink-500 hover:text-ink-900 disabled:opacity-50"
          >
            <X size={18} />
          </button>
        </div>
        <div className="px-5 py-4 overflow-y-auto space-y-4">
          {!isEdit && (
            <div>
              <Label htmlFor="pt-key" hint="lowercase + digits + underscores">
                Key (immutable)
              </Label>
              <Input
                id="pt-key"
                value={draft.key}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"),
                  })
                }
                placeholder="big_format_poster"
                autoFocus
              />
            </div>
          )}

          <div>
            <Label htmlFor="pt-label">Label</Label>
            <Input
              id="pt-label"
              value={draft.label}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
              placeholder="Big format poster"
            />
          </div>

          <div>
            <Label htmlFor="pt-description" hint="Optional, shown in the create-workspace form">
              Description
            </Label>
            <Input
              id="pt-description"
              value={draft.description ?? ""}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              placeholder="A4 poster mockup at 297 × 420 mm with 5 mm bleed."
            />
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div>
              <Label htmlFor="pt-trim-w" hint="mm">
                Trim width
              </Label>
              <Input
                id="pt-trim-w"
                type="number"
                step="0.1"
                value={draft.trim_w_mm}
                onChange={(e) =>
                  setDraft({ ...draft, trim_w_mm: Number(e.target.value) })
                }
              />
            </div>
            <div>
              <Label htmlFor="pt-trim-h" hint="mm">
                Trim height
              </Label>
              <Input
                id="pt-trim-h"
                type="number"
                step="0.1"
                value={draft.trim_h_mm}
                onChange={(e) =>
                  setDraft({ ...draft, trim_h_mm: Number(e.target.value) })
                }
              />
            </div>
            <div>
              <Label htmlFor="pt-bleed" hint="mm">
                Bleed
              </Label>
              <Input
                id="pt-bleed"
                type="number"
                step="0.1"
                value={draft.bleed_mm ?? 3}
                onChange={(e) =>
                  setDraft({ ...draft, bleed_mm: Number(e.target.value) })
                }
              />
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div>
              <Label htmlFor="pt-dpi">DPI</Label>
              <Input
                id="pt-dpi"
                type="number"
                step="1"
                value={draft.dpi ?? 300}
                onChange={(e) =>
                  setDraft({ ...draft, dpi: Number(e.target.value) })
                }
              />
            </div>
            <div>
              <Label htmlFor="pt-color">Color space</Label>
              <Select
                id="pt-color"
                value={draft.color_space ?? "CMYK"}
                onChange={(e) =>
                  setDraft({ ...draft, color_space: e.target.value })
                }
              >
                <option value="CMYK">CMYK</option>
                <option value="RGB">RGB</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="pt-gen-size" hint="gpt-image-2 native">
                Generation size
              </Label>
              <Select
                id="pt-gen-size"
                value={draft.generation_size ?? "1024x1536"}
                onChange={(e) =>
                  setDraft({ ...draft, generation_size: e.target.value })
                }
              >
                <option value="1024x1024">1024×1024 (square)</option>
                <option value="1024x1536">1024×1536 (portrait)</option>
                <option value="1536x1024">1536×1024 (landscape)</option>
              </Select>
            </div>
          </div>

          <div>
            <Label htmlFor="pt-notes">Notes</Label>
            <Input
              id="pt-notes"
              value={draft.notes ?? ""}
              onChange={(e) => setDraft({ ...draft, notes: e.target.value })}
              placeholder="Designer-facing notes about safety margins, dieline, etc."
            />
          </div>

          {error && (
            <div className="rounded-md border border-clay-200 bg-clay-50 px-3 py-2 text-xs text-clay-800">
              {error}
            </div>
          )}
        </div>
        <div className="px-5 py-3 bg-paper-100/40 border-t border-ink-200/60 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="px-3 py-1.5 text-sm font-medium text-ink-600 rounded-md hover:bg-paper-200 disabled:opacity-50"
          >
            Cancel
          </button>
          <Button onClick={onSave} disabled={saving}>
            <Save size={13} className="mr-1.5" />
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create"}
          </Button>
        </div>
      </div>
    </div>
  );
}
