"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Provider, type Task } from "@/lib/api";
import { Banner, Card } from "./ui";
import { ProviderField, pickDefaultProvider } from "./ProviderField";

export function NewRunForm() {
  const router = useRouter();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [taskId, setTaskId] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [repairs, setRepairs] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

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

  const task = tasks.find((t) => t.id === taskId);

  async function start() {
    setBusy(true);
    setError(undefined);
    try {
      const run = await api.createRun({
        task_id: taskId,
        provider,
        model: model.trim() || undefined,
        max_repair_iterations: repairs,
      });
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <Card title="Start a run">
      <div className="stack">
        {error ? <Banner tone="bad">{error}</Banner> : null}
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
        <div>
          <label htmlFor="repairs">Repair attempts allowed</label>
          <input
            id="repairs"
            type="number"
            min={0}
            max={10}
            value={repairs}
            onChange={(e) => setRepairs(Math.min(10, Math.max(0, Number(e.target.value) || 0)))}
          />
        </div>
        <button className="primary" disabled={busy || !taskId || !provider} onClick={start}>
          {busy ? "Starting…" : "Start run"}
        </button>
      </div>
    </Card>
  );
}
