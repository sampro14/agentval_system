import type { Provider } from "@/lib/api";

/** The provider to preselect: the first configured real one, else the offline fake. */
export function pickDefaultProvider(providers: Provider[]): string {
  return (providers.find((p) => p.configured && p.name !== "fake") ?? providers.find((p) => p.name === "fake"))?.name ?? "";
}

interface Props {
  providers: Provider[];
  provider: string;
  model: string;
  onProvider: (name: string) => void;
  onModel: (model: string) => void;
}

/** Provider dropdown (unconfigured ones are disabled, with the variable to set) plus an optional model override. */
export function ProviderField({ providers, provider, model, onProvider, onModel }: Props) {
  const selected = providers.find((p) => p.name === provider);
  return (
    <>
      <div>
        <label htmlFor="provider">LLM provider</label>
        <select id="provider" value={provider} onChange={(e) => onProvider(e.target.value)}>
          {providers.map((p) => (
            <option key={p.name} value={p.name} disabled={!p.configured}>
              {p.name}
              {p.configured ? "" : ` (set ${p.key_env_vars.join(" or ")} in the API's environment)`}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor="model">Model (optional)</label>
        <input
          id="model"
          value={model}
          placeholder={selected?.default_model ?? ""}
          onChange={(e) => onModel(e.target.value)}
        />
      </div>
    </>
  );
}
