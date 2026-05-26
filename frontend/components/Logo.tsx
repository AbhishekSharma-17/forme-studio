import { cn } from "@/lib/utils";

interface LogoProps {
  className?: string;
  showWordmark?: boolean;
}

/**
 * "Forme" wordmark — the brand is a printer's term for the locked-up bed of
 * type. The mark uses a heavy serif "F" + tag-style stack of swashes to
 * suggest registration marks / a press plate.
 */
export function Logo({ className, showWordmark = true }: LogoProps) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <svg
        viewBox="0 0 36 36"
        width="28"
        height="28"
        aria-hidden
        className="text-ink-900"
      >
        <rect
          x="2"
          y="2"
          width="32"
          height="32"
          rx="6"
          fill="currentColor"
        />
        <g
          fill="none"
          stroke="rgb(250 248 242)"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          {/* F mark */}
          <path d="M12 9 V27" />
          <path d="M12 9 H24" />
          <path d="M12 17.5 H21" />
        </g>
        {/* registration tick */}
        <circle cx="27" cy="27" r="2.5" fill="rgb(215 88 39)" />
      </svg>
      {showWordmark && (
        <span className="font-display text-xl font-semibold tracking-tight text-ink-900">
          Forme
        </span>
      )}
    </span>
  );
}
