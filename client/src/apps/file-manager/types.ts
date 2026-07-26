export interface FolderItem {
  id: number;
  name: string;
  parent_id: number | null;
}

export interface FileItem {
  id: number;
  name: string;
  folder_id: number | null;
  content_type: string;
  size_bytes: number;
}

export interface ContentsResponse {
  folders: FolderItem[];
  files: FileItem[];
}
