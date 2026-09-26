"use client";

import { useCallback, useEffect, useState } from "react";
import { EventBody } from "@/components/EventView";
import { ProviderField, pickDefaultProvider } from "@/components/ProviderField";
import { Banner, Card, Chip, Metric, Pre, Tick } from "@/components/ui";
import { api, ApiError, type Provider, type Scorecard, type StageResult, type StageRow, type Task } from "@/lib/api";
import { fmtMs, fmtNumber, label } from "@/lib/format";

export default function StagesPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [taskId, setTaskId] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [fresh, setFresh] = useState(false);
  const [rows, setRows] = useState<StageRow[]>([]);
  const [selected, setSelected] = useState<string>();
  const [result, setResult] = useState<StageResult>();
  const [running, setRunning] = useState<string>();
  const [error, setError] = useState<string>();
  const [disabled, setDisabled] = useState(false);

  useEffect(() => {
    Promise.all([api.tasks(), api.providers()])
      .then(([t, p]) => {
        setTasks(t);
        setProviders(p);
        setTaskId((current) => current || t[0]?.id || "");
        setProvider((current) => current || pickDefaultProvider(p));
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const refresh = useCallback(async (id: string) => {
    try {
      setRows((await api.stageStatus(id)).agents);
      setDisabled(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404 && e.message.includes("disabled")) setDisabled(true);
      else setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    if (!taskId) return;
    setSelected(undefined);
    setResult(undefined);
    setError(undefined);
    refresh(taskId);
  }, [taskId, refresh]);

  async function run(agent: string) {
    setRunning(agent);
    setSelected(agent);
    setError(undefined);
    try {
      setResult(await api.runStage({ agent, task_id: taskId, provider, model: model.trim() || undefined, fresh }));
      await refresh(taskId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(undefined);
    }
  }

  async function show(agent: string) {
    setSelected(agent);
    setError(undefined);
    try {
      setResult(await api.stageResult(taskId, agent));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const task = tasks.find((t) => t.id === taskId);

  return (
    <>
      <h1>One agent at a time</h1>
      <p className="subtitle">
        Run a single agent on a task, look at exactly what it was given and what it produced, then run the next one. Each
        agent continues from the previous agent&apos;s saved output. Running the planner always starts from the untouched app.
      </p>
      {disabled ? (
        <Banner tone="warn">
          This page is switched off. Set <span className="mono">AGENTEVAL_ENABLE_DEV_ENDPOINTS=true</span> in the API&apos;s
          environment (or <span className="mono">.env</span>) and restart the API. It is off by default because running agents
          spends LLM credits and starts Docker containers.
        </Banner>
      ) : null}
      {error ? <Banner tone="bad">{error}</Banner> : null}

      <div className="grid-2">
        <div className="stack">
          {result ? <ResultPanel result={result} /> : <Card>Pick an agent on the right and press Run.</Card>}
        </div>

        <div className="stack">
          <Card title="Setup">
            <div className="stack">
              <div>
                <label htmlFor="task">Task</label>
                <select id="task" value={taskId} onChange={(e) => setTaskId(e.target.value)}>
                  {tasks.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title} ({t.difficulty})
                    </option>
                  ))}
                </select>
              </div>
              {task ? <div className="task-text">{task.task}</div> : null}
              <ProviderField
                providers={providers}
                provider={provider}
                model={model}
                onProvider={setProvider}
                onModel={setModel}
              />
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 400, color: "var(--text)" }}>
                <input type="checkbox" style={{ width: "auto" }} checked={fresh} onChange={(e) => setFresh(e.target.checked)} />
                Discard earlier results before running
              </label>
            </div>
          </Card>

          <Card title="Agents">
            <div className="agent-list">
              {rows.map((row) => (
                <div key={row.agent} className="row" style={{ flexWrap: "nowrap" }}>
                  <button
                    className={`agent-btn ${selected === row.agent ? "selected" : ""}`}
                    disabled={!row.ran || running !== undefined}
                    onClick={() => show(row.agent)}
                    title={row.ran ? "Show the last result" : "Not run yet"}
                  >
                    {running === row.agent ? <span className="spinner" /> : <Tick ok={row.ran ? row.ok : null} />}
                    <span style={{ flex: 1 }}>{label(row.agent)}</span>
                    <span className="muted num">
                      {row.ran ? `${fmtMs(row.latency_ms)} · ${fmtNumber((row.tokens_in ?? 0) + (row.tokens_out ?? 0))} tok` : ""}
                    </span>
                  </button>
                  <button
                    className="primary"
                    disabled={disabled || running !== undefined || !taskId || !provider}
                    onClick={() => run(row.agent)}
                  >
                    Run
                  </button>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}

function ResultPanel({ result }: { result: StageResult }) {
  const usage = result.event.token_usage;
  return (
    <div className="stack">
      <Card>
        <div className="row">
          <h2 style={{ margin: 0, fontSize: 16 }}>{label(result.agent)}</h2>
          <Chip tone={result.ok ? "ok" : "bad"}>{result.ok ? "ok" : "failed"}</Chip>
          <span className="muted">
            {result.transcript[0]?.model ?? "no LLM call"} · {fmtMs(result.event.latency_ms)} ·{" "}
            {fmtNumber(usage.input)} in / {fmtNumber(usage.output)} out tokens · {usage.llm_calls} LLM call
            {usage.llm_calls === 1 ? "" : "s"}
          </span>
        </div>
      </Card>
      {result.notes.map((note) => (
        <Banner key={note} tone="warn">
          {note}
        </Banner>
      ))}
      {result.error ? <Banner tone="bad">{result.error}</Banner> : null}
      {result.scorecard ? <ScorecardCard scorecard={result.scorecard} /> : null}
      <Card title="Result">
        <div className="step-body" style={{ border: 0, padding: 0 }}>
          <EventBody agent={result.agent} status={result.event.status} output={result.event.output} />
        </div>
      </Card>
      {result.transcript.map((exchange, i) => (
        <Card key={i} title={`LLM call ${i + 1}: what the agent was given and what came back`}>
          <div className="stack">
            <details>
              <summary className="muted">System prompt ({exchange.system.length.toLocaleString()} characters)</summary>
              <Pre>{exchange.system}</Pre>
            </details>
            <details>
              <summary className="muted">Prompt ({exchange.prompt.length.toLocaleString()} characters)</summary>
              <Pre>{exchange.prompt}</Pre>
            </details>
            <details open>
              <summary className="muted">
                Raw reply ({fmtNumber(exchange.input_tokens)} tokens in, {fmtNumber(exchange.output_tokens)} out)
              </summary>
              <Pre>{exchange.response}</Pre>
            </details>
          </div>
        </Card>
      ))}
    </div>
  );
}

function ScorecardCard({ scorecard }: { scorecard: Scorecard }) {
  const chips = (files: string[], tone?: "ok" | "bad") =>
    files.length ? (
      files.map((f) => (
        <Chip key={f} tone={tone}>
          {f}
        </Chip>
      ))
    ) : (
      <span className="muted">none</span>
    );

  if (scorecard.kind === "draft") {
    return (
      <Card title="Scored against the hidden acceptance test">
        <div className="stack">
          <div className="metrics">
            <Metric label="verdict on the code" value={scorecard.acceptance_passed ? "passed" : "failed"} />
            <Metric
              label="hidden tests passed"
              value={`${scorecard.acceptance_tests_passed}/${scorecard.acceptance_tests_total}`}
            />
          </div>
          <div className="row">
            <span className="muted">Files written:</span> {chips(scorecard.files_written)}
          </div>
          <div className="row">
            <span className="muted">Planned but not written:</span> {chips(scorecard.planned_not_written, "bad")}
          </div>
          <div className="row">
            <span className="muted">Written but not planned:</span> {chips(scorecard.written_not_planned, "bad")}
          </div>
        </div>
      </Card>
    );
  }

  const pct = (v: number) => `${Math.round(v * 100)}%`;
  return (
    <Card title="Scored against the reference solution">
      <div className="stack">
        <div className="metrics">
          <Metric
            label={`recall (${scorecard.found.length}/${scorecard.needed.length} needed files read)`}
            value={pct(scorecard.recall)}
          />
          <Metric label="precision (files read that were needed or helpful)" value={pct(scorecard.precision)} />
        </div>
        <div className="row">
          <span className="muted">Needed files it missed:</span> {chips(scorecard.missed, "bad")}
        </div>
        <div className="row">
          <span className="muted">Read but not useful:</span> {chips(scorecard.irrelevant, "bad")}
        </div>
      </div>
    </Card>
  );
}
