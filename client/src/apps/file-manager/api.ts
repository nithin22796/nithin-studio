import type { ContentsResponse, FileItem, FolderItem } from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const BASE = `${API_URL}/file-manager`;

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `request failed with status ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function handleEmpty(response: Response): Promise<void> {
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `request failed with status ${response.status}`);
  }
}

export async function fetchContents(folderId: number | null): Promise<ContentsResponse> {
  const url = folderId === null ? `${BASE}/items` : `${BASE}/items?folder_id=${folderId}`;
  return handle(await fetch(url));
}

export async function searchItems(query: string): Promise<ContentsResponse> {
  return handle(await fetch(`${BASE}/search?q=${encodeURIComponent(query)}`));
}

export async function listAllFolders(): Promise<FolderItem[]> {
  return handle(await fetch(`${BASE}/folders`));
}

export async function createFolder(
  name: string,
  parentId: number | null,
): Promise<FolderItem> {
  return handle(
    await fetch(`${BASE}/folders`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parent_id: parentId }),
    }),
  );
}

export async function getOrCreateFolder(
  name: string,
  parentId: number | null,
): Promise<FolderItem> {
  return handle(
    await fetch(`${BASE}/folders/get-or-create`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, parent_id: parentId }),
    }),
  );
}

export async function renameFolder(id: number, name: string): Promise<FolderItem> {
  return handle(
    await fetch(`${BASE}/folders/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export async function deleteFolder(id: number): Promise<void> {
  await handleEmpty(await fetch(`${BASE}/folders/${id}`, { method: "DELETE" }));
}

export function uploadFile(
  file: File,
  folderId: number | null,
  onProgress?: (fraction: number) => void,
): Promise<FileItem> {
  const url = folderId === null ? `${BASE}/files` : `${BASE}/files?folder_id=${folderId}`;
  const body = new FormData();
  body.append("file", file);

  // XMLHttpRequest, not fetch — fetch has no way to observe upload progress.
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.onprogress = (event) => {
      if (onProgress && event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as FileItem);
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

export function fileContentUrl(
  id: number,
  disposition: "inline" | "attachment" = "attachment",
): string {
  return `${BASE}/files/${id}/content?disposition=${disposition}`;
}

export async function renameFile(id: number, name: string): Promise<FileItem> {
  return handle(
    await fetch(`${BASE}/files/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  );
}

export async function moveFile(id: number, folderId: number | null): Promise<FileItem> {
  return handle(
    await fetch(`${BASE}/files/${id}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_id: folderId }),
    }),
  );
}

export async function deleteFile(id: number): Promise<void> {
  await handleEmpty(await fetch(`${BASE}/files/${id}`, { method: "DELETE" }));
}
