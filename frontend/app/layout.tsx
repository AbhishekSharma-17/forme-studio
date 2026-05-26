import type { Metadata } from "next";
import { Fraunces, Inter } from "next/font/google";

import { AppShell } from "@/components/AppShell";

import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});
const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
  axes: ["opsz", "SOFT"],
});

export const metadata: Metadata = {
  title: "Forme Studio — AI-assisted packaging & print design",
  description:
    "Forme is an AI-assisted packaging design tool that turns briefs into print-ready PSD / PDF / vector deliverables.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${fraunces.variable}`}>
      <body className="font-sans">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
