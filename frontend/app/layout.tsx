import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Aurea — Wealth Intelligence Platform",
  description: "Governed, agentic wealth management. Truly personal advice, at scale.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='15' stroke='%23c8a35e' stroke-width='1.5' fill='%230f2b3d'/><path d='M16 7l6 11H10l6-11z' fill='%23c8a35e' opacity='0.9'/><circle cx='16' cy='20' r='2.4' fill='white'/></svg>" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
