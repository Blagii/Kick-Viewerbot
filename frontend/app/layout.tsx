import "@/styles/globals.css";
import "./youtube.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "YouTube Viewer Bot",
  description: "Configure and monitor YouTube playback sessions.",
  icons: { icon: "/youtube-icon.svg" },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
