from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

# A generous cap, not a real limit for any normal page — exists only to stop
# a pathological page (an infinite-scroll gallery, a page that's actually a
# sitemap) from turning "paste a URL" into a multi-thousand-download job.
MAX_IMAGES = 300

# Tracking pixels and tiny icons aren't what "pull the images from this page"
# means in practice — filtering by byte size (rather than trying to parse
# dimensions) is a cheap way to skip most of them without a real image
# decode.
MIN_IMAGE_BYTES = 2048

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; nithin-studio image-importer/1.0)"}


class _WookmarkLinkParser(HTMLParser):
    """Collects hrefs from `<a>` tags nested inside
    `ul.wookmark-initialised > li.thumbwook` — the gallery grid markup this
    is scraping links from. Tracks ancestry with a simple tag/class stack
    rather than a real DOM, since we only need to answer "is the current
    <a> inside one of these li's, which is inside one of these ul's" —
    good enough for realistic (if not perfectly well-formed) HTML without a
    full parser dependency.
    """

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []
        self._stack: list[tuple[str, set[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        self._stack.append((tag, classes))
        if tag == "a" and self._inside_target_li():
            href = attr_map.get("href")
            if href:
                self._add(href)

    def handle_endtag(self, tag: str) -> None:
        # Truncate back to (and including) the most recent matching open
        # tag — tolerates the occasional unclosed tag in real-world HTML
        # instead of desyncing the whole stack.
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                return

    def _inside_target_li(self) -> bool:
        seen_target_ul = False
        for tag, classes in self._stack[:-1]:  # exclude the <a> just pushed
            if tag == "ul" and "wookmark-initialised" in classes:
                seen_target_ul = True
            elif tag == "li" and "thumbwook" in classes and seen_target_ul:
                return True
        return False

    def _add(self, url: str) -> None:
        if url.startswith("data:"):
            return
        absolute = urljoin(self.base_url, url)
        if absolute not in self.urls:
            self.urls.append(absolute)


def extract_image_urls(page_url: str, html: str) -> list[str]:
    parser = _WookmarkLinkParser(page_url)
    parser.feed(html)
    return parser.urls[:MAX_IMAGES]


def filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    return name or "image"


def fetch_page_image_urls(page_url: str) -> list[str]:
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=20.0) as client:
        response = client.get(page_url)
        response.raise_for_status()
        return extract_image_urls(str(response.url), response.text)


def download_image(client: httpx.Client, url: str) -> tuple[str, bytes, str] | None:
    """Returns (filename, content, content_type), or None if the URL isn't a
    real downloadable image (wrong content-type, too small, or a request
    failure — any of which just means "skip this one," not "abort the job")."""
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    content_type = response.headers.get("content-type", "").split(";")[0].strip()
    if not content_type.startswith("image/"):
        return None
    content = response.content
    if len(content) < MIN_IMAGE_BYTES:
        return None
    return filename_from_url(url), content, content_type
