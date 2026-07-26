import { useState } from "react";
import type { ExtractResponse } from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type ExtractSource =
  | { kind: "upload"; file: File }
  | { kind: "file-manager"; fileId: number };

type ExtractorState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; result: ExtractResponse }
  | { status: "error"; message: string };

export function useExtractor() {
  const [state, setState] = useState<ExtractorState>({ status: "idle" });

  async function extract(source: ExtractSource, intervalSeconds: number) {
    setState({ status: "loading" });

    try {
      const response =
        source.kind === "upload"
          ? await postUpload(source.file, intervalSeconds)
          : await postFileManagerId(source.fileId, intervalSeconds);

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(
          payload?.detail ?? `request failed with status ${response.status}`,
        );
      }
      const result = (await response.json()) as ExtractResponse;
      setState({ status: "success", result });
    } catch (err) {
      setState({
        status: "error",
        message: err instanceof Error ? err.message : "unknown error",
      });
    }
  }

  return { state, extract };
}

function postUpload(file: File, intervalSeconds: number) {
  const body = new FormData();
  body.append("file", file);
  return fetch(`${API_URL}/frame-extractor/extract?interval_seconds=${intervalSeconds}`, {
    method: "POST",
    body,
  });
}

function postFileManagerId(fileId: number, intervalSeconds: number) {
  return fetch(
    `${API_URL}/frame-extractor/extract?interval_seconds=${intervalSeconds}&file_manager_file_id=${fileId}`,
    { method: "POST" },
  );
}
