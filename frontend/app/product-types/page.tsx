import Link from "next/link";
import { Box } from "lucide-react";

import { ProductTypesManager } from "@/components/ProductTypesManager";
import { ApiError, api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ProductTypesPage() {
  let presets;
  try {
    presets = await api.listProductTypes();
  } catch (err) {
    return (
      <div className="rounded-lg border border-clay-200 bg-clay-50 p-6 text-clay-900">
        Backend unreachable at <code className="font-mono">{api.baseUrl}</code>.
        {err instanceof ApiError && (
          <div className="mt-2 text-xs font-mono opacity-80">{err.message}</div>
        )}
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <div className="mb-2">
        <Link
          href="/settings"
          className="text-xs font-semibold uppercase tracking-wider text-ink-500 hover:text-clay-700 transition-colors"
        >
          ← Back to settings
        </Link>
      </div>
      <div className="mb-8 flex items-start gap-3">
        <div className="rounded-lg bg-ink-900 text-paper-100 p-2.5">
          <Box size={20} />
        </div>
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-ink-400 mb-0.5">
            Configuration
          </p>
          <h1 className="font-display text-3xl text-ink-900">Product types</h1>
          <p className="text-sm text-ink-500 mt-1">
            Define the print specs that get frozen onto every new workspace.
          </p>
        </div>
      </div>

      <ProductTypesManager initial={presets} />
    </div>
  );
}
