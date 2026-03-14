import feedparser  
import hashlib  
import os  
import json  
import datetime  
import re  
from html.parser import HTMLParser  
  
OUTPUT_PATH = "output/merged.xml"  
SEEN_PATH = "output/seen.json"  
  
  
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
    for mc in entry.get("media_content", []):  
        url = mc.get("url", "")  
        medium = mc.get("medium", "")  
        mime = mc.get("type", "")  
        if url and (medium == "image" or mime.startswith("image/")):  
            return url  
    for mc in entry.get("media_content", []):  
        if mc.get("url"):  
            return mc["url"]  
  
    for mt in entry.get("media_thumbnail", []):  
        if mt.get("url"):  
            return mt["url"]  
  
    for enc in entry.get("enclosures", []):  
        if enc.get("type", "").startswith("image/") and enc.get("href"):  
            return enc["href"]  
  
    for lnk in entry.get("links", []):  
        if lnk.get("type", "").startswith("image/") and lnk.get("href"):  
            return lnk["href"]  
  
    for field in ("content", "summary"):  
        raw = ""  
        if field == "content":  
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
    for c in entry.get("content", []):  
        text = strip_html(c.get("value", ""))  
        if text:  
            return truncate(text)  
  
    text = strip_html(entry.get("summary", ""))  
    if text:  
        return truncate(text)  
  
    return ""  
  
  
# ---------------------------------------------------------------------------  
# Real-URL extraction  (FeedBurner wraps links)  
# ---------------------------------------------------------------------------  
  
def extract_link(entry) -> str:  
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
# Seen-ID persistence  
# ---------------------------------------------------------------------------  
  
def load_seen() -> set:  
    if os.path.exists(SEEN_PATH):  
        with open(SEEN_PATH, "r", encoding="utf-8") as f:  
            return set(json.load(f))  
    return set()  
  
  
def save_seen(seen: set):  
    os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)  
    with open(SEEN_PATH, "w", encoding="utf-8") as f:  
        json.dump(list(seen), f)  
  
  
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
  
  
def _make_items(entries, fallback_date: str) -> str:  
    """Return the XML lines for a list of entries (no channel wrapper)."""  
    lines = []  
    for e in entries:  
        title = e.get("title", "No title").strip()  
        link = extract_link(e)  
        desc = extract_description(e)  
        thumb = extract_thumbnail(e)  
        pub = e.get("published", fallback_date)  
  
        lines.append("<item>")  
        lines.append(f"  <title><![CDATA[{title}]]></title>")  
        lines.append(f"  <link>{escape_xml(link)}</link>")  
        lines.append(f"  <guid isPermaLink=\"false\">{escape_xml(link)}</guid>")  
        lines.append(f"  <pubDate>{pub}</pubDate>")  
        if desc:  
            lines.append(f"  <description><![CDATA[{desc}]]></description>")  
        if thumb:  
            lines.append(f'  <media:thumbnail url="{escape_xml(thumb)}"/>')  
            lines.append(  
                f'  <media:content url="{escape_xml(thumb)}" medium="image"/>'  
            )  
        lines.append("</item>")  
    return "\n".join(lines)  
  
  
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
        _make_items(entries, now),  
        "</channel>",  
        "</rss>",  
    ]  
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
  
    seen = load_seen()  
    new_entries = [e for e in entries if unique_id(e) not in seen]  
    print(f"{len(new_entries)} new entries to append")  
  
    if not new_entries:  
        print("Nothing new to write.")  
        return  
  
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)  
  
    if os.path.exists(OUTPUT_PATH):  
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:  
            existing = f.read()  
        # Strip closing tags, inject new items, re-close  
        existing = re.sub(r'\s*</channel>\s*</rss>\s*$', '', existing.rstrip())  
        now = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")  
        updated = existing + "\n" + _make_items(new_entries, now) + "\n</channel>\n</rss>"  
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:  
            f.write(updated)  
    else:  
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:  
            f.write(make_rss(new_entries))  
  
    seen.update(unique_id(e) for e in new_entries)  
    save_seen(seen)  
    print(f"✅ Appended {len(new_entries)} new items to {OUTPUT_PATH}")  
  
  
if __name__ == "__main__":  
    main()