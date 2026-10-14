import type { Metadata } from "next";
import { Public_Sans, Source_Serif_4 } from "next/font/google";
import "./globals.css";

// Public Sans was designed for government services, which suits a civic legal tool.
// Source Serif 4 is used for answers because legal text reads better in a serif.
const sans = Public_Sans({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const serif = Source_Serif_4({ subsets: ["latin"], variable: "--font-serif", display: "swap" });

export const metadata: Metadata = {
  title: "TenantWise: Ontario renter questions, answered with the law",
  description:
    "Ask about rent increases, repairs, evictions and deposits in Ontario. Every answer cites the Residential Tenancies Act or an official Ontario guide.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${serif.variable}`}>
      <body>
        <header className="site-header">
          <a href="/" className="wordmark">TenantWise</a>
          <nav>
            <a href="/stats">How it performs</a>
            <a href="https://github.com/Jeffano/tenantwise">Source code</a>
          </nav>
        </header>
        <main className="page">{children}</main>
        <footer className="site-footer">
          <p>
            TenantWise gives general legal information, not legal advice. For your situation, contact the{" "}
            <a href="https://tribunalsontario.ca/ltb/">Landlord and Tenant Board</a>,{" "}
            <a href="https://www.legalaid.on.ca/">Legal Aid Ontario</a>, or a community legal clinic.
          </p>
          <p>Built by <a href="https://jeffanojohn.com">Jeffano John</a>.</p>
        </footer>
      </body>
    </html>
  );
}
