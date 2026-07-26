import { saveBlob } from "../../shared/download";

export async function downloadSelectedFrames(
  apiUrl: string,
  jobId: string,
  filenames: string[],
) {
  const response = await fetch(`${apiUrl}/frame-extractor/jobs/${jobId}/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filenames }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      payload?.detail ?? `download failed with status ${response.status}`,
    );
  }
  saveBlob(await response.blob(), `${jobId}-selected-frames.zip`);
}
