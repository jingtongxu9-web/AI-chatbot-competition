"""Crawl the two official InnoWing websites and build a local corpus.

The crawler is deliberately an offline-build step. It is not imported by
the answer path, and the answer path must never make network requests.

Run from ``submission_repo``::

    .venv\\Scripts\\python.exe -m build.scrape

The output is ``data/pages.json``, ``data/images.json`` and
``data/crawl_report.json``. The report keeps enough information to tell a
partial crawl from a complete one instead of silently treating failures as
missing facts.
"""

from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup


SITES = [
    "https://innowings.engg.hku.hk/",
    "https://innoacademy.engg.hku.hk/",
]

USER_AGENT = "InnoWingChatbotResearch/1.0 (educational crawl)"
REQUEST_TIMEOUT = float(os.getenv("CRAWL_TIMEOUT_SECONDS", "12"))
REQUEST_DELAY_SECONDS = float(os.getenv("CRAWL_DELAY_SECONDS", "0.20"))
MAX_RETRIES = int(os.getenv("CRAWL_MAX_RETRIES", "2"))
# The filtered WordPress sitemaps currently contain 583 and 666 page URLs.
# Keep room for pages found only through links, while making an accidental
# crawl expansion visible in crawl_report.json instead of running forever.
DEFAULT_MAX_PAGES = int(os.getenv("CRAWL_MAX_PAGES", "800"))

SKIP_EXTENSIONS = {
    ".7z", ".avi", ".css", ".csv", ".doc", ".docx", ".gif", ".gz",
    ".ico", ".jpeg", ".jpg", ".js", ".json", ".m4a", ".mov", ".mp3",
    ".mp4", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".svg", ".tar",
    ".tgz", ".tif", ".tiff", ".txt", ".webm", ".webp", ".xls", ".xlsx",
    ".xml", ".zip",
}

SKIP_PATH_PARTS = (
    "/wp-admin",
    "/wp-admin/",
    "/wp-login.php",
    "/wp-json/",
    "/feed/",
    "/comments/",
    "/author/",
    "/category/",
    "/tag/",
    "/search/",
)


