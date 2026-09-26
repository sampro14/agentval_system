import type { Json } from "@/lib/api";
import { fmtNumber } from "@/lib/format";
import { Banner, Chip, DiffView, JsonBlock, Metric, Pre, Tick } from "./ui";

type Output = Record<string, Json>;

const list = (value: Json): Json[] => (Array.isArray(value) ? value : []);

/** One line describing what an agent produced, shown on the collapsed step. */
export function summarize(agent: string, output: Output): string {
  switch (agent) {
    case "planner": {
      const issues = list(output.quality?.issues).length;
      return `${output.task_count ?? "?"} tasks${issues ? `, ${issues} quality issue${issues > 1 ? "s" : ""}` : ""}`;
    }
    case "research":
      return `${list(output.files_selected).length} of ${output.files_available ?? "?"} files read`;
    case "draft": {
      const retried = output.attempts > 1 ? ", retried" : "";
      return `${list(output.files_modified).length} files, +${output.lines_added ?? 0} −${output.lines_removed ?? 0}${retried}`;
    }
    case "repair":
      return `attempt ${output.iteration ?? "?"}: ${list(output.files_modified).length} files`;
    case "execute": {
      const tests = output.tests;
      return tests ? `${tests.passed}/${tests.total} tests passed` : "no test report";
    }
    case "validate":
      return output.passed ? "all checks passed" : `failed: ${list(output.failed_checks).join(", ")}`;
    case "analyze_failure":
      return `${output.category} → ${output.stage}`;
    case "evaluate":
      return output.task_success ? "task succeeded" : "task not solved";
    default:
      return "";
  }
}

export function EventBody({ agent, status, output }: { agent: string; status: string; output: Output }) {
  if (status === "error" || output.error) {
    return (
      <Banner tone="bad">
        <strong>{agent} failed:</strong> <span className="mono">{String(output.error ?? "unknown error")}</span>
      </Banner>
    );
  }
  switch (agent) {
    case "planner":
      return <PlannerView o={output} />;
    case "research":
      return <ResearchView o={output} />;
    case "draft":
    case "repair":
      return <CodeView o={output} />;
    case "execute":
      return <ExecutionView o={output} />;
    case "validate":
      return <ValidationView o={output} />;
    case "analyze_failure":
      return <FailureView o={output} />;
    case "evaluate":
      return <EvaluationView o={output} />;
    default:
      return <JsonBlock value={output} />;
  }
}

