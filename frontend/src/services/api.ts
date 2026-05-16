/** Low-level SSE client using fetch + ReadableStream (POST). */

export type SseHandler = (event: string, data: Record<string, unknown>) => void;

/**
 * POST JSON body to an SSE endpoint and invoke handler per parsed event.
 *
 * @param url - Absolute or same-origin URL.
 * @param body - JSON-serializable request body.
 * @param onEvent - Callback receiving ``event`` name and parsed JSON ``data``.
 * @param signal - Optional AbortSignal for cancellation.
 *
 * @returns Promise that resolves when the stream ends.
 */
export async function streamSsePost(
  url: string,
  body: unknown,
  onEvent: SseHandler,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "");
    throw new Error(`SSE request failed: ${res.status} ${text}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];

  const flush = () => {
    if (!dataLines.length) {
      return;
    }
    const raw = dataLines.join("\n");
    dataLines = [];
    try {
      const data = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
      onEvent(eventName, data);
    } catch {
      onEvent(eventName, { raw });
    }
    eventName = "message";
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n");
    buffer = parts.pop() ?? "";
    for (const line of parts) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      } else if (line === "") {
        flush();
      }
    }
  }
  flush();
}
