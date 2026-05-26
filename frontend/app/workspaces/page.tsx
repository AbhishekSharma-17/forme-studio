import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/Button";
import { WorkspaceCard } from "@/components/WorkspaceCard";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function WorkspacesPage() {
  let workspaces;
  try {
    workspaces = await api.listWorkspaces();
  } catch {
    return (
      <div className="rounded-lg border border-clay-200 bg-clay-50 p-6 text-clay-900">
        Backend unreachable at <code className="font-mono">{api.baseUrl}</code>. Start it with:
        <pre className="mt-3 rounded bg-ink-900 text-paper-100 p-3 text-xs overflow-auto">{`cd backend
uv run uvicorn app.main:app --reload --port 8002`}</pre>
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-10 pb-6 border-b border-ink-200/40">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-ink-400 mb-1.5">
            Module · Packaging
          </p>
          <h1 className="font-display text-4xl md:text-5xl text-ink-900 font-medium tracking-tight">Workspaces</h1>
          <p className="text-ink-600 mt-2.5 max-w-xl text-sm leading-relaxed">
            One workspace per product / SKU. Frozen print specs, generated
            artwork, exports and audit trail all live together.
          </p>
        </div>
        <Link href="/workspaces/new" className="shrink-0">
          <Button size="lg" className="shadow-md hover:shadow-lg">+ New workspace</Button>
        </Link>
      </div>

      {workspaces.length === 0 ? (
        <EmptyState
          title="No workspaces yet"
          description="Create one to brief Forme on a product. Specs are frozen the moment you create it, so every later export is anchored to the same trim, bleed, DPI and color space."
          ctaHref="/workspaces/new"
          ctaLabel="Create your first workspace"
        />
      ) : (
        <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
          {workspaces.map((ws) => (
            <WorkspaceCard key={ws.id} ws={ws} />
          ))}
        </div>
      )}
    </>
  );
}
