import { ChatWidget } from "./components/ChatWidget";

/**
 * Demo page wiring the CS widget.
 */
export default function App() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 px-4 py-10">
      <div className="mx-auto max-w-3xl">
        <h1 className="mb-2 text-2xl font-bold tracking-tight text-white">多智能体客服控制台</h1>
        <p className="mb-6 text-sm text-slate-400">
          后端：<code className="text-brand-400">FastAPI + LangGraph + Qdrant + Redis</code>
          ，流式通道为 SSE（<code className="text-brand-400">/api/chat/stream</code>
          ）；WebSocket 通道已在后端预留演示端点。
        </p>
        <ChatWidget />
      </div>
    </div>
  );
}
