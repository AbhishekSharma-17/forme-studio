import { type HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-2xl bg-white/80 border border-ink-200/60 shadow-[0_1px_3px_rgba(12,10,9,0.03),0_12px_36px_-16px_rgba(12,10,9,0.05),inset_0_1px_0_rgba(255,255,255,0.8)] backdrop-blur-md transition-all duration-300",
        className
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "px-6 py-4.5 border-b border-ink-200/40 flex items-center justify-between gap-4",
        className
      )}
      {...props}
    />
  );
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={cn("font-display text-lg text-ink-900 font-medium tracking-tight", className)}
      {...props}
    />
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-6", className)} {...props} />;
}

export function CardFooter({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "px-6 py-4 border-t border-ink-200/40 bg-paper-100/30 rounded-b-2xl flex items-center justify-end gap-3",
        className
      )}
      {...props}
    />
  );
}
