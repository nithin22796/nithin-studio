export type SessionStatus =
  "launching" | "running" | "stopping" | "terminated" | "failed";

export interface Session {
  id: number;
  status: SessionStatus;
  loaded_model: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface GeneratedImage {
  file_id: number;
  name: string;
}
