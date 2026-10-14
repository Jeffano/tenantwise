// Client for the TenantWise API.
// Why fetch + ReadableStream instead of EventSource: EventSource only supports GET,
// and questions are sent as a POST body so they never appear in URLs or server access logs.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export type Citation = {
  n: number;
  section_ref: string;
  heading_path: string;
  url: string;
  source_title: string;
};

export type DonePayload = {
  citations: Citation[];
  citations_valid?: boolean;
  log_id: number;
  cached?: boolean;
};

type Handlers = {
  onMeta: (route: string) => void;
  onToken: (text: string) => void;
  onDone: (payload: DonePayload) => void;
  onError: (message: string) => void;
};

export async function ask(question: string, h: Handlers, signal?: AbortSignal): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
      signal,
    });
  } catch {
    h.onError("Could not reach TenantWise. Check your connection and try again.");
    return;
  }
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => null);
    h.onError(detail?.detail ?? `Request failed (${res.status}). Try again in a moment.`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE events are separated by a blank line.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      let data = "";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      const parsed = data ? JSON.parse(data) : null;
      if (event === "meta") h.onMeta(parsed.route);
      else if (event === "token") h.onToken(parsed);
      else if (event === "done") h.onDone(parsed);
      else if (event === "error") h.onError(parsed);
    }
  }
}

export async function sendFeedback(logId: number, value: 1 | -1): Promise<void> {
  await fetch(`${API_URL}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ log_id: logId, value }),
  }).catch(() => undefined);
}
