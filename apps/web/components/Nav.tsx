"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { API_URL, REPLAY } from "@/lib/api";
import { REPO_URL } from "@/lib/replay";

const LINKS = [
  { href: "/", label: "Runs", match: (p: string) => p === "/" || p.startsWith("/runs") },
  { href: "/stages", label: "One agent at a time", match: (p: string) => p.startsWith("/stages") },
].filter((link) => !REPLAY || link.href !== "/stages");

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
        {REPLAY ? (
          <span className="api-url">
            Recorded replay ·{" "}
            <a href={REPO_URL} target="_blank" rel="noopener">
              GitHub
            </a>
          </span>
        ) : (
          <span className="api-url mono">API {API_URL}</span>
        )}
      </div>
    </header>
  );
}
