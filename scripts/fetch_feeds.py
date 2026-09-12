#!/usr/bin/env python3
"""Récupère les items récents de tous les flux et les écrit en JSON.

Usage: python scripts/fetch_feeds.py <heures> <sortie.json>
"""
import json
import re
import sys
import html
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
ROOT = Path(__file__).resolve().parent.parent
MAX_ITEMS_PER_FEED = 15


def clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def child(element, *names):
    """Premier enfant portant l'un de ces noms de balise, namespace ignoré.

    Ne jamais remplacer par `a or b` : un Element sans enfant est falsy, ce qui
    ferait silencieusement perdre toutes les dates.
    """
    for name in names:
        for node in element:
            if node.tag.split("}")[-1] == name:
                return node
    return None


def fetch_one(feed, cutoff):
    name, url = feed["name"], feed["url"]
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })
        raw = urllib.request.urlopen(request, timeout=20).read()
        root = ET.fromstring(raw)
    except Exception as exc:
        return {"name": name, "error": f"{type(exc).__name__}: {exc}"[:120], "items": []}

    items = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue

        title_node = child(node, "title")
        title = clean(title_node.text if title_node is not None else "")
        if not title:
            continue

        date_node = child(node, "pubDate", "published", "updated", "date")
        published = parse_date(date_node.text if date_node is not None else None)
        if published is None or published < cutoff:
            continue

        link_node = child(node, "link")
        link = ""
        if link_node is not None:
            link = (link_node.get("href") or link_node.text or "").strip()

        summary_node = child(node, "description", "summary", "content")
        summary = clean(summary_node.text if summary_node is not None else "")[:300]

        items.append({
            "title": title,
            "link": link,
            "published": published.astimezone(timezone.utc).isoformat(),
            "summary": summary,
        })
        if len(items) >= MAX_ITEMS_PER_FEED:
            break

    return {"name": name, "group": feed.get("group", ""), "items": items}


def main():
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "items.json"

    feeds = json.loads((ROOT / "feeds.json").read_text(encoding="utf-8"))["feeds"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda f: fetch_one(f, cutoff), feeds))

    errors = [r for r in results if r.get("error")]
    sources = [r for r in results if not r.get("error") and r["items"]]
    total = sum(len(r["items"]) for r in sources)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": hours,
        "stats": {"feeds": len(feeds), "items": total, "errors": len(errors)},
        "errors": [{"name": e["name"], "error": e["error"]} for e in errors],
        "sources": sources,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"feeds={len(feeds)} ok={len(sources)} errors={len(errors)} items={total}")
    for err in errors:
        print(f"  ! {err['name']}: {err['error']}")

    if total == 0:
        print("ERREUR : aucun item récupéré, le réseau est probablement bloqué.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
