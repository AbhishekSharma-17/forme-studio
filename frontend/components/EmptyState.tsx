import Link from "next/link";

import { Button } from "@/components/ui/Button";

interface EmptyStateProps {
  title: string;
  description: string;
  ctaHref?: string;
  ctaLabel?: string;
}

export function EmptyState({ title, description, ctaHref, ctaLabel }: EmptyStateProps) {
  return (
    <div className="rounded-xl border border-dashed border-ink-200 bg-white/60 px-8 py-16 text-center">
      <div className="mx-auto w-fit rounded-full border border-ink-200 bg-paper-100 px-4 py-1 text-xs font-mono text-ink-500 mb-5">
        empty
      </div>
      <h2 className="font-display text-2xl text-ink-900 mb-2">{title}</h2>
      <p className="text-ink-600 max-w-md mx-auto mb-6">{description}</p>
      {ctaHref && ctaLabel && (
        <Link href={ctaHref}>
          <Button size="lg">{ctaLabel}</Button>
        </Link>
      )}
    </div>
  );
}
