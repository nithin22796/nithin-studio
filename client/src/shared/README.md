Generic, app-agnostic UI helpers with no single sub-app's data model baked in.

All sub-apps are routes inside this one client (see `../apps/`), so this is
real shared code — import it directly, don't copy it.

- `media-preview/` — full-screen preview modal for a list of images
  (`MediaItem[]`: `{ id, src, alt?, downloadName? }`). Supports prev/next,
  zoom in/out, keyboard nav (arrows, +/-, Escape), and an optional download
  action. Map your app's data into `MediaItem[]` before rendering it.
- `download.ts` — `saveBlob(blob, filename)` and `downloadUrl(url, filename)`
  for triggering browser downloads from fetched responses.
