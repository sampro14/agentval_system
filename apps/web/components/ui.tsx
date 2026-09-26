import type { ReactNode } from "react";
import type { Json } from "@/lib/api";

const STATUS_STYLE: Record<string, { cls: string; text: string }> = {
  queued: { cls: "neutral", text: "Queued" },
  running: { cls: "info pulse", text: "Running" },
  succeeded: { cls: "ok", text: "Succeeded" },
  recovered: { cls: "warn", text: "Recovered" },
  failed: { cls: "bad", text: "Failed" },
  error: { cls: "bad", text: "Error" },
};

export function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLE[status] ?? { cls: "neutral", text: status };
  return (
    <span className={`badge ${style.cls}`}>
      <span className="dot" />
      {style.text}
    </span>
  );
}

/** Passed / failed / not-available marker for the hidden acceptance test result. */
export function AcceptanceBadge({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="muted">–</span>;
  return value >= 1 ? <span className="badge ok">✓ passed</span> : <span className="badge bad">✗ failed</span>;
}

export function Chip({ children, tone }: { children: ReactNode; tone?: "ok" | "bad" }) {
  return <span className={`chip ${tone ?? ""}`}>{children}</span>;
}

export function Banner({ tone, children }: { tone: "bad" | "warn" | "info"; children: ReactNode }) {
  return <div className={`banner ${tone}`}>{children}</div>;
}

export function Card({ title, children }: { title?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {title ? <div className="card-title">{title}</div> : null}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="metric">
      <div className="v">{value}</div>
      <div className="k">{label}</div>
    </div>
  );
}

export function Pre({ children }: { children: ReactNode }) {
  return <pre>{children}</pre>;
}

export function JsonBlock({ value }: { value: Json }) {
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

/** A unified diff with added and removed lines coloured. */
export function DiffView({ path, diff }: { path: string; diff: string }) {
  return (
    <details className="diff" open>
      <summary className="mono">{path}</summary>
      <pre>
        {diff.split("\n").map((line, i) => {
          const cls = line.startsWith("+++") || line.startsWith("---")
            ? ""
            : line.startsWith("+")
              ? "add"
              : line.startsWith("-")
                ? "del"
                : line.startsWith("@@")
                  ? "hunk"
                  : "";
          return (
            <span key={i} className={cls}>
              {line || " "}
            </span>
          );
        })}
      </pre>
    </details>
  );
}

export function Tick({ ok }: { ok: boolean | null | undefined }) {
  if (ok === null || ok === undefined) return <span className="muted">–</span>;
  return ok ? <span style={{ color: "var(--ok)" }}>✓</span> : <span style={{ color: "var(--bad)" }}>✗</span>;
}
