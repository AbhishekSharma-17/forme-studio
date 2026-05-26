import Link from "next/link";

import { CreateWorkspaceForm } from "@/components/CreateWorkspaceForm";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function NewWorkspacePage() {
  let presets;
  try {
    presets = await api.listPresets();
  } catch {
    return (
      <div className="rounded-lg border border-clay-200 bg-clay-50 p-6 text-clay-900">
        Backend unreachable at <code className="font-mono">{api.baseUrl}</code>. Start it and reload.
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="mb-6">
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
        <h1 className="font-display text-3xl text-ink-900 mt-2">
          New packaging workspace
        </h1>
        <p className="text-ink-600 mt-1">
          Pick a product type — Forme freezes its print specs into the workspace
          so every later export ships at the correct trim, bleed and DPI.
        </p>
      </div>

      <CreateWorkspaceForm presets={presets} />
    </div>
  );
}
