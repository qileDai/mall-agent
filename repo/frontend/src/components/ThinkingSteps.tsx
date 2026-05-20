import type { ThinkingEntry } from "../hooks/useAgentChat";

interface ThinkingStepsProps {
  items: ThinkingEntry[];
}

/**
 * Live timeline of agent ``thinking`` / ``tool_call`` events from SSE.
 */
export function ThinkingSteps({ items }: ThinkingStepsProps) {
  if (!items.length) {
    return null;
  }
  return (
    <div className="mb-3 max-h-40 overflow-y-auto rounded-lg border border-slate-800 bg-slate-900/80 p-2 text-xs text-slate-300">
      <div className="mb-1 font-semibold text-slate-400">执行轨迹</div>
      <ul className="space-y-1">
        {items.map((t) => (
          <li key={t.id} className="font-mono break-all">
            <span className="text-brand-500">[{t.kind}]</span>{" "}
            {JSON.stringify(t.payload)}
          </li>
        ))}
      </ul>
    </div>
  );
}
