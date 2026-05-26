import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Tailwind-aware className concatenator. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** ISO date → "23 May 2026" */
export function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
