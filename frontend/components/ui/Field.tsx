import React, {
  forwardRef,
  useState,
  useEffect,
  useRef,
  type InputHTMLAttributes,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cn } from "@/lib/utils";

const baseField =
  "block w-full rounded-lg border border-ink-200/80 bg-white/95 px-3.5 py-2 text-sm text-ink-900 placeholder-ink-400/70 transition-all duration-200 focus:border-clay-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-clay-500/10 shadow-[0_1px_2px_rgba(12,10,9,0.02)] disabled:opacity-60";

interface LabelProps {
  htmlFor: string;
  children: React.ReactNode;
  required?: boolean;
  hint?: string;
}

export function Label({ htmlFor, children, required, hint }: LabelProps) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-1.5 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-ink-700"
    >
      <span>
        {children}
        {required && <span className="ml-0.5 text-clay-600">*</span>}
      </span>
      {hint && <span className="text-[10px] font-normal text-ink-400 normal-case tracking-normal">{hint}</span>}
    </label>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(baseField, "h-10", className)} {...props} />;
  }
);

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} className={cn(baseField, "min-h-[88px] resize-y", className)} {...props} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(
  function Select({ className, children, value, onChange, id, ...props }, ref) {
    const [open, setOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement | null>(null);

    // Extract options dynamically from children options
    const options = React.Children.map(children, (child) => {
      if (React.isValidElement(child) && child.type === "option") {
        return {
          value: child.props.value,
          label: child.props.children,
        };
      }
      return null;
    }).filter(Boolean) as { value: string; label: string }[];

    const activeOption = options.find((opt) => String(opt.value) === String(value)) || options[0];

    useEffect(() => {
      if (!open) return;
      function handleOutside(e: MouseEvent) {
        if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
          setOpen(false);
        }
      }
      document.addEventListener("mousedown", handleOutside);
      return () => document.removeEventListener("mousedown", handleOutside);
    }, [open]);

    function handleSelect(val: string) {
      setOpen(false);
      if (onChange) {
        // Synthesise select change event to feed into react-form listeners cleanly
        const event = {
          target: {
            value: val,
            id: id || "",
            name: props.name || "",
          },
        } as unknown as React.ChangeEvent<HTMLSelectElement>;
        onChange(event);
      }
    }

    return (
      <div className="relative" ref={containerRef}>
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className={cn(
            baseField,
            "h-10 text-left pr-9 flex items-center justify-between cursor-pointer focus:ring-2 focus:ring-clay-500/10 transition-all duration-200",
            className
          )}
        >
          <span className="truncate font-medium text-ink-900">
            {activeOption ? activeOption.label : "Select..."}
          </span>
          <svg
            aria-hidden
            className={cn(
              "pointer-events-none text-ink-400 transition-transform duration-200 shrink-0 ml-2",
              open && "rotate-180 text-clay-650"
            )}
            width="12"
            height="12"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M4 6l4 4 4-4" />
          </svg>
        </button>

        {open && (
          <div className="absolute left-0 right-0 mt-1.5 z-40 rounded-xl border border-ink-200 bg-white/95 backdrop-blur-md shadow-[0_10px_32px_-12px_rgba(12,10,9,0.12)] overflow-hidden max-h-60 overflow-y-auto animate-fade-in">
            {options.map((opt) => {
              const selected = String(opt.value) === String(value);
              return (
                <button
                  key={String(opt.value)}
                  type="button"
                  onClick={() => handleSelect(String(opt.value))}
                  className={cn(
                    "w-full text-left px-4 py-2.5 text-sm transition-all duration-150 flex items-center justify-between",
                    selected
                      ? "bg-clay-50 text-clay-700 font-semibold"
                      : "text-ink-700 hover:bg-paper-100 hover:text-ink-900"
                  )}
                >
                  <span className="truncate">{opt.label}</span>
                  {selected && (
                    <span className="text-clay-600 font-bold text-xs shrink-0 ml-2">✓</span>
                  )}
                </button>
              );
            })}
          </div>
        )}

        {/* Hidden select target for standard bindings and ref targets */}
        <select
          ref={ref}
          value={value}
          onChange={onChange}
          className="sr-only"
          {...props}
        >
          {children}
        </select>
      </div>
    );
  }
);
