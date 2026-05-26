import { forwardRef, type ButtonHTMLAttributes } from "react";

import { cn } from "@/lib/utils";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-ink-900 text-paper-100 hover:bg-ink-800 active:scale-[0.98] active:bg-ink-900 border border-ink-950 shadow-[0_1px_2px_rgba(12,10,9,0.08),inset_0_1px_0_rgba(255,255,255,0.12)]",
  secondary:
    "bg-white/80 backdrop-blur-sm text-ink-800 border border-ink-200/80 hover:border-ink-300 hover:bg-paper-50 active:scale-[0.98] shadow-sm",
  ghost: "bg-transparent text-ink-600 hover:text-ink-900 hover:bg-paper-200/80 active:scale-[0.98]",
  danger:
    "bg-clay-600 text-white border border-clay-700 hover:bg-clay-700 active:scale-[0.98] active:bg-clay-800 shadow-[0_1px_2px_rgba(12,10,9,0.08),inset_0_1px_0_rgba(255,255,255,0.12)]",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-6 text-base",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { className, variant = "primary", size = "md", loading, disabled, children, ...props },
    ref
  ) {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "relative inline-flex items-center justify-center gap-2 rounded-md font-medium transition-all",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          variantClasses[variant],
          sizeClasses[size],
          className
        )}
        {...props}
      >
        {loading && (
          <span
            aria-hidden
            className="inline-block h-3.5 w-3.5 rounded-full border-2 border-current border-r-transparent animate-spin"
          />
        )}
        {children}
      </button>
    );
  }
);
