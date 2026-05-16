import { FormEvent, useMemo, useState } from "react";
import { useAgentChat } from "../hooks/useAgentChat";
import { ThinkingSteps } from "./ThinkingSteps";

const QUICK: string[] = [
  "我的退款什么时候到账？",
  "请帮我审核交易 TXN-ABC123 的风控状态",
  "查询我的钱包余额和最近账单",
  "我要转人工",
];

/**
 * Embedded CS widget: transcript, composer, quick prompts, human handoff.
 */
export function ChatWidget() {
  const { messages, thinking, streaming, error, reconnectAttempt, sendMessage, stop } = useAgentChat();
  const [input, setInput] = useState("");
  const disabled = useMemo(() => streaming, [streaming]);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const t = input.trim();
    if (!t) {
      return;
    }
    setInput("");
    await sendMessage(t);
  };

  return (
    <div className="flex h-[min(720px,85vh)] flex-col rounded-2xl border border-slate-800 bg-slate-900/60 shadow-xl shadow-brand-900/20 backdrop-blur">
      <header className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div>
          <div className="text-sm font-semibold text-white">智能客服</div>
          <div className="text-xs text-slate-500">LangGraph · SSE · 预留 WebSocket</div>
        </div>
        <div className="flex gap-2">
          {streaming ? (
            <button
              type="button"
              className="rounded-md border border-amber-700 px-2 py-1 text-xs text-amber-200 hover:bg-amber-900/40"
              onClick={() => stop()}
            >
              停止
            </button>
          ) : null}
        </div>
      </header>

      <ThinkingSteps items={thinking} />

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {messages.length === 0 ? (
          <p className="text-sm text-slate-500">你好，我是多智能体客服，试试下方快捷问题。</p>
        ) : null}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                m.role === "user"
                  ? "bg-brand-700 text-white"
                  : "border border-slate-800 bg-slate-950/80 text-slate-100"
              }`}
            >
              {m.content ||
                (m.status === "streaming"
                  ? streaming && m.role === "assistant"
                    ? "正在处理（模型可能自动重试，见下方执行轨迹）…"
                    : "…"
                  : "")}
            </div>
          </div>
        ))}
      </div>

      <div className="border-t border-slate-800 px-3 py-2">
        <div className="mb-2 flex flex-wrap gap-2">
          {QUICK.map((q) => (
            <button
              key={q}
              type="button"
              disabled={disabled}
              className="rounded-full border border-slate-700 bg-slate-900 px-3 py-1 text-xs text-slate-200 hover:border-brand-500 hover:text-white disabled:opacity-40"
              onClick={() => void sendMessage(q)}
            >
              {q}
            </button>
          ))}
        </div>
        {error ? (
          <div className="mb-2 text-xs text-red-400">
            {error}
            {reconnectAttempt ? `（已重试 ${reconnectAttempt} 次）` : null}
          </div>
        ) : null}
        <form className="flex gap-2" onSubmit={(e) => void onSubmit(e)}>
          <input
            className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none ring-brand-500 focus:ring"
            placeholder="描述你的支付 / 风控 / 钱包问题…"
            value={input}
            disabled={disabled}
            onChange={(e) => setInput(e.target.value)}
          />
          <button
            type="submit"
            disabled={disabled || !input.trim()}
            className="rounded-xl bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-40"
          >
            发送
          </button>
        </form>
      </div>
    </div>
  );
}
