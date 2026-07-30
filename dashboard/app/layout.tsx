import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "market-mcp dashboard",
  description:
    "Scheduled market scans: crypto, IDX and global equities, prediction markets and backtests.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
