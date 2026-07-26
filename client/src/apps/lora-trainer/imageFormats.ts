// Browsers only recognize a handful of MIME types as "image/*" — HEIC (iPhone)
// usually gets one, but DSLR RAW formats typically don't, so `accept="image/*"`
// alone hides them in the file picker and `content_type.startsWith("image/")`
// alone would exclude them from anywhere already-uploaded files are filtered.
const EXTRA_PHOTO_EXTENSIONS = [
  "heic",
  "heif",
  "cr2",
  "cr3",
  "nef",
  "arw",
  "dng",
  "raf",
  "orf",
  "rw2",
  "pef",
  "srw",
];

export const FILE_INPUT_ACCEPT = [
  "image/*",
  ...EXTRA_PHOTO_EXTENSIONS.map((ext) => `.${ext}`),
].join(",");

export function isPhotoFile(file: { name: string; content_type: string }): boolean {
  if (file.content_type.startsWith("image/")) return true;
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return EXTRA_PHOTO_EXTENSIONS.includes(ext);
}
