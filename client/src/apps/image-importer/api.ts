import type { ImportJob, PreviewJob } from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const BASE = `${API_URL}/image-importer`;

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

// `temp_url` from the server is a path like "/image-importer/temp/<job>/<id>" —
// this turns it into a full URL the browser can actually load.
export function tempImageSrc(tempUrl: string): string {
  return `${API_URL}${tempUrl}`;
}

export async function startPreview(url: string): Promise<{ job_id: string }> {
  return handle(
    await fetch(`${BASE}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    }),
  );
}

export async function getPreviewJob(jobId: string): Promise<PreviewJob> {
  return handle(await fetch(`${BASE}/preview-jobs/${jobId}`));
}

// Fire-and-forget: called on unmount / when switching to a different page
// URL, where there's no useful way to react to a failure — `keepalive` lets
// the request survive the page/component tearing down mid-flight.
export function discardPreviewJob(jobId: string): void {
  fetch(`${BASE}/preview-jobs/${jobId}/discard`, { method: "POST", keepalive: true }).catch(
    () => {},
  );
}

export async function startImport(
  previewJobId: string,
  imageIds: string[],
  folderId: number | null,
): Promise<{ job_id: string }> {
  return handle(
    await fetch(`${BASE}/import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preview_job_id: previewJobId, image_ids: imageIds, folder_id: folderId }),
    }),
  );
}

export async function getImportJob(jobId: string): Promise<ImportJob> {
  return handle(await fetch(`${BASE}/jobs/${jobId}`));
}

export async function cancelImport(jobId: string): Promise<ImportJob> {
  return handle(await fetch(`${BASE}/jobs/${jobId}/cancel`, { method: "POST" }));
}
