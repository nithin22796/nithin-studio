import * as fileManagerApi from "../file-manager/api";
import type { Job } from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const BASE = `${API_URL}/lora-trainer`;

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

/** Every dataset image — whether picked from file-manager or freshly
 * uploaded here — is identified by a single `fileManagerId`-shaped number.
 * Fresh uploads never touch file-manager/MinIO: they're staged on local
 * disk server-side and given a *negative* id so this one id space can
 * still be threaded through the whole wizard unchanged; these two
 * functions are the only places that need to know the difference. */
export function uploadDatasetImage(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<{ file_manager_id: number; name: string }> {
  const body = new FormData();
  body.append("file", file);

  // XMLHttpRequest, not fetch — fetch has no way to observe upload progress.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/uploads`);
    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as { file_manager_id: number; name: string });
        return;
      }
      let detail = `request failed with status ${xhr.status}`;
      try {
        detail = JSON.parse(xhr.responseText)?.detail ?? detail;
      } catch {
        // response wasn't JSON — fall back to the generic message above
      }
      reject(new Error(detail));
    };
    xhr.onerror = () => reject(new Error("network error during upload"));
    xhr.send(body);
  });
}

export function datasetImageUrl(
  fileManagerId: number,
  disposition: "inline" | "attachment" = "attachment",
): string {
  if (fileManagerId < 0) {
    return `${BASE}/uploads/content?id=${fileManagerId}&disposition=${disposition}`;
  }
  return fileManagerApi.fileContentUrl(fileManagerId, disposition);
}

export async function captionImage(fileManagerId: number): Promise<string> {
  const result = await handle<{ caption: string }>(
    await fetch(`${BASE}/caption`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_manager_id: fileManagerId }),
    }),
  );
  return result.caption;
}

export interface CreateJobPayload {
  trigger_word: string;
  steps: number;
  rank: number;
  alpha: number;
  images: { file_manager_id: number; caption: string; crop?: number[] | null }[];
}

export async function createJob(payload: CreateJobPayload): Promise<Job> {
  return handle(
    await fetch(`${BASE}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function listJobs(): Promise<Job[]> {
  return handle(await fetch(`${BASE}/jobs`));
}

export async function getJob(id: number): Promise<Job> {
  return handle(await fetch(`${BASE}/jobs/${id}`));
}

/** Marks a finished job as seen — it stays in Postgres, just stops showing
 * up in `listJobs`. Only a job in a terminal state can be dismissed. */
export async function dismissJob(id: number): Promise<Job> {
  return handle(await fetch(`${BASE}/jobs/${id}/dismiss`, { method: "POST" }));
}

/** Stops a queued/running job: terminates its instance and deletes its S3
 * dataset/output right away — unlike a natural failure, there's nothing to
 * keep around for a retry since this is a deliberate user action. */
export async function cancelJob(id: number): Promise<Job> {
  return handle(await fetch(`${BASE}/jobs/${id}/cancel`, { method: "POST" }));
}

/** Relaunches a failed job against its existing S3 dataset — no re-upload
 * needed, since a natural failure keeps the dataset around (unlike a
 * cancellation). Only available while that dataset still exists (the
 * bucket's lifecycle rule expires it after about a day). */
export async function retryJob(id: number): Promise<Job> {
  return handle(await fetch(`${BASE}/jobs/${id}/retry`, { method: "POST" }));
}

/** Current train.log content for a job — re-uploaded every ~15s while
 * training runs, not just once at the end, so a still-running job's log
 * can actually be inspected. Throws if nothing's been uploaded yet. */
export async function getJobLog(id: number): Promise<string> {
  const response = await fetch(`${BASE}/jobs/${id}/log`);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `request failed with status ${response.status}`);
  }
  return response.text();
}

export interface DuplicateCheckJob {
  status: "running" | "succeeded" | "failed" | "cancelled";
  groups: number[][];
  error: string | null;
  progress: number;
}

export async function startDuplicateCheck(
  fileManagerIds: number[],
): Promise<{ job_id: string }> {
  return handle(
    await fetch(`${BASE}/duplicates`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_manager_ids: fileManagerIds }),
    }),
  );
}

export async function getDuplicateCheck(jobId: string): Promise<DuplicateCheckJob> {
  return handle(await fetch(`${BASE}/duplicates/${jobId}`));
}

export async function cancelDuplicateCheck(jobId: string): Promise<void> {
  await fetch(`${BASE}/duplicates/${jobId}/cancel`, { method: "POST" });
}

export interface PeopleIdentity {
  embedding: number[];
  /** base64-encoded JPEG */
  thumbnail: string;
  face_count: number;
}

export interface PeopleDetectJob {
  status: "running" | "succeeded" | "failed" | "cancelled";
  identities: PeopleIdentity[];
  error: string | null;
  progress: number;
}

export async function startPeopleDetect(fileManagerIds: number[]): Promise<{ job_id: string }> {
  return handle(
    await fetch(`${BASE}/people/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_manager_ids: fileManagerIds }),
    }),
  );
}

export async function getPeopleDetect(jobId: string): Promise<PeopleDetectJob> {
  return handle(await fetch(`${BASE}/people/detect/${jobId}`));
}

export async function cancelPeopleDetect(jobId: string): Promise<void> {
  await fetch(`${BASE}/people/detect/${jobId}/cancel`, { method: "POST" });
}

export interface PersonCropResult {
  file_manager_id: number;
  /** One box per matched instance of the trained person in this photo — a
   * collage with the person appearing more than once produces more than one
   * box here. */
  crops: number[][];
}

export interface PeopleSelectJob {
  status: "running" | "succeeded" | "failed" | "cancelled";
  results: PersonCropResult[];
  error: string | null;
  progress: number;
}

export async function startPeopleSelect(
  fileManagerIds: number[],
  embeddings: number[][],
): Promise<{ job_id: string }> {
  return handle(
    await fetch(`${BASE}/people/select`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_manager_ids: fileManagerIds, embeddings }),
    }),
  );
}

export async function getPeopleSelect(jobId: string): Promise<PeopleSelectJob> {
  return handle(await fetch(`${BASE}/people/select/${jobId}`));
}

export async function cancelPeopleSelect(jobId: string): Promise<void> {
  await fetch(`${BASE}/people/select/${jobId}/cancel`, { method: "POST" });
}

export function cropPreviewUrl(fileManagerId: number, crop: number[]): string {
  const [x1, y1, x2, y2] = crop;
  const params = new URLSearchParams({
    file_manager_id: String(fileManagerId),
    x1: String(x1),
    y1: String(y1),
    x2: String(x2),
    y2: String(y2),
  });
  return `${BASE}/crop-preview?${params}`;
}

export interface CaptionResult {
  file_manager_id: number;
  caption: string;
}

export interface CaptionBatchJob {
  status: "running" | "succeeded" | "failed" | "cancelled";
  captions: CaptionResult[];
  error: string | null;
  progress: number;
}

export async function startCaptionBatch(fileManagerIds: number[]): Promise<{ job_id: string }> {
  return handle(
    await fetch(`${BASE}/captions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_manager_ids: fileManagerIds }),
    }),
  );
}

export async function getCaptionBatch(jobId: string): Promise<CaptionBatchJob> {
  return handle(await fetch(`${BASE}/captions/${jobId}`));
}

export async function cancelCaptionBatch(jobId: string): Promise<void> {
  await fetch(`${BASE}/captions/${jobId}/cancel`, { method: "POST" });
}
