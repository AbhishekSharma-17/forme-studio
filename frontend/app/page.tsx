import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";

export default async function HomePage() {
  let workspaceCount = 0;
  let online = false;
  let version = "—";
  try {
    const health = await api.health();
    online = health.status === "ok";
    version = health.version;
    if (online) {
      const ws = await api.listWorkspaces();
      workspaceCount = ws.length;
    }
  } catch {
    /* offline */
  }

  return (
    <div className="grid lg:grid-cols-[1.4fr_1fr] gap-16 items-center py-4">
      <section>
        <Badge tone="clay" className="mb-6">
          Module 1 · Packaging · Pilot
        </Badge>
        <h1 className="font-display text-5xl md:text-6xl leading-[1.05] text-ink-900 mb-6 tracking-tight">
          Print-ready packaging,<br />
          <span className="italic font-normal text-clay-600">generated.</span>
        </h1>
        <p className="text-lg text-ink-600 max-w-xl mb-9 leading-relaxed font-sans font-normal">
          Forme turns a creative brief into iterative <code className="font-mono text-sm bg-paper-200/50 px-1.5 py-0.5 rounded text-ink-700">gpt-image-2</code> designs and
          delivers them as layered PSD, print PDF, and vector artwork — with
          a full audit trail per workspace.
        </p>

        <div className="flex flex-wrap items-center gap-3.5">
          <Link href="/workspaces/new">
            <Button size="lg" className="shadow-md hover:shadow-lg">Create your first workspace</Button>
          </Link>
          <Link href="/workspaces">
            <Button size="lg" variant="secondary" className="hover:border-ink-300">
              Browse workspaces
              {workspaceCount > 0 && (
                <span className="ml-2.5 rounded-full bg-ink-900 text-paper-100 text-[10px] font-bold px-2 py-0.5 tracking-normal">
                  {workspaceCount}
                </span>
              )}
            </Button>
          </Link>
        </div>

        <div className="mt-12 grid grid-cols-3 gap-4 max-w-lg text-sm">
          <Stat label="Pilot module" value="Packaging" />
          <Stat label="Workspaces" value={online ? String(workspaceCount) : "—"} />
          <Stat label="API" value={`v${version}`} />
        </div>
      </section>

      <aside className="relative">
        <div className="absolute -inset-10 rounded-full bg-gradient-to-br from-clay-200/40 via-paper-300/30 to-sage-200/30 blur-3xl opacity-80" />
        <div className="relative rounded-2xl border border-ink-200/65 bg-white/90 p-6 shadow-[0_4px_24px_rgba(12,10,9,0.04)] backdrop-blur-sm">
          <h3 className="font-display text-xl text-ink-900 mb-4 font-medium tracking-tight">
            What ships in the pilot
          </h3>
          <ul className="space-y-3.5 text-sm text-ink-600">
            <Bullet>Per-product workspaces with frozen print specs</Bullet>
            <Bullet>gpt-image-2 generate & edit, multi-variant</Bullet>
            <Bullet>Layered PSD (Tier A + B; Tier C optional)</Bullet>
            <Bullet>Print PDF/X-4 with bleed + trim marks</Bullet>
            <Bullet>Vector export via Vectorizer.AI</Bullet>
            <Bullet>Full audit trail per workspace (DB + JSONL)</Bullet>
          </ul>
        </div>
      </aside>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ink-200/60 bg-white/60 backdrop-blur-sm px-4 py-3 shadow-[0_1px_2px_rgba(12,10,9,0.01)] transition-all hover:bg-white hover:shadow-sm">
      <div className="text-[9px] uppercase tracking-wider font-semibold text-ink-400">
        {label}
      </div>
      <div className="font-display text-xl text-ink-900 mt-0.5 font-medium">{value}</div>
    </div>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden
        className="mt-2 size-1.5 shrink-0 rounded-full bg-clay-500 shadow-[0_0_4px_rgba(215,88,39,0.4)]"
      />
      <span>{children}</span>
    </li>
  );
}
