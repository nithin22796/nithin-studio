export type Scale = 2 | 4;

export interface UpscaleJob {
  status: "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  total: number;
  file_id: number | null;
  name: string | null;
  content_type: string | null;
  size_bytes: number | null;
  error: string | null;
  diagnostics: string | null;
}
