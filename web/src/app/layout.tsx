import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Countersign · Case desk",
  description:
    "GMP deviation investigation desk. Agents assemble and cite; the QP confirms personally.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
