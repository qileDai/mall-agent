import { useCallback, useMemo, useRef, useState } from "react";
import { streamSsePost } from "../services/api";

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  /** Client-generated stable id. */
  id: string;
  role: ChatRole;
  content: string;
  status?: "streaming" | "done" | "error";
}

export interface ThinkingEntry {
  id: string;
  kind: "thinking" | "tool_call" | "error";
  payload: Record<string, unknown>;
  ts: number;
}

export interface UseAgentChatOptions {
  /** POST endpoint for SSE, e.g. ``/api/chat/stream``. */
  endpoint?: string;
  /** Initial session id; if omitted, generated once. */
  sessionId?: string;
  /** Max automatic reconnect attempts after network errors. */
  maxReconnect?: number;
}

/**
 * Manage multi-turn chat over SSE with typing effect hooks and reconnect backoff.
 *
 * @param options - Endpoint and session configuration.
 * @returns Chat state and actions for UI binding.
 */
export function useAgentChat(options: UseAgentChatOptions = {}) {
  const endpoint = options.endpoint ?? "/api/chat/stream";
  const sessionRef = useRef(options.sessionId ?? crypto.randomUUID());
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [thinking, setThinking] = useState<ThinkingEntry[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);

  const pushThinking = useCallback((kind: ThinkingEntry["kind"], payload: Record<string, unknown>) => {
    setThinking((prev) => [
      ...prev,
      { id: crypto.randomUUID(), kind, payload, ts: Date.now() },
    ]);
  }, []);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streamingRef.current) {
        return;
      }
      setError(null);
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
        status: "done",
      };
      const asstId = crypto.randomUUID();
      const asstMsg: ChatMessage = {
        id: asstId,
        role: "assistant",
        content: "",
        status: "streaming",
      };
      setMessages((m) => [...m, userMsg, asstMsg]);
      setThinking([]);
      streamingRef.current = true;
      setStreaming(true);
      const max = options.maxReconnect ?? 5;
      let attempt = 0;
      let buf = "";
      let receivedAny = false;
      try {
        while (attempt <= max) {
          try {
            if (!receivedAny) {
              buf = "";
            }
            await streamSsePost(
              endpoint,
              { message: trimmed, session_id: sessionRef.current },
              (event, data) => {
                receivedAny = true;
                if (event === "thinking" || event === "tool_call") {
                  pushThinking(event, data);
                } else if (event === "token") {
                  const piece = typeof data.text === "string" ? data.text : "";
                  buf += piece;
                  setMessages((prev) =>
                    prev.map((x) => (x.id === asstId ? { ...x, content: buf } : x)),
                  );
                } else if (event === "error") {
                  const msg = typeof data.message === "string" ? data.message : "unknown error";
                  pushThinking("error", data);
                  setError(msg);
                } else if (event === "done") {
                  if (typeof data.session_id === "string") {
                    sessionRef.current = data.session_id;
                  }
                }
              },
              abortRef.current.signal,
            );
            setReconnectAttempt(0);
            setMessages((prev) =>
              prev.map((x) => (x.id === asstId ? { ...x, status: "done" } : x)),
            );
            break;
          } catch (e) {
            if (abortRef.current.signal.aborted) {
              break;
            }
            const msg = e instanceof Error ? e.message : String(e);
            if (receivedAny) {
              setError(msg);
              setMessages((prev) =>
                prev.map((x) =>
                  x.id === asstId ? { ...x, status: "error", content: x.content || msg } : x,
                ),
              );
              break;
            }
            if (attempt >= max) {
              setError(msg);
              setMessages((prev) =>
                prev.map((x) =>
                  x.id === asstId ? { ...x, status: "error", content: x.content || msg } : x,
                ),
              );
              break;
            }
            setReconnectAttempt(attempt + 1);
            const delay = Math.min(30_000, 800 * 2 ** attempt);
            await new Promise((r) => setTimeout(r, delay));
            attempt += 1;
          }
        }
      } finally {
        streamingRef.current = false;
        setStreaming(false);
      }
    },
    [endpoint, options.maxReconnect, pushThinking],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const sessionId = useMemo(() => sessionRef.current, []);

  return {
    sessionId,
    messages,
    thinking,
    streaming,
    error,
    reconnectAttempt,
    sendMessage,
    stop,
  };
}
