import type { GeneratedImage, Session } from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const BASE = `${API_URL}/image-generator`;

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      payload?.detail ?? `request failed with status ${response.status}`,
    );
  }
  return response.json() as Promise<T>;
}

export async function getCurrentSession(): Promise<Session | null> {
  return handle(await fetch(`${BASE}/sessions/current`));
}

export async function listModels(): Promise<string[]> {
  const { models } = await handle<{ models: string[] }>(
    await fetch(`${BASE}/models`),
  );
  return models;
}

export async function startSession(model: string): Promise<Session> {
  return handle(
    await fetch(`${BASE}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }),
  );
}

export async function stopSession(id: number): Promise<Session> {
  return handle(await fetch(`${BASE}/sessions/${id}/stop`, { method: "POST" }));
}

export async function generate(
  prompt: string,
  steps: number,
): Promise<GeneratedImage> {
  return handle(
    await fetch(`${BASE}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, steps }),
    }),
  );
}
