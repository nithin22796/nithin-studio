export interface MediaItem {
  id: string;
  src: string;
  alt?: string;
  downloadName?: string;
  /** Defaults to "image" if omitted — existing callers (frame-extractor)
   * only ever deal in images, so this stays optional rather than forcing
   * every call site to specify it. */
  kind?: "image" | "video";
}