function PlannerView({ o }: { o: Output }) {
  const issues: string[] = list(o.quality?.issues);
  const tasks = list(o.plan?.tasks);
  return (
    <>
      <div>
        <h4>Goal</h4>
        {o.plan?.goal}
      </div>
      <div className="row">
        <Chip>{o.attempts === 1 ? "valid on first attempt" : `needed ${o.attempts} attempts`}</Chip>
        {issues.length === 0 ? <Chip tone="ok">no plan-quality issues</Chip> : null}
      </div>
      {issues.length > 0 ? (
        <Banner tone="warn">
          <strong>Plan-quality issues</strong>
          <ul className="plain">
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </Banner>
      ) : null}
      <table>
        <thead>
          <tr>
            <th>Task</th>
            <th>What</th>
            <th>Files</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              <td className="mono">
                {t.id}
                {list(t.dependencies).length ? <div className="muted">after {t.dependencies.join(", ")}</div> : null}
              </td>
              <td>
                {t.description}
                {t.validation_criteria ? <div className="muted">✓ {t.validation_criteria}</div> : null}
              </td>
              <td className="mono">
                {list(t.files_to_touch).map((f) => (
                  <div key={f}>{f}</div>
                ))}
                {list(t.new_files).map((f) => (
                  <div key={f}>
                    <Chip tone="ok">new</Chip> {f}
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function ResearchView({ o }: { o: Output }) {
  const selected: string[] = list(o.files_selected);
  const reasons: Record<string, string> = o.reasons ?? {};
  const auto = new Set<string>(list(o.auto_included));
  const notes: { label: string; files: Json[] }[] = [
    { label: "Asked for files that do not exist", files: list(o.unknown_files) },
    { label: "Skipped because they are empty", files: list(o.empty_skipped) },
  ].filter((n) => n.files.length > 0);
  return (
    <>
      <div>
        <h4>
          Files the coder will read ({selected.length} of {o.files_available})
        </h4>
        {selected.length ? (
          <table>
            <tbody>
              {selected.map((path) => (
                <tr key={path}>
                  <td className="mono">{path}</td>
                  <td>{auto.has(path) ? <Chip>plan edits it</Chip> : <Chip tone="ok">chosen</Chip>}</td>
                  <td className="muted">{auto.has(path) ? "" : reasons[path]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <span className="muted">none</span>
        )}
      </div>
      {notes.map((n) => (
        <Banner key={n.label} tone="warn">
          {n.label}: <span className="mono">{n.files.join(", ")}</span>
        </Banner>
      ))}
      {list(o.problems).map((problem) => (
        <Banner key={String(problem)} tone="bad">
          {String(problem)}
        </Banner>
      ))}
    </>
  );
}

function CodeView({ o }: { o: Output }) {
  const diffs = Object.entries((o.diffs ?? {}) as Record<string, string>);
  return (
    <>
      <div>
        <h4>{o.action !== undefined ? "Repair" : "Summary"}</h4>
        {o.summary ?? o.action ?? <span className="muted">no description</span>}
      </div>
      {o.lines_added !== undefined ? (
        <div className="row">
          <Chip tone="ok">+{o.lines_added}</Chip>
          <Chip tone="bad">−{o.lines_removed}</Chip>
          <span className="muted mono">{list(o.files_modified).join(", ")}</span>
        </div>
      ) : null}
      {list(o.files_modified).length === 0 ? <Banner tone="warn">No files were written.</Banner> : null}
      {o.attempts > 1 ? <Banner tone="warn">The first reply was rejected and had to be redone (see the run's trajectory for why).</Banner> : null}
      {list(o.plan_check?.planned_not_written).length ? (
        <Banner tone="warn">
          Planned but not written: <span className="mono">{o.plan_check.planned_not_written.join(", ")}</span>
        </Banner>
      ) : null}
      {list(o.plan_check?.written_not_planned).length ? (
        <Banner tone="warn">
          Written but not in the plan: <span className="mono">{o.plan_check.written_not_planned.join(", ")}</span>
        </Banner>
      ) : null}
      {diffs.map(([path, diff]) => (
        <DiffView key={path} path={path} diff={diff} />
      ))}
    </>
  );
}

function ExecutionView({ o }: { o: Output }) {
  const checks = list(o.checks);
  const tests = o.tests;
  return (
    <>
      <div>
        <h4>Checks</h4>
        <table>
          <thead>
            <tr>
              <th />
              <th>Check</th>
              <th>Image</th>
              <th className="num">Exit</th>
              <th className="num">Time</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((c) => (
              <tr key={c.name}>
                <td>
                  <Tick ok={c.passed} />
                </td>
                <td>{c.name}</td>
                <td className="mono">{c.image}</td>
                <td className="num">{c.timed_out ? "timeout" : c.exit_code}</td>
                <td className="num">{c.duration_ms} ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {tests ? (
        <div>
          <h4>Tests</h4>
          <div className="row">
            <Chip tone="ok">{tests.passed} passed</Chip>
            {tests.failed + tests.errors > 0 ? <Chip tone="bad">{tests.failed + tests.errors} failed</Chip> : null}
            {tests.skipped ? <Chip>{tests.skipped} skipped</Chip> : null}
          </div>
          {list(tests.failures).map((f) => (
            <div key={f.test} style={{ marginTop: 8 }}>
              <div className="mono">{f.test}</div>
              <Pre>{f.message}</Pre>
            </div>
          ))}
        </div>
      ) : (
        <Banner tone="warn">No test report was produced (no tests were collected, or the run crashed).</Banner>
      )}
      {checks.map((c) =>
        c.stdout || c.stderr ? (
          <details key={`out-${c.name}`}>
            <summary className="muted">Output of {c.name}</summary>
            <Pre>{`${c.stdout}${c.stderr ? `\n--- stderr ---\n${c.stderr}` : ""}`}</Pre>
          </details>
        ) : null,
      )}
    </>
  );
}

function ValidationView({ o }: { o: Output }) {
  const checks = Object.entries((o.checks ?? {}) as Record<string, boolean>);
  const requirements = list(o.requirements);
  const issues = list(o.issues);
  const regressions = list(o.evidence?.regressions);
  return (
    <>
      <div className="row">
        {checks.map(([name, ok]) => (
          <Chip key={name} tone={ok ? "ok" : "bad"}>
            {ok ? "✓" : "✗"} {name.replaceAll("_", " ")}
          </Chip>
        ))}
      </div>
      {regressions.length ? (
        <Banner tone="bad">
          Tests that passed earlier now fail: <span className="mono">{regressions.join(", ")}</span>
        </Banner>
      ) : null}
      {String(o.evidence?.requirements_review ?? "").startsWith("skipped") ? (
        <div className="muted">Requirement review skipped: {o.evidence.requirements_review.replace("skipped: ", "")}.</div>
      ) : null}
      {requirements.length ? (
        <table>
          <thead>
            <tr>
              <th>Requirement</th>
              <th>Implemented</th>
              <th>Tested</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {requirements.map((r) => (
              <tr key={r.requirement}>
                <td>{r.requirement}</td>
                <td>
                  <Tick ok={r.implemented} />
                </td>
                <td>
                  <Tick ok={r.tested} />
                </td>
                <td className="muted">{r.evidence}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {issues.length ? (
        <ul className="plain">
          {issues.map((i) => (
            <li key={String(i)}>{String(i)}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

function FailureView({ o }: { o: Output }) {
  const evidence = list(o.evidence);
  return (
    <>
      <dl className="kv">
        <dt>Category</dt>
        <dd>
          <Chip tone="bad">{o.category}</Chip>
        </dd>
        <dt>Responsible stage</dt>
        <dd>{o.stage}</dd>
        <dt>Confidence</dt>
        <dd>
          {Math.round((o.confidence ?? 0) * 100)}% <span className="muted">({o.attribution_method} attribution)</span>
        </dd>
        <dt>Root cause</dt>
        <dd>{o.root_cause}</dd>
        <dt>Recommended action</dt>
        <dd>{o.recommended_action}</dd>
        <dt>Failed checks</dt>
        <dd>{list(o.failed_checks).join(", ") || "–"}</dd>
      </dl>
      {evidence.length ? (
        <div>
          <h4>Evidence</h4>
          {evidence.map((e, i) => (
            <Pre key={i}>{String(e)}</Pre>
          ))}
        </div>
      ) : null}
    </>
  );
}

function EvaluationView({ o }: { o: Output }) {
  const yes = (v: Json) => (v ? "yes" : "no");
  return (
    <>
      <div className="metrics">
        <Metric label="task solved" value={yes(o.task_success)} />
        <Metric label="first-pass success" value={yes(o.first_pass_success)} />
        <Metric label="recovered by repair" value={yes(o.recovered)} />
        <Metric label="repair attempts" value={o.repair_iterations} />
        <Metric label="tests passed" value={`${o.tests_passed}/${o.tests_total}`} />
        <Metric label="LLM calls" value={o.llm_calls} />
        <Metric label="tokens" value={fmtNumber(o.total_tokens)} />
        <Metric label="agent time" value={`${((o.total_latency_ms ?? 0) / 1000).toFixed(1)} s`} />
      </div>
      {list(o.failure_categories).length ? (
        <div className="row">
          <span className="muted">Failure categories:</span>
          {list(o.failure_categories).map((c, i) => (
            <Chip key={i} tone="bad">
              {String(c)}
            </Chip>
          ))}
        </div>
      ) : null}
    </>
  );
}
