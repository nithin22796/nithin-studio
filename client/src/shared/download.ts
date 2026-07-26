export function saveBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function downloadUrl(url: string, filename: string) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`download failed with status ${response.status}`);
  saveBlob(await response.blob(), filename);
}
