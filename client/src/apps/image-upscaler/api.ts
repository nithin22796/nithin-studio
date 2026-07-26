import type { Scale, UpscaleJob } from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function startUpscale(file: File, scale: Scale): Promise<{ job_id: string }> {
  const body = new FormData();
  body.append("file", file);
  return handle(
    await fetch(`${API_URL}/image-upscaler/upscale?scale=${scale}`, {
      method: "POST",
      body,
    }),
  );
}

export async function getUpscaleJob(jobId: string): Promise<UpscaleJob> {
  return handle(await fetch(`${API_URL}/image-upscaler/jobs/${jobId}`));
}

export async function cancelUpscale(jobId: string): Promise<UpscaleJob> {
  return handle(await fetch(`${API_URL}/image-upscaler/jobs/${jobId}/cancel`, { method: "POST" }));
}
