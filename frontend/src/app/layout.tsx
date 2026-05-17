import type { Metadata } from "next";
import { Hanken_Grotesk, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// Professional, business-style typography: one neutral sans across the
// whole surface, with mono reserved for technical strings (trace_ids,
// file paths, code, status chips). Hierarchy comes from weight + size,
// not from a decorative display face or italics.
const hankenGrotesk = Hanken_Grotesk({
  weight: ["400", "500", "600", "700"],
  subsets: ["latin"],
  variable: "--font-hanken-grotesk",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Partner-Ticketing Agentic Platform",
  description:
    "Reference implementation — eight specialist agents wired through a LangGraph state machine, with a human-in-the-loop gate on every outbound message.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${hankenGrotesk.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">{children}</body>
    </html>
  );
}
