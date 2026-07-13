from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import html
import json
import re
import time
import urllib.request

from ._ssl import build_ssl_context

_ssl_ctx = build_ssl_context()

# ── in-memory cache ───────────────────────────────────────────────────────────
_cache: dict | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 300  # 5 minutes


@dataclass
class HotTopic:
    title: str
    source: str
    rank: int
    heat: int = 0
    url: str = ""
    summary: str = ""


def fetch_hot_topics(limit: int = 50) -> dict:
    global _cache, _cache_ts
    now = time.monotonic()
    if _cache is not None and now - _cache_ts < _CACHE_TTL:
        cached = dict(_cache)
        cached["topics"] = cached["topics"][:limit]
        cached["cached"] = True
        return cached

    items: list[HotTopic] = []
    errors: list[dict] = []
    for source_name, fetcher in (
        ("今日头条", _fetch_toutiao),
        ("百度热搜", _fetch_baidu),
        ("微博热搜", _fetch_weibo),
    ):
        try:
            items.extend(fetcher())
        except Exception as exc:  # Keep other sources alive when one source changes.
            errors.append({"source": source_name, "error": str(exc)})

    ranked = _merge_and_rank(items)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "topics": ranked,
        "source_count": len({item.source for item in items}),
        "errors": errors,
        "cached": False,
    }
    _cache = result
    _cache_ts = now

    out = dict(result)
    out["topics"] = ranked[:limit]
    return out


def _fetch_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _fetch_text(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _fetch_toutiao() -> list[HotTopic]:
    data = _fetch_json("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc")
    topics = []
    for index, item in enumerate(data.get("data") or [], start=1):
        title = str(item.get("Title") or "").strip()
        if not title:
            continue
        topics.append(HotTopic(
            title=title,
            source="今日头条",
            rank=index,
            heat=_safe_int(item.get("HotValue")),
            url=str(item.get("Url") or "").strip(),
            summary=str(item.get("Abstract") or "").strip(),
        ))
    return topics


def _fetch_baidu() -> list[HotTopic]:
    text = _fetch_text("https://top.baidu.com/board?tab=realtime")
    start = text.find("<!--s-data:")
    end = text.find("-->", start)
    if start < 0 or end < 0:
        raise ValueError("百度热搜页面结构无法解析")
    raw = text[start + len("<!--s-data:"):end]
    data = json.loads(raw)
    cards = (((data.get("data") or {}).get("cards")) or [])
    content = []
    for card in cards:
        if card.get("component") == "hotList":
            content = card.get("content") or []
            break
    topics = []
    for index, item in enumerate(content, start=1):
        title = str(item.get("word") or item.get("query") or "").strip()
        if not title:
            continue
        topics.append(HotTopic(
            title=html.unescape(title),
            source="百度热搜",
            rank=index,
            heat=_safe_int(item.get("hotScore")),
            url=str(item.get("url") or item.get("rawUrl") or "").strip(),
            summary=str(item.get("desc") or "").strip(),
        ))
    return topics


def _fetch_weibo() -> list[HotTopic]:
    """Fetch Weibo hot search (best-effort, no auth required for public list)."""
    text = _fetch_text("https://s.weibo.com/top/summary?cate=realtimehot", timeout=8)
    # Each row: <td class="td-02"><a ...>keyword</a> ...
    rows = re.findall(r'<td class="td-02"><a[^>]+>([^<]+)</a>', text)
    # Heat values: <td class="td-03">(\d+)</td>
    heats = re.findall(r'<td class="td-03">(\d+)</td>', text)
    topics = []
    for index, title in enumerate(rows[:50], start=1):
        title = html.unescape(title.strip())
        if not title or title in ("置顶",):
            continue
        heat = _safe_int(heats[index - 1]) if index - 1 < len(heats) else 0
        topics.append(HotTopic(
            title=title,
            source="微博热搜",
            rank=index,
            heat=heat,
        ))
    if not topics:
        raise ValueError("微博热搜页面结构无法解析")
    return topics


def _merge_and_rank(items: list[HotTopic]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for item in items:
        key = _normalize_title(item.title)
        if not key:
            continue
        current = grouped.setdefault(key, {
            "title": item.title,
            "summary": item.summary,
            "heat": 0,
            "score": 0.0,
            "sources": [],
            "links": [],
            "best_rank": item.rank,
        })
        current["heat"] = max(current["heat"], item.heat)
        current["best_rank"] = min(current["best_rank"], item.rank)
        current["score"] += (120 - min(item.rank, 100)) * 1000 + item.heat / 100
        if item.source not in current["sources"]:
            current["sources"].append(item.source)
            current["score"] += 50000
        if item.url:
            current["links"].append({"source": item.source, "url": item.url})
        if not current.get("summary") and item.summary:
            current["summary"] = item.summary

    ranked = sorted(grouped.values(), key=lambda x: (x["score"], x["heat"]), reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        item["source_text"] = " / ".join(item["sources"])
        item["heat_text"] = _format_heat(item["heat"])
    return ranked


def _normalize_title(title: str) -> str:
    clean = re.sub(r"\s+", "", title.strip().lower())
    clean = re.sub(r'''["'《》【】\[\]（）()，,。.!！?？:：;；·\-_/]''', "", clean)
    return clean


def _format_heat(value: int) -> str:
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return str(value) if value else "-"


def _safe_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
