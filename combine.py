import feedparser
import hashlib
import os
import datetime
import re
from html.parser import HTMLParser

OUTPUT_PATH = "output/merged.xml"


# ---------------------------------------------------------------------------
# HTML utilities
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts).strip()


def strip_html(html: str) -> str:
    """Return plain text from an HTML string."""
    if not html:
        return ""
    p = _TextExtractor()
    try:
        p.feed(html)
        return p.get_text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html).strip()


def truncate(text: str, max_chars: int = 400) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------------------
# Link / ID helpers
# ---------------------------------------------------------------------------

def normalize_link(link: str) -> str:
    if not link:
        return ""
    link = re.sub(r"https?://(www\.)?", "", link)
    link = re.sub(r"/+$", "", link)
    return link.strip().lower()


def unique_id(entry) -> str:
    link = normalize_link(entry.get("link", ""))
    title = entry.get("title", "").strip().lower()
    return hashlib.md5(f"{link}-{title}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Thumbnail extraction  (handles all 9 feed variants)
# ---------------------------------------------------------------------------

def extract_thumbnail(entry) -> str:
    """
    Try every known location for an image URL, in priority order:

    1. media:content (image medium)
    2. media:thumbnail
    3. enclosure (RSS 2.0 standard — used by NASA IOTD)
    4. links with type image/*
    5. First <img> src scraped from content:encoded / description
    """

    # 1. media:content — feedparser exposes as entry.media_content (list of dicts)
    for mc in entry.get("media_content", []):
        url = mc.get("url", "")
        medium = mc.get("medium", "")
        mime = mc.get("type", "")
        if url and (medium == "image" or mime.startswith("image/")):
            return url
    # Some feeds put a single media:content without medium attr — grab first URL
    for mc in entry.get("media_content", []):
        if mc.get("url"):
            return mc["url"]

    # 2. media:thumbnail — feedparser: entry.media_thumbnail (list of dicts)
    for mt in entry.get("media_thumbnail", []):
        if mt.get("url"):
            return mt["url"]

    # 3. enclosure — feedparser: entry.enclosures (list of dicts)  — NASA IOTD
    for enc in entry.get("enclosures", []):
        if enc.get("type", "").startswith("image/") and enc.get("href"):
            return enc["href"]

    # 4. links with image type
    for lnk in entry.get("links", []):
        if lnk.get("type", "").startswith("image/") and lnk.get("href"):
            return lnk["href"]

    # 5. Scrape first <img src="..."> from content:encoded or description
    for field in ("content", "summary"):
        raw = ""
        if field == "content":
            # feedparser wraps content:encoded as entry.content (list)
            for c in entry.get("content", []):
                raw = c.get("value", "")
                if raw:
                    break
        else:
            raw = entry.get("summary", "")
        if raw:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw, re.IGNORECASE)
            if m:
                return m.group(1)

    return ""


# ---------------------------------------------------------------------------
# Description extraction  (handles Aeon's body-in-description quirk)
# ---------------------------------------------------------------------------

def extract_description(entry) -> str:
    """
    Return a clean plain-text excerpt (~400 chars).

    Priority:
    1. content:encoded  → strip HTML → truncate
    2. summary (description) → strip HTML → truncate
    Both Aeon (full text in summary) and standard feeds are covered this way.
    """
    # content:encoded via feedparser entry.content list
    for c in entry.get("content", []):
        text = strip_html(c.get("value", ""))
        if text:
            return truncate(text)

    # summary / description
    text = strip_html(entry.get("summary", ""))
    if text:
        return truncate(text)

    return ""


# ---------------------------------------------------------------------------
# Real-URL extraction  (FeedBurner wraps links)
# ---------------------------------------------------------------------------

def extract_link(entry) -> str:
    """
    FeedBurner replaces <link> with a tracking redirect and puts the
    real URL in <feedburner:origLink>.  feedparser exposes this as
    entry.feedburner_origlink.
    """
    return (
        entry.get("feedburner_origlink")
        or entry.get("link", "")
    )


# ---------------------------------------------------------------------------
# Feed I/O
# ---------------------------------------------------------------------------

def load_feeds(file="feed_urls.txt"):
    with open(file, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def fetch_all_feeds(urls):
    all_entries = []
    for url in urls:
        print(f"Fetching {url}")
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            print(f"  ⚠ Parse error or empty feed: {feed.bozo_exception}")
            continue
        print(f"  → {len(feed.entries)} entries")
        all_entries.extend(feed.entries)
    return all_entries


def deduplicate(entries):
    seen = set()
    unique = []
    for e in entries:
        uid = unique_id(e)
        if uid not in seen:
            seen.add(uid)
            unique.append(e)
    return unique


# ---------------------------------------------------------------------------
# XML builder
# ---------------------------------------------------------------------------

def escape_xml(text: str) -> str:
    """Escape characters that break XML outside CDATA."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;")
    )


def make_rss(entries) -> str:
    now = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">',
        "<channel>",
        "<title>Combined Feed</title>",
        "<link>https://example.com/merged.xml</link>",
        "<description>Merged RSS feed</description>",
        f"<lastBuildDate>{now}</lastBuildDate>",
    ]

    for e in entries:
        title = e.get("title", "No title").strip()
        link = extract_link(e)
        desc = extract_description(e)
        thumb = extract_thumbnail(e)
        pub = e.get("published", now)

        lines.append("<item>")
        lines.append(f"  <title><![CDATA[{title}]]></title>")
        lines.append(f"  <link>{escape_xml(link)}</link>")
        lines.append(f"  <guid isPermaLink=\"false\">{escape_xml(link)}</guid>")
        lines.append(f"  <pubDate>{pub}</pubDate>")
        if desc:
            lines.append(f"  <description><![CDATA[{desc}]]></description>")
        if thumb:
            # media:thumbnail is universally supported by feed readers
            lines.append(f'  <media:thumbnail url="{escape_xml(thumb)}"/>')
            # also emit media:content for readers that prefer it
            lines.append(
                f'  <media:content url="{escape_xml(thumb)}" medium="image"/>'
            )
        lines.append("</item>")

    lines += ["</channel>", "</rss>"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    urls = load_feeds()
    entries = fetch_all_feeds(urls)
    print(f"\nFetched {len(entries)} total entries")

    entries = deduplicate(entries)
    print(f"{len(entries)} unique entries after deduplication")

    rss = make_rss(entries)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"\n✅ Merged RSS saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
