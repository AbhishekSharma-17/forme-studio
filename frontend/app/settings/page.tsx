import { ServerCog } from "lucide-react";

import { SettingsForm } from "@/components/SettingsForm";
import { api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function SettingsPage() {
  let settings;
  try {
    settings = await api.getSettings();
  } catch {
    return (
      <div className="rounded-lg border border-clay-200 bg-clay-50 p-6 text-clay-900">
        Backend unreachable at <code className="font-mono">{api.baseUrl}</code>.
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-8 flex items-start gap-3">
        <div className="rounded-lg bg-ink-900 text-paper-100 p-2.5">
          <ServerCog size={20} />
        </div>
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-ink-400 mb-0.5">
            Local dashboard
          </p>
          <h1 className="font-display text-3xl text-ink-900">Settings</h1>
          <p className="text-ink-600 mt-1 max-w-xl">
            Configure which providers Forme uses, tune costs and timeouts, and
            verify your credentials. Editable fields write back to the same{" "}
            <code className="font-mono text-xs">.env</code> the backend runs from;
            the worker hot-reloads on save.
          </p>
        </div>
      </div>

      <SettingsForm initial={settings} />
    </div>
  );
}
