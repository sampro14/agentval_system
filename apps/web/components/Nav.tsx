"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { API_URL } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Runs", match: (p: string) => p === "/" || p.startsWith("/runs") },
  { href: "/stages", label: "One agent at a time", match: (p: string) => p.startsWith("/stages") },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <Link href="/" className="brand">
          AgentEval
        </Link>
        <nav className="nav">
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} className={link.match(pathname) ? "active" : ""}>
              {link.label}
            </Link>
          ))}
        </nav>
        <span className="spacer" />
        <span className="api-url mono">API {API_URL}</span>
      </div>
    </header>
  );
}
