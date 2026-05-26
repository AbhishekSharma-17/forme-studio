import Link from "next/link";
import { type ReactNode } from "react";

import { Logo } from "@/components/Logo";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface AppShellProps {
  children: ReactNode;
}

async function getHealthSafe() {
  try {
    return await api.health();
  } catch {
    return null;
  }
}

export async function AppShell({ children }: AppShellProps) {
  const health = await getHealthSafe();
  const online = health?.status === "ok";

  return (
    <div className="relative min-h-screen z-[1]">
      <header className="sticky top-0 z-30 border-b border-ink-200/40 bg-paper-100/70 backdrop-blur-md shadow-[0_2px_8px_rgba(12,10,9,0.02)]">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2.5 transition-transform active:scale-[0.98]">
            <Logo />
          </Link>

          <nav className="flex items-center gap-1.5">
            <Link
              href="/workspaces"
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-ink-600 hover:bg-paper-200/70 hover:text-ink-900 transition-all duration-200"
            >
              Workspaces
            </Link>
            <Link
              href="/settings"
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-ink-600 hover:bg-paper-200/70 hover:text-ink-900 transition-all duration-200"
            >
              Settings
            </Link>
            <Link
              href="/workspaces/new"
              className="rounded-lg bg-ink-900 px-3.5 py-1.5 text-sm font-medium text-paper-50 hover:bg-ink-800 transition-all duration-200 shadow-sm hover:shadow-md"
            >
              + New workspace
            </Link>
          </nav>
        </div>

        <div className="border-t border-ink-200/20 bg-paper-50/30">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-4 sm:px-6 py-2 text-xs">
            <div className="flex items-center gap-3 text-ink-500">
              <span className="flex items-center gap-2">
                <span
                  className={cn(
                    "size-2 rounded-full",
                    online
                      ? "bg-sage-500 animate-pulse shadow-[0_0_8px_rgba(87,125,96,0.5)]"
                      : "bg-clay-500"
                  )}
                />
                <span className="font-medium text-ink-700">
                  {online ? "Backend online" : "Backend offline"}
                </span>
              </span>
              {online && (
                <>
                  <span className="text-ink-300">·</span>
                  <span className="text-ink-400">API v{health!.version}</span>
                  <span className="text-ink-300">·</span>
                  <span className="font-mono text-[10px] text-ink-400/80 bg-paper-200/60 px-1.5 py-0.5 rounded">
                    {health!.image_model}
                  </span>
                </>
              )}
            </div>
            {online && (
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-ink-400">
                <Capability label="gpt-image-2" on={health!.capabilities.openai_image} />
                <Capability
                  label={`Vector → ${labelForVectorizer(health!.providers.vectorizer_primary)}`}
                  on={isVectorizerReady(health!)}
                  tag={
                    health!.providers.vectorizer_fallback
                      ? `fallback: ${labelForVectorizer(health!.providers.vectorizer_fallback)}`
                      : undefined
                  }
                />
                <Capability
                  label={`Segmentation → ${labelForSegmentation(health!.providers.segmentation)}`}
                  on={isSegmentationReady(health!)}
                />
                {health!.capabilities.cdr_enabled && (
                  <Capability
                    label={`CDR → ${labelForCdr(health!.providers.cdr_primary)}`}
                    on={isCdrReady(health!)}
                    tag={
                      health!.providers.cdr_fallback
                        ? `fallback: ${labelForCdr(health!.providers.cdr_fallback)}`
                        : undefined
                    }
                  />
                )}
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 sm:px-6 py-10">{children}</main>

      <footer className="border-t border-ink-200/30 bg-paper-100/40 py-8 mt-16">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 sm:px-6 text-xs text-ink-400/80 tracking-wider">
          <span className="uppercase font-medium">Forme Studio — packaging pilot · single tenant · local</span>
          <span className="font-mono">{new Date().getFullYear()}</span>
        </div>
      </footer>
    </div>
  );
}

function Capability({
  label,
  on,
  tag,
}: {
  label: string;
  on: boolean;
  tag?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-medium tracking-tight border transition-all duration-200",
        on
          ? "bg-sage-50/50 text-sage-700 border-sage-200/40"
          : "bg-paper-100 text-ink-400 border-ink-200/30"
      )}
      title={tag}
    >
      <span
        className={cn(
          "size-1 rounded-full",
          on ? "bg-sage-500" : "bg-ink-300"
        )}
      />
      <span>{label}</span>
      {tag && (
        <span className="text-[9px] opacity-75 hidden lg:inline">
          ({tag})
        </span>
      )}
    </span>
  );
}

function labelForVectorizer(p: "vectorizer_ai" | "inkscape_potrace"): string {
  return p === "vectorizer_ai" ? "Vectorizer.AI" : "Inkscape Potrace";
}

function labelForSegmentation(
  p: import("@/lib/api").SegmentationProvider,
): string {
  if (p === "replicate") return "Replicate SAM-2";
  if (p === "self_hosted") return "self-hosted SAM";
  if (p === "sam3") return "SAM 3.1 (self-hosted)";
  return "off";
}

function isVectorizerReady(h: import("@/lib/api").Health): boolean {
  const p = h.providers.vectorizer_primary;
  if (p === "vectorizer_ai") return h.capabilities.vectorizer_ai;
  if (p === "inkscape_potrace") return h.capabilities.inkscape;
  return false;
}

function isSegmentationReady(h: import("@/lib/api").Health): boolean {
  const p = h.providers.segmentation;
  if (p === "replicate") return h.capabilities.segmentation_replicate;
  if (p === "self_hosted") return h.capabilities.segmentation_self_hosted;
  if (p === "sam3") return h.capabilities.segmentation_sam3;
  return false;
}

function labelForCdr(p: "cloudconvert" | "uniconvertor"): string {
  return p === "cloudconvert" ? "CloudConvert" : "UniConvertor";
}

function isCdrReady(h: import("@/lib/api").Health): boolean {
  // CDR is ready only when (a) the master toggle is on and (b) the
  // currently-selected primary provider has its dependency present.
  if (!h.capabilities.cdr_enabled) return false;
  const p = h.providers.cdr_primary;
  if (p === "cloudconvert") return h.capabilities.cdr_cloudconvert;
  if (p === "uniconvertor") return h.capabilities.cdr_uniconvertor;
  return false;
}
