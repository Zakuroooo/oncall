import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "OnCall — the fix is not done until it is proven",
  description:
    "A self-healing infrastructure agent where every fix is verified by a health check that ran against the live service under load. The model proposes; the oracle decides.",
  icons: {
    icon: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%230a0d13'/%3E%3Cpath d='M6 17h5l3-7 4 14 3-7h5' fill='none' stroke='%234dd4c0' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
