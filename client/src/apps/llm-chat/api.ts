import type { ChatMessage } from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const BASE = `${API_URL}/llm-chat`;

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

// Stateless server — the full conversation so far is sent on every call and
// nothing is persisted server-side, so history only lives in this page's state.
export async function sendMessage(history: ChatMessage[]): Promise<string> {
  const { reply } = await handle<{ reply: string }>(
    await fetch(`${BASE}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history }),
    }),
  );
  return reply;
}
