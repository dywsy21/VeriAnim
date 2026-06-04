"""Public texture search and download helpers.

The harness treats FreeStockTextures as a small MCP-like provider: callers ask
for texture candidates, download a few previews, then let a vision model choose
whether any candidate is appropriate before code generation sees the asset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import mimetypes
import re
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests


FREE_STOCK_TEXTURES_LICENSE = "CC0"


@dataclass(slots=True)
class TextureCandidate:
    title: str
    page_url: str
    image_url: str | None = None
    download_url: str | None = None
    tags: list[str] = field(default_factory=list)
    local_path: Path | None = None
    score: float = 0.0

    def to_manifest(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "title": self.title,
            "page_url": self.page_url,
            "image_url": self.image_url,
            "download_url": self.download_url,
            "tags": self.tags,
            "local_path": str(self.local_path) if self.local_path else None,
            "license": FREE_STOCK_TEXTURES_LICENSE,
            "score": self.score,
        }


class FreeStockTexturesClient:
    """Scrape public FreeStockTextures pages without relying on private APIs."""

    BASE_URL = "https://freestocktextures.com/"
    CATEGORY_ALIASES = {
        "brick": "wall",
        "bricks": "wall",
        "brushed": "metal",
        "ceramic": "stone",
        "cement": "concrete",
        "fabric": "abstract",
        "grass": "ground",
        "leather": "abstract",
        "marble": "stone",
        "paper": "grunge",
        "plank": "wood",
        "planks": "wood",
        "rust": "metal",
        "soil": "ground",
        "tabletop": "wood",
        "timber": "wood",
        "water": "liquid",
    }
    TOP_CATEGORIES = {
        "abstract",
        "concrete",
        "graffiti",
        "ground",
        "grunge",
        "liquid",
        "metal",
        "nature",
        "stone",
        "wall",
        "wood",
    }

    def __init__(self, *, timeout_seconds: int = 20, user_agent: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent
                or "VeriAnim texture search (+https://freestocktextures.com/license/; public CC0 textures)",
                "Accept": "text/html,image/*;q=0.9,*/*;q=0.8",
            }
        )

    def search(self, query: str, *, limit: int = 6) -> list[TextureCandidate]:
        query = query.strip()
        if not query:
            return []
        candidates: dict[str, TextureCandidate] = {}
        for url in self._candidate_list_urls(query):
            try:
                html = self._get_text(url)
            except requests.RequestException:
                continue
            for item in _parse_listing_candidates(html, url):
                existing = candidates.get(item.page_url)
                if existing is None:
                    candidates[item.page_url] = item
                if len(candidates) >= max(limit * 4, limit):
                    break
            if len(candidates) >= max(limit * 4, limit):
                break
        ranked = sorted(candidates.values(), key=lambda item: _text_score(query, item.title, item.tags), reverse=True)
        enriched: list[TextureCandidate] = []
        for candidate in ranked[: max(limit * 4, limit)]:
            try:
                detail_html = self._get_text(candidate.page_url)
                detail = _parse_detail_page(detail_html, candidate.page_url)
            except requests.RequestException:
                detail = {}
            candidate.image_url = detail.get("image_url") or candidate.image_url
            candidate.download_url = detail.get("download_url") or candidate.download_url
            candidate.tags = detail.get("tags") or candidate.tags
            candidate.score = _text_score(query, candidate.title, candidate.tags)
            if candidate.image_url or candidate.download_url:
                enriched.append(candidate)
        filtered = [candidate for candidate in enriched if _passes_intent_gate(query, candidate)]
        return sorted(filtered, key=lambda item: item.score, reverse=True)[:limit]

    def download_candidate(self, candidate: TextureCandidate, output_dir: Path) -> TextureCandidate:
        output_dir.mkdir(parents=True, exist_ok=True)
        urls = [candidate.download_url, candidate.image_url]
        last_error: Exception | None = None
        for url in [item for item in urls if item]:
            try:
                response = self.session.get(str(url), timeout=self.timeout_seconds, allow_redirects=True)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type and not content_type.startswith("image/"):
                    image_url = _first_image_url(response.text, response.url)
                    if image_url and image_url != url:
                        response = self.session.get(image_url, timeout=self.timeout_seconds, allow_redirects=True)
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type and not content_type.startswith("image/"):
                    continue
                suffix = _suffix_from_response(response.url, content_type)
                path = output_dir / f"{_slugify(candidate.title)}{suffix}"
                path.write_bytes(response.content)
                candidate.local_path = path
                return candidate
            except Exception as exc:  # Network and filesystem failures both fall through to fallback URL.
                last_error = exc
                continue
        if last_error:
            raise RuntimeError(f"Could not download texture candidate '{candidate.title}': {last_error}") from last_error
        raise RuntimeError(f"Could not download texture candidate '{candidate.title}': no downloadable image URL")

    def _candidate_list_urls(self, query: str) -> list[str]:
        tokens = _query_tokens(query)
        slugs: list[str] = []
        for token in tokens:
            slug = self.CATEGORY_ALIASES.get(token, token)
            if slug not in slugs:
                slugs.append(slug)
        joined = "-".join(tokens[:3])
        if joined and joined not in slugs:
            slugs.insert(0, joined)
        categories = [
            self.CATEGORY_ALIASES.get(token, token)
            for token in tokens
            if self.CATEGORY_ALIASES.get(token, token) in self.TOP_CATEGORIES
        ]
        if not categories:
            categories = ["texture"]
        urls: list[str] = []
        for slug in [*slugs, *categories]:
            if slug == "texture":
                urls.append(urljoin(self.BASE_URL, "texture/"))
            else:
                urls.append(urljoin(self.BASE_URL, f"photos-{quote(slug)}/"))
        return list(dict.fromkeys(urls))

    def _get_text(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.text


class _ListingParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.items: list[TextureCandidate] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href and "/texture/" in href:
            self._href = urljoin(self.base_url, href)
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        title = _clean_text(" ".join(self._text))
        if title:
            self.items.append(TextureCandidate(title=title, page_url=self._href))
        self._href = None
        self._text = []


class _DetailParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.title: str | None = None
        self.image_url: str | None = None
        self.download_url: str | None = None
        self.tags: list[str] = []
        self._capture_heading = False
        self._heading_text: list[str] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag.lower() == "h1":
            self._capture_heading = True
            self._heading_text = []
        if tag.lower() == "img":
            alt = (attributes.get("alt") or "").lower()
            src = attributes.get("src") or attributes.get("data-src")
            if src and "free texture" in alt and "search" not in alt:
                self.image_url = urljoin(self.base_url, src)
        if tag.lower() == "a":
            href = attributes.get("href")
            if href:
                self._href = urljoin(self.base_url, href)
                self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._capture_heading:
            self._heading_text.append(data)
        if self._href:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "h1" and self._capture_heading:
            self.title = _clean_text(" ".join(self._heading_text))
            self._capture_heading = False
            self._heading_text = []
        if tag.lower() == "a" and self._href:
            text = _clean_text(" ".join(self._link_text))
            path = urlparse(self._href).path
            if text.lower() == "download" or "download" in path:
                self.download_url = self._href
            elif re.fullmatch(r"[a-z0-9][a-z0-9 \-]{1,40}", text.lower() or ""):
                if not any(skip in path for skip in ("/about", "/contact", "/license", "/terms", "/privacy")):
                    self.tags.append(text.lower())
            self._href = None
            self._link_text = []


def _parse_listing_candidates(html: str, base_url: str) -> list[TextureCandidate]:
    parser = _ListingParser(base_url)
    parser.feed(html)
    seen: set[str] = set()
    result: list[TextureCandidate] = []
    for item in parser.items:
        if item.page_url in seen:
            continue
        if _is_non_texture_listing_item(item):
            continue
        seen.add(item.page_url)
        result.append(item)
    return result


def _parse_detail_page(html: str, base_url: str) -> dict[str, Any]:
    parser = _DetailParser(base_url)
    parser.feed(html)
    image_url = parser.image_url or _first_image_url(html, base_url)
    return {
        "title": parser.title,
        "image_url": image_url,
        "download_url": parser.download_url,
        "tags": list(dict.fromkeys(parser.tags)),
    }


def _first_image_url(html: str, base_url: str) -> str | None:
    for match in re.finditer(r"<img\b[^>]*(?:src|data-src)=['\"]([^'\"]+)['\"][^>]*>", html, re.IGNORECASE):
        tag = match.group(0).lower()
        if "search" in tag or "logo" in tag:
            continue
        if "free texture" in tag or "texture" in tag:
            return urljoin(base_url, unescape(match.group(1)))
    return None


def _query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", query.lower())
    stop = {"and", "for", "of", "the", "with", "material", "texture", "surface", "seamless"}
    return [token for token in tokens if token not in stop][:6]


def _text_score(query: str, title: str, tags: list[str]) -> float:
    intent = _query_intent(query)
    tokens = set(intent["tokens"])
    haystack = " ".join([title, *tags]).lower()
    haystack_tokens = set(_query_tokens(haystack))
    score = sum(2.0 for token in tokens if token in haystack_tokens)
    score += sum(0.5 for token in tokens if token in haystack and token not in haystack_tokens)
    if title.lower() in query.lower() or query.lower() in title.lower():
        score += 3.0
    for token in intent["required_any"]:
        if token in haystack_tokens or token in haystack:
            score += 8.0
    for token in intent["positive"]:
        if token in haystack_tokens or token in haystack:
            score += 3.0
    for token in intent["negative"]:
        if token in haystack_tokens or token in haystack:
            score -= 8.0
    if intent["required_any"] and not any(token in haystack_tokens or token in haystack for token in intent["required_any"]):
        score -= 20.0
    return score


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _is_non_texture_listing_item(item: TextureCandidate) -> bool:
    title = item.title.strip().lower()
    if title in {"browse", "download", "next", "previous"}:
        return True
    return not urlparse(item.page_url).path.startswith("/texture/")


def _passes_intent_gate(query: str, candidate: TextureCandidate) -> bool:
    intent = _query_intent(query)
    haystack = " ".join([candidate.title, *candidate.tags]).lower()
    haystack_tokens = set(_query_tokens(haystack))
    title = candidate.title.lower()
    title_tokens = set(_query_tokens(title))
    if any(token in title_tokens or token in title for token in intent["negative"]):
        return False
    if intent["required_any"] and not any(token in haystack_tokens or token in haystack for token in intent["required_any"]):
        return False
    if candidate.score < intent["min_score"]:
        return False
    return True


def _query_intent(query: str) -> dict[str, Any]:
    tokens = _query_tokens(query)
    required_any: list[str] = []
    positive: list[str] = []
    negative: list[str] = []
    min_score = -100.0

    if "ceramic" in tokens and any(token in tokens for token in ("plain", "white", "clean", "solid")):
        required_any.extend(["ceramic", "porcelain"])
        positive.extend(["smooth", "glossy", "white"])
        negative.extend(["wall", "paper", "grunge", "dirty", "wrinkled", "rust", "wood"])
        min_score = 4.0
    if "leather" in tokens:
        required_any.append("leather")
        positive.extend(["brown", "hide", "grain", "quilted"])
        negative.extend(["paper", "wood", "wooden", "floor", "wall"])
        min_score = 4.0
    if "brushed" in tokens and "metal" in tokens:
        required_any.append("metal")
        positive.extend(["brushed", "scratched", "galvanized", "silver", "steel", "metallic"])
        negative.extend(["rust", "rusty", "russet", "paper", "grunge", "painted"])
        min_score = 2.0
    return {
        "tokens": tokens,
        "required_any": list(dict.fromkeys(required_any)),
        "positive": list(dict.fromkeys(positive)),
        "negative": list(dict.fromkeys(negative)),
        "min_score": min_score,
    }


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.lower()).strip("_")
    return slug or "texture"


def _suffix_from_response(url: str, content_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    if guessed in {".jpe"}:
        return ".jpg"
    if guessed in {".jpg", ".jpeg", ".png", ".webp"}:
        return guessed
    return ".jpg"
