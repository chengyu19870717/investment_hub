"""AI 用量采集与分析：Claude Code / Codex / DeepSeek。

各渠道能力边界（实测确认，不要凭直觉扩大）：
- **Codex**：`~/.codex/sessions/**/*.jsonl` 的 `token_count` 事件，既有每轮 token，
  又有官方 `rate_limits`（5小时=300分钟窗口、7天=10080分钟窗口、used_percent、resets_at）。
  唯一能拿到"官方口径限额"的渠道。
- **Claude Code**：`~/.claude/projects/**/*.jsonl` 每条 assistant 消息带 usage（四类 token + 模型）。
  **本地不存任何限额信息**（已逐字段搜过），所以 5小时/7天只能由我们自己按滚动窗口累加用量，
  配额上限需用户自己填（不填就只显示用量不显示百分比）。
- **DeepSeek**：官方无 usage 接口（/user/usage 返回 404），只有 /user/balance 余额。
  历史消费靠**余额快照差值**反推，因此第一天没有对比基准，只有余额没有消费额。

摄取策略：jsonl 只追加，按 (文件路径 → 已读偏移量) 增量解析，每行只解析一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import glob
import hashlib
import json
import os
import urllib.request

CLAUDE_GLOB = str(Path.home() / ".claude" / "projects" / "**" / "*.jsonl")
CODEX_GLOB = str(Path.home() / ".codex" / "sessions" / "**" / "*.jsonl")
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"

CHANNELS = {
    "claude": {"label": "Claude Code", "icon": "🤖"},
    "codex": {"label": "Codex", "icon": "🧑‍💻"},
    "deepseek": {"label": "DeepSeek", "icon": "🐬"},
}

# 滚动窗口，与各家限额口径对齐
WINDOW_5H = 300      # 分钟
WINDOW_7D = 10080    # 分钟


@dataclass
class UsageRecord:
    channel: str
    ts: str            # ISO8601 UTC
    dedupe_key: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    total_tokens: int
    session_id: str
    project: str


# ── 解析 ──────────────────────────────────────────────────

def _project_of(path: str, channel: str, cwd: str | None = None) -> str:
    """项目名优先取记录里的 cwd —— 那是权威来源。

    退化方案是解析目录名 `-Users-chengyu-project-investment-hub`，但连字符
    既可能是路径分隔符也可能是目录名本身的字符（investment-hub），
    从名字反推必然出错，只在没有 cwd 时才用。
    """
    if channel != "claude":
        return "codex"
    if cwd:
        try:
            return str(Path(cwd).relative_to(Path.home()))
        except ValueError:
            return cwd
    name = Path(path).parent.name
    return name.replace("-Users-chengyu-", "").replace("-", "/") or name


def parse_claude_line(line: str, path: str) -> UsageRecord | None:
    if '"usage"' not in line:
        return None
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    msg = d.get("message") or {}
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    ts = d.get("timestamp")
    if not ts:
        return None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cr = int(usage.get("cache_read_input_tokens") or 0)
    cw = int(usage.get("cache_creation_input_tokens") or 0)
    # uuid 做去重键：续接会话（--resume）会把历史消息重放进新文件，不去重会双计
    key = d.get("uuid") or hashlib.md5(f"{ts}{inp}{out}{cr}{cw}".encode()).hexdigest()
    return UsageRecord(
        channel="claude", ts=ts, dedupe_key=f"claude:{key}",
        model=msg.get("model") or "unknown",
        input_tokens=inp, output_tokens=out, cache_read=cr, cache_write=cw,
        total_tokens=inp + out + cr + cw,
        session_id=d.get("sessionId") or "",
        project=_project_of(path, "claude", d.get("cwd")),
    )


def parse_codex_line(line: str, path: str) -> tuple[UsageRecord | None, dict | None]:
    """返回 (用量记录, 限额快照)。限额只在最新一条有意义，由调用方取最后一个。"""
    if '"token_count"' not in line:
        return None, None
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None, None
    payload = d.get("payload") or {}
    if payload.get("type") != "token_count":
        return None, None
    ts = d.get("timestamp")
    info = payload.get("info") or {}
    # last_token_usage = 本轮增量；total_token_usage 是会话累计，累加会严重高估
    last = info.get("last_token_usage") or {}
    limits = payload.get("rate_limits")
    rec = None
    if ts and last:
        inp = int(last.get("input_tokens") or 0)
        out = int(last.get("output_tokens") or 0)
        cr = int(last.get("cached_input_tokens") or 0)
        cw = int(last.get("cache_write_input_tokens") or 0)
        if inp or out:
            key = hashlib.md5(f"{path}{ts}{inp}{out}{cr}{cw}".encode()).hexdigest()
            # Codex 的 input_tokens 含缓存部分，Claude 的不含。存库前统一成
            # 「四类互斥」口径：input 只留非缓存部分，否则同表混算时
            # 各类占比会加起来超过 100%。总量仍是 inp+out，与官方一致。
            rec = UsageRecord(
                channel="codex", ts=ts, dedupe_key=f"codex:{key}",
                model="codex", input_tokens=max(0, inp - cr - cw), output_tokens=out,
                cache_read=cr, cache_write=cw,
                total_tokens=inp + out,
                session_id=Path(path).stem[-36:], project="codex",
            )
    snapshot = None
    if limits and ts:
        # 按窗口拆开：同一条事件里 primary/secondary 谁是 5小时谁是 7天并不固定，
        # 而且最新一条可能只带其中一个窗口（secondary 为 null）。
        # 若整条取最新，另一个窗口的限额就会凭空消失。
        per_window = {}
        for slot in ("primary", "secondary"):
            item = limits.get(slot)
            if item and item.get("window_minutes"):
                per_window[str(item["window_minutes"])] = item
        if per_window:
            snapshot = {"ts": ts, "windows": per_window,
                        "plan_type": limits.get("plan_type"),
                        "credits": limits.get("credits")}
    return rec, snapshot


def iter_new_lines(path: str, offset: int):
    """从 offset 续读。文件变短说明被轮转/重写过，退回从头读。"""
    size = os.path.getsize(path)
    if offset > size:
        offset = 0
    with open(path, "r", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            yield line
        yield None  # 哨兵：告诉调用方读完了
        return fh.tell()


def scan_channel(channel: str, offsets: dict[str, int]) -> tuple[list[UsageRecord], dict, dict]:
    """扫描一个渠道的全部日志文件，只读各文件未读过的部分。

    返回 (新记录, 新的偏移量表, 最新限额快照)。
    """
    pattern = CLAUDE_GLOB if channel == "claude" else CODEX_GLOB
    records: list[UsageRecord] = []
    new_offsets: dict[str, int] = {}
    latest_limits: dict | None = None
    for path in glob.glob(pattern, recursive=True):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        start = offsets.get(path, 0)
        if start > size:
            start = 0          # 文件被重写，重新来过
        if start == size:
            new_offsets[path] = size
            continue
        pos = start
        try:
            with open(path, "r", errors="replace") as fh:
                fh.seek(start)
                for line in fh:
                    if channel == "claude":
                        rec = parse_claude_line(line, path)
                        if rec:
                            records.append(rec)
                    else:
                        rec, snap = parse_codex_line(line, path)
                        if rec:
                            records.append(rec)
                        if snap:
                            # 每个窗口各自保留最新的一条
                            if latest_limits is None:
                                latest_limits = {"ts": snap["ts"], "windows": {},
                                                 "plan_type": snap.get("plan_type"),
                                                 "credits": snap.get("credits")}
                            for wm, item in snap["windows"].items():
                                cur = latest_limits["windows"].get(wm)
                                if cur is None or snap["ts"] > cur["_ts"]:
                                    latest_limits["windows"][wm] = {**item, "_ts": snap["ts"]}
                            if snap["ts"] >= latest_limits["ts"]:
                                latest_limits["ts"] = snap["ts"]
                                latest_limits["plan_type"] = snap.get("plan_type")
                                latest_limits["credits"] = snap.get("credits")
                pos = fh.tell()
        except OSError:
            continue
        new_offsets[path] = pos
    return records, new_offsets, latest_limits


# ── DeepSeek ─────────────────────────────────────────────

def fetch_deepseek_balance(api_key: str, timeout: int = 15) -> dict:
    """查余额。带自签证书环境的兜底：用户本机开了代理做 SSL 拦截时，
    标准 urlopen 会 CERTIFICATE_VERIFY_FAILED，此时退回系统 curl（走系统钥匙串）。"""
    req = urllib.request.Request(DEEPSEEK_BALANCE_URL,
                                 headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        import subprocess
        out = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), DEEPSEEK_BALANCE_URL,
             "-H", f"Authorization: Bearer {api_key}"],
            capture_output=True, text=True, timeout=timeout + 5)
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError(f"余额查询失败：{out.stderr.strip() or '无响应'}")
        return json.loads(out.stdout)


def cny_balance(payload: dict) -> float | None:
    for info in payload.get("balance_infos") or []:
        if info.get("currency") == "CNY":
            try:
                return float(info.get("total_balance"))
            except (TypeError, ValueError):
                return None
    return None


# ── 时间与聚合 ────────────────────────────────────────────

def to_local(ts: str) -> datetime:
    """日志里是 UTC（尾部 Z）。日/周/月分桶必须按本地时区，否则跨零点的用量会算错天。"""
    text = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def bucket_key(dt: datetime, period: str) -> str:
    if period == "day":
        return dt.strftime("%Y-%m-%d")
    if period == "week":
        iso = dt.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if period == "month":
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y")


def window_usage(rows: list[dict], minutes: int) -> dict:
    """滚动窗口内的用量。Claude 没有官方限额时，这是唯一能自算的口径。"""
    now = datetime.now().astimezone()
    since = now - timedelta(minutes=minutes)
    total = tokens_in = tokens_out = calls = 0
    earliest: datetime | None = None
    for row in rows:
        dt = to_local(row["ts"])
        if dt < since:
            continue
        calls += 1
        total += row["total_tokens"]
        tokens_in += row["input_tokens"] + row["cache_read"] + row["cache_write"]
        tokens_out += row["output_tokens"]
        if earliest is None or dt < earliest:
            earliest = dt
    # 滚动窗口的"重置"= 窗口内最早一次调用滑出窗口的时刻
    resets_at = (earliest + timedelta(minutes=minutes)).isoformat() if earliest else None
    return {
        "window_minutes": minutes,
        "total_tokens": total,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "calls": calls,
        "resets_at": resets_at,
        "since": since.isoformat(),
    }


def aggregate(rows: list[dict], period: str) -> list[dict]:
    buckets: dict[str, dict] = {}
    for row in rows:
        key = bucket_key(to_local(row["ts"]), period)
        b = buckets.setdefault(key, {
            "bucket": key, "total_tokens": 0, "input_tokens": 0,
            "output_tokens": 0, "cache_read": 0, "cache_write": 0, "calls": 0,
        })
        b["total_tokens"] += row["total_tokens"]
        b["input_tokens"] += row["input_tokens"]
        b["output_tokens"] += row["output_tokens"]
        b["cache_read"] += row["cache_read"]
        b["cache_write"] += row["cache_write"]
        b["calls"] += 1
    return sorted(buckets.values(), key=lambda b: b["bucket"])


def by_model(rows: list[dict], limit: int = 8) -> list[dict]:
    agg: dict[str, dict] = {}
    for row in rows:
        m = row.get("model") or "unknown"
        b = agg.setdefault(m, {"model": m, "total_tokens": 0, "calls": 0})
        b["total_tokens"] += row["total_tokens"]
        b["calls"] += 1
    return sorted(agg.values(), key=lambda x: -x["total_tokens"])[:limit]


def by_project(rows: list[dict], limit: int = 10) -> list[dict]:
    agg: dict[str, dict] = {}
    for row in rows:
        p = row.get("project") or "-"
        b = agg.setdefault(p, {"project": p, "total_tokens": 0, "calls": 0})
        b["total_tokens"] += row["total_tokens"]
        b["calls"] += 1
    return sorted(agg.values(), key=lambda x: -x["total_tokens"])[:limit]


def parse_codex_limits(snapshot: dict) -> list[dict]:
    """把按窗口收集的限额归一成「5小时 / 7天」两条，附重置时间与该条的采集时刻。"""
    out = []
    now = datetime.now().astimezone()
    for wm, item in (snapshot.get("windows") or {}).items():
        minutes = int(wm)
        resets = item.get("resets_at")
        resets_dt = datetime.fromtimestamp(resets).astimezone() if resets else None
        captured = item.get("_ts")
        # 快照过期判定：limit 是某一刻的瞬时值，一旦重置时刻已过，
        # 这个 used_percent 描述的是上一个窗口，照搬显示会误导（例如显示已用 100%
        # 而实际窗口早就重置清零了）。Codex 只在运行时写这个字段，久不用就会陈旧。
        stale = bool(resets_dt and resets_dt < now)
        out.append({
            "label": "5小时限制" if minutes == WINDOW_5H else
                     ("7天限制" if minutes == WINDOW_7D else f"{minutes // 60}小时限制"),
            "window_minutes": minutes,
            "used_percent": item.get("used_percent"),
            "resets_at": resets_dt.isoformat() if resets_dt else None,
            "captured_at": captured,
            "stale": stale,
            "stale_hint": "该窗口已重置，此为上一周期的旧快照；在 Codex 里跑一次即可刷新" if stale else "",
        })
    return sorted(out, key=lambda x: x["window_minutes"])