def _normalise_url(url: str) -> str:
    """Canonicalise a URL enough to deduplicate crawl aliases."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    # Tracking/query URLs are not separate content pages for this corpus.
    return urlunparse((scheme, netloc, path, "", "", ""))


def _same_site(url: str, domain: str) -> bool:
    return urlparse(url).netloc.lower() == domain.lower()


def _is_crawlable_page(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.query:
        return False
    lower_path = parsed.path.lower()
    if any(part in lower_path for part in SKIP_PATH_PARTS):
        return False
    return not any(lower_path.endswith(ext) for ext in SKIP_EXTENSIONS)


def _make_robot_parser(base_url: str, session: requests.Session) -> RobotFileParser:
    """Read robots.txt through our session so the request is identifiable."""
    robots_url = urljoin(base_url, "/robots.txt")
    parser = RobotFileParser(robots_url)
    try:
        response = session.get(robots_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        parser.parse(response.text.splitlines())
    except requests.RequestException:
        # A missing/unreachable robots file is not permission to crawl
        # everything; the conservative fallback allows only normal pages and
        # our explicit path filters.
        parser.parse([])
    return parser


def _request_with_retries(
    session: requests.Session,
    url: str,
    stats: dict,
) -> requests.Response:
    """GET one URL with bounded retries for transient HTTP failures."""
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        if REQUEST_DELAY_SECONDS > 0:
            time.sleep(REQUEST_DELAY_SECONDS)
        try:
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code >= 400:
                response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            status = getattr(exc.response, "status_code", None)
            # Permanent 4xx pages do not become valid after a retry.  Keep
            # retrying rate limits, timeouts and server-side failures only.
            if status is not None and 400 <= status < 500 and status not in {408, 429}:
                raise
            if attempt + 1 < MAX_RETRIES:
                time.sleep(min(2 ** attempt, 4))
    assert last_error is not None
    raise last_error


def _sitemap_page_urls(
    base_url: str,
    session: requests.Session,
    robot: RobotFileParser,
    stats: dict,
) -> list[str]:
    """Return same-site page URLs from a WordPress sitemap hierarchy."""
    root_url = urljoin(base_url, "/wp-sitemap.xml")
    pending = deque([root_url])
    seen_sitemaps: set[str] = set()
    page_urls: list[str] = []
    seen_pages: set[str] = set()

    while pending:
        sitemap_url = _normalise_url(pending.popleft())
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        if not _same_site(sitemap_url, urlparse(base_url).netloc):
            continue
        if not robot.can_fetch(USER_AGENT, sitemap_url):
            stats["robots_blocked"] += 1
            continue
        try:
            response = _request_with_retries(session, sitemap_url, stats)
            if "xml" not in response.headers.get("Content-Type", "").lower():
                continue
            root = ET.fromstring(response.content)
        except (requests.RequestException, ET.ParseError) as exc:
            stats["sitemap_errors"].append({"url": sitemap_url, "error": str(exc)[:200]})
            continue

        tag = root.tag.rsplit("}", 1)[-1].lower()
        locs = [
            element.text.strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].lower() == "loc" and element.text
        ]
        if tag == "sitemapindex":
            pending.extend(locs)
            continue

        for raw_url in locs:
            url = _normalise_url(raw_url)
            if (
                _same_site(url, urlparse(base_url).netloc)
                and _is_crawlable_page(url)
                and robot.can_fetch(USER_AGENT, url)
                and url not in seen_pages
            ):
                seen_pages.add(url)
                page_urls.append(url)

    stats["sitemap_urls"] = len(page_urls)
    return page_urls


def _best_srcset_url(value: str, base_url: str) -> str | None:
    """Choose the largest URL in a srcset, when one is available."""
    candidates: list[tuple[float, str]] = []
    for item in value.split(","):
        parts = item.strip().split()
        if not parts:
            continue
        score = 0.0
        if len(parts) > 1:
            descriptor = parts[1].lower()
            try:
                score = float(descriptor[:-1]) if descriptor.endswith(("w", "x")) else 0.0
            except ValueError:
                score = 0.0
        candidates.append((score, urljoin(base_url, parts[0])))
    return max(candidates, default=(0.0, None))[1]


def _content_container(soup: BeautifulSoup):
    """Select the most likely readable-content container for these sites."""
    for selector in (
        ".entry-content",
        ".post-content",
        ".page-content",
        "article",
        "main",
        "#content",
        ".site-main",
    ):
        body = soup.select_one(selector)
        if body is not None:
            return body
    return None


def _clean_text(body) -> str:
    for node in body.select(
        "script, style, nav, footer, header, noscript, form, aside, .sharedaddy, "
        ".jp-relatedposts, .comments-area, .post-navigation"
    ):
        node.decompose()
    lines = []
    for line in body.get_text("\n", strip=True).splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def _page_type(url: str) -> str:
    path = urlparse(url).path.lower()
    if path in {"", "/"}:
        return "home"
    if "/category/" in path:
        return "category"
    if "/author/" in path:
        return "author"
    return "page"


def extract(html: str, url: str) -> dict:
    """Extract readable text, provenance and image records from one page."""
    soup = BeautifulSoup(html, "html.parser")
    body = _content_container(soup)
    if body is None:
        raise ValueError("no readable content container")

    title_node = soup.select_one("h1") or soup.title
    title = title_node.get_text(" ", strip=True) if title_node else ""

    images: list[dict] = []
    seen_images: set[str] = set()
    for img in soup.select("img"):
        candidates = [
            img.get("data-src"),
            img.get("data-lazy-src"),
            img.get("data-original"),
            img.get("src"),
        ]
        if img.get("srcset"):
            candidates.insert(0, _best_srcset_url(img["srcset"], url))
        src = next((candidate for candidate in candidates if candidate), None)
        if not src or src.startswith("data:"):
            continue
        image_url = urljoin(url, src)
        if image_url in seen_images:
            continue
        seen_images.add(image_url)
        figure = img.find_parent("figure")
        caption_node = figure.find("figcaption") if figure else None
        images.append({
            "src": image_url,
            "alt": img.get("alt", "").strip(),
            "caption": caption_node.get_text(" ", strip=True) if caption_node else "",
            "page": url,
        })

    # A few galleries put the original image URL on a link around a thumbnail.
    for link in soup.select("a[href]"):
        href = urljoin(url, link.get("href", ""))
        if (
            not href
            or not urlparse(href).path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))
            or href in seen_images
        ):
            continue
        image = link.find("img")
        seen_images.add(href)
        images.append({
            "src": href,
            "alt": image.get("alt", "").strip() if image else "",
            "caption": "",
            "page": url,
        })

    return {
        "url": url,
        "title": title,
        "text": _clean_text(body),
        "images": images,
        "source_type": "website",
        "page_type": _page_type(url),
    }


def crawl(
    start_url: str,
    max_pages: int = DEFAULT_MAX_PAGES,
    *,
    pages: list[dict] | None = None,
    stats: dict | None = None,
    session: requests.Session | None = None,
) -> list[str]:
    """Crawl one site and return successful page URLs.

    If ``pages`` is supplied, extracted records are appended during the same
    request that discovers the URL. This avoids downloading every page a
    second time just to parse it.
    """
    own_session = session is None
    session = session or requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    stats = stats if stats is not None else {}
    for key, default in {
        "requested": 0,
        "succeeded": 0,
        "failed": [],
        "robots_blocked": 0,
        "sitemap_errors": [],
        "discovered": 0,
        "duplicate_urls": 0,
        "non_html": 0,
        "skipped_empty": 0,
        "hit_page_limit": False,
    }.items():
        stats.setdefault(key, default)

    base_url = _normalise_url(start_url)
    domain = urlparse(base_url).netloc
    robot = _make_robot_parser(base_url, session)
    sitemap_urls = _sitemap_page_urls(base_url, session, robot, stats)
    queue = deque([base_url, *sitemap_urls])
    queued: set[str] = set()
    seen: set[str] = set()
    successful: list[str] = []

    try:
        while queue and len(successful) < max_pages:
            raw_url = queue.popleft()
            url = _normalise_url(raw_url)
            if url in queued or url in seen:
                stats["duplicate_urls"] += 1
                continue
            queued.add(url)
            if not _same_site(url, domain) or not _is_crawlable_page(url):
                continue
            if not robot.can_fetch(USER_AGENT, url):
                stats["robots_blocked"] += 1
                continue
            seen.add(url)
            stats["requested"] += 1
            try:
                response = _request_with_retries(session, url, stats)
                final_url = _normalise_url(response.url)
                if not _same_site(final_url, domain) or not _is_crawlable_page(final_url):
                    stats["failed"].append({"url": url, "error": "redirected off-site or to a non-page"})
                    continue
                content_type = response.headers.get("Content-Type", "").lower()
                if "html" not in content_type and "xhtml" not in content_type:
                    stats["non_html"] += 1
                    continue
                record = extract(response.text, final_url)
                if not record["text"] and not record["images"]:
                    stats["skipped_empty"] += 1
                else:
                    successful.append(final_url)
                    if pages is not None:
                        pages.append(record)
                stats["succeeded"] += 1

                # Link traversal supplements the sitemap and detects pages
                # that the CMS forgot to publish in a sitemap.
                page_soup = BeautifulSoup(response.text, "html.parser")
                for anchor in page_soup.select("a[href]"):
                    link = _normalise_url(urljoin(final_url, anchor.get("href", "")))
                    if (
                        _same_site(link, domain)
                        and _is_crawlable_page(link)
                        and robot.can_fetch(USER_AGENT, link)
                        and link not in queued
                        and link not in seen
                    ):
                        queue.append(link)
                        stats["discovered"] += 1
                if len(successful) % 100 == 0:
                    print(
                        f"{start_url}: {len(successful)} pages fetched, "
                        f"{len(stats['failed'])} failures",
                        flush=True,
                    )
            except (requests.RequestException, ValueError) as exc:
                stats["failed"].append({"url": url, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})

        stats["hit_page_limit"] = bool(queue and len(successful) >= max_pages)
        stats["remaining_queue"] = len(queue)
        return successful
    finally:
        if own_session:
            session.close()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    output_dir = Path("data")
    output_dir.mkdir(exist_ok=True)
    all_pages: list[dict] = []
    reports: list[dict] = []
    session = requests.Session()

    for site in SITES:
        site_pages: list[dict] = []
        site_stats: dict = {"site": site}
        urls = crawl(site, pages=site_pages, stats=site_stats, session=session)
        all_pages.extend(site_pages)
        reports.append(site_stats)
        print(
            f"{site}: {len(urls)} pages, {sum(len(p['images']) for p in site_pages)} images, "
            f"{len(site_stats['failed'])} failures"
        )

    # A redirect or duplicate sitemap entry can surface the same canonical URL twice.
    unique_pages: list[dict] = []
    seen_page_urls: set[str] = set()
    for page in all_pages:
        page["url"] = _normalise_url(page["url"])
        if page["url"] not in seen_page_urls:
            seen_page_urls.add(page["url"])
            unique_pages.append(page)

    images = [image for page in unique_pages for image in page["images"]]
    _write_json(output_dir / "pages.json", unique_pages)
    _write_json(output_dir / "images.json", images)
    _write_json(
        output_dir / "crawl_report.json",
        {
            "sites": reports,
            "pages_written": len(unique_pages),
            "images_written": len(images),
            "max_pages_per_site": DEFAULT_MAX_PAGES,
            "request_delay_seconds": REQUEST_DELAY_SECONDS,
            "user_agent": USER_AGENT,
        },
    )
    session.close()
    print(f"total: {len(unique_pages)} pages, {len(images)} images")
