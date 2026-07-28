export interface PreviewImage {
  id: string;
  temp_url: string;
}

export interface PreviewJob {
  status: "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  total: number;
  images: PreviewImage[];
  error: string | null;
}

export interface ImportJob {
  status: "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  total: number;
  saved_file_ids: number[];
  skipped: number;
  error: string | null;
}
