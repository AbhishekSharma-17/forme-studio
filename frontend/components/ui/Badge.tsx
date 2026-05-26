import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Tone = "neutral" | "clay" | "sage" | "warning";

const toneClasses: Record<Tone, string> = {
  neutral: "bg-paper-200/80 text-ink-600 border-ink-300/40",
  clay: "bg-clay-50 text-clay-700 border-clay-200/60",
  sage: "bg-sage-50 text-sage-700 border-sage-200/60",
  warning: "bg-amber-50 text-amber-800 border-amber-200/60",
};

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function Badge({ className, tone = "neutral", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider",
        toneClasses[tone],
        className
      )}
      {...props}
    />
  );
}
