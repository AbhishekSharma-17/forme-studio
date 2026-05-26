/**
 * Minimal SSE consumer for POST-bodied endpoints.
 *
 * The native `EventSource` is GET-only and can't carry a JSON body, so we
 * roll our own using `fetch` + a ReadableStream. Yields one `SseEvent`
 * per `event: NAME\ndata: …\n\n` frame.
 */

export interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

interface StreamPostOptions {
  url: string;
  body: unknown;
  signal?: AbortSignal;
  onEvent: (event: SseEvent) => void;
  onError?: (err: unknown) => void;
}

export async function streamPost({
  url,
  body,
  signal,
  onEvent,
  onError,
}: StreamPostOptions): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    onError?.(err);
    return;
  }

  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}`;
    try {
      const text = await res.text();
      detail = text || detail;
    } catch {
      /* ignore */
    }
    onError?.(new Error(detail));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let sepIdx: number;
      // SSE frames are separated by a blank line.
      while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);
        const parsed = parseFrame(raw);
        if (parsed) onEvent(parsed);
      }
    }
  } catch (err) {
    if ((err as { name?: string }).name === "AbortError") return;
    onError?.(err);
  }
}

function parseFrame(raw: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  const dataStr = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(dataStr) as Record<string, unknown> };
  } catch {
    return { event, data: { raw: dataStr } };
  }
}
