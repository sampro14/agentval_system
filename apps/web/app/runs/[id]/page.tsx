"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Pipeline } from "@/components/Pipeline";
import { Timeline } from "@/components/Timeline";
import { AcceptanceBadge, Banner, Card, Chip, Metric, StatusBadge } from "@/components/ui";
import { api, isTerminal, type AgentEvent, type Failure, type Metric as MetricRow, type Run, type Task } from "@/lib/api";
import { elapsedMs, fmtMs, fmtNumber } from "@/lib/format";

const POLL_MS = 1500;

/** Follows a run: polls it, appending only the events that are new, until it reaches a final status. */
function useLiveRun(id: string) {
  const [run, setRun] = useState<Run>();
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [failures, setFailures] = useState<Failure[]>([]);
  const [metrics, setMetrics] = useState<MetricRow[]>([]);
  const [error, setError] = useState<Error>();
  const lastSeq = useRef(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    lastSeq.current = 0;
    setRun(undefined);
    setEvents([]);
    setFailures([]);
    setMetrics([]);

    const tick = async () => {
      let finished = false;
      try {
        // Run first, then events: a run that already reports a final status has all of its events saved.
        const latest = await api.run(id);
        const fresh = await api.trajectory(id, lastSeq.current);
        finished = isTerminal(latest.status);
        const [f, m] = finished ? await Promise.all([api.failures(id), api.evaluation(id)]) : [[], []];
        if (cancelled) return;
        if (fresh.length) {
          lastSeq.current = fresh[fresh.length - 1].seq;
          setEvents((prev) => [...prev, ...fresh]);
        }
        setRun(latest);
        if (finished) {
          setFailures(f);
          setMetrics(m);
        }
        setError(undefined);
      } catch (e) {
        if (!cancelled) setError(e as Error);
      }
      if (!cancelled && !finished) timer = setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  return { run, events, failures, metrics, error };
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const { run, events, failures, metrics, error } = useLiveRun(id);
  const [task, setTask] = useState<Task>();
  const running = run ? !isTerminal(run.status) : true;
  const now = useNow(running);

  useEffect(() => {
    if (!run?.task_id) return;
    api
      .tasks()
      .then((all) => setTask(all.find((t) => t.id === run.task_id)))
      .catch(() => undefined);
  }, [run?.task_id]);

  const metric = (name: string) => metrics.find((m) => m.metric === name)?.score;
  const tokens = events.reduce((sum, e) => sum + e.token_usage.input + e.token_usage.output, 0);
  const elapsed = run ? elapsedMs(run.started_at ?? run.created_at, run.completed_at, now) : null;

  return (
    <>
      <p className="muted" style={{ margin: "0 0 8px" }}>
        <Link href="/">← All runs</Link>
      </p>
      {error ? <Banner tone="bad">{error.message}</Banner> : null}
      {!run ? (
        error ? null : (
          <span className="muted">Loading…</span>
        )
      ) : (
        <div className="stack">
          <div>
            <div className="row">
              <h1 style={{ margin: 0 }}>{task?.title ?? run.task_id ?? "Run"}</h1>
              <StatusBadge status={run.status} />
              <AcceptanceBadge value={run.acceptance_passed} />
            </div>
            <p className="subtitle" style={{ margin: "4px 0 0" }}>
              {run.provider} · <span className="mono">{run.model}</span> · {fmtMs(elapsed)} · {fmtNumber(tokens)} tokens ·{" "}
              <span className="mono">{run.id.slice(0, 8)}</span>
            </p>
          </div>

          <details>
            <summary className="muted">Task</summary>
            <div className="task-text" style={{ marginTop: 8 }}>
              {run.task}
            </div>
          </details>

          <Card>
            <Pipeline events={events} running={running} />
          </Card>

          {run.error ? <Banner tone="bad">Run stopped with an error: {run.error}</Banner> : null}

          {!running ? (
            <Card title="Result">
              <div className="stack">
                <div className="metrics">
                  <Metric label="outcome" value={run.status} />
                  <Metric
                    label="hidden acceptance test"
                    value={
                      metric("acceptance_tests_total") !== undefined
                        ? `${metric("acceptance_tests_passed")}/${metric("acceptance_tests_total")} passed`
                        : "not run"
                    }
                  />
                  {metric("retrieval_recall") !== undefined ? (
                    <Metric
                      label="research: needed files read / files read that were useful"
                      value={`${Math.round((metric("retrieval_recall") ?? 0) * 100)}% / ${Math.round((metric("retrieval_precision") ?? 0) * 100)}%`}
                    />
                  ) : null}
                  <Metric label="first-pass success" value={metric("first_pass_success") ? "yes" : "no"} />
                  <Metric label="repair attempts" value={metric("repair_iterations") ?? 0} />
                  <Metric label="tokens" value={fmtNumber(run.total_tokens ?? tokens)} />
                  <Metric label="agent time" value={fmtMs(run.total_latency_ms)} />
                </div>
                {failures.length ? (
                  <div className="row">
                    <span className="muted">Failures:</span>
                    {failures.map((f) => (
                      <Chip key={f.id} tone={f.resolved ? "ok" : "bad"}>
                        {f.category} → {f.stage}
                        {f.resolved ? " (fixed)" : ""}
                      </Chip>
                    ))}
                  </div>
                ) : null}
              </div>
            </Card>
          ) : null}

          <div>
            <h2 style={{ fontSize: 15, margin: "8px 0" }}>
              Steps {running ? <span className="muted">(updating live)</span> : null}
            </h2>
            {events.length === 0 ? (
              <span className="muted">
                {run.status === "queued" ? "Waiting for the worker to pick this run up…" : "Waiting for the first agent…"}
              </span>
            ) : (
              <Timeline events={events} />
            )}
          </div>
        </div>
      )}
    </>
  );
}
