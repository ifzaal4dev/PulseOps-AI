import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "PulseOps AI — Reliability Command Center",
  description: "Closed-loop agentic reliability command center for smart facilities.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 min-w-0 px-8 py-6 max-w-[1600px]">{children}</main>
        </div>
      </body>
    </html>
  );
}
