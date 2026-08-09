"""文件整理：按关键词/扩展名规则，把目录根下的散落文件归入分类子目录。

设计约束（都是有意为之，改动前先读）：
1. **只动根目录下的散落文件**，不递归、不移动已有子目录 —— 子目录名往往已承载用户的路径记忆。
2. **只在 $HOME 之下工作**，且不允许把 $HOME 本身当整理目标（否则会把 Desktop/Documents 这些系统目录当成散落文件的邻居乱扫）。
3. **绝不覆盖**：目标同名一律改名为 `xxx (1).ext`，宁可留冗余也不丢数据。
4. 纯规则、零网络、零 AI —— 结果可预测、可复现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re

HOME = Path.home()

# 常驻不整理的目录名：这些是 macOS 或工具自建的，混进分类里只会添乱
PROTECTED_DIR_NAMES = {".Trash", "Library", "Applications", "System", "Volumes"}

# 兜底分类：所有规则都没命中时的去处
FALLBACK_CATEGORY = "其他-待整理"

# 预置规则。match_type: keyword=匹配文件名; ext=匹配扩展名(小写,不含点)
# priority 小的先匹配，命中即停
DEFAULT_RULES = [
    {"category": "工作-信贷数据安全", "match_type": "keyword", "pattern": "数据安全|脱敏|导出审批|数据导出", "priority": 10},
    {"category": "工作-信贷业务",     "match_type": "keyword", "pattern": "信贷|授信|用信|审查池|审查量|驻场调研|建档授权", "priority": 20},
    {"category": "工作-绩效考核",     "match_type": "keyword", "pattern": "绩效|考核|打分机制|项目评价|责任认定", "priority": 30},
    {"category": "工作-会议纪要",     "match_type": "keyword", "pattern": "会议|纪要|周会|例会|汇报", "priority": 40},
    {"category": "投研报告",         "match_type": "keyword", "pattern": "研究报告|研报|投资分析|深度研究|财报", "priority": 50},
    {"category": "项目文档",         "match_type": "keyword", "pattern": "PRD|需求|方案|设计文档|架构", "priority": 60},
    {"category": "个人",             "match_type": "keyword", "pattern": "简历|个税|退税|报销|体检|影评|理财", "priority": 70},
    {"category": "安装包",           "match_type": "ext",     "pattern": "dmg|pkg|app|exe|msi", "priority": 80},
    {"category": "压缩包",           "match_type": "ext",     "pattern": "zip|7z|rar|tar|gz", "priority": 90},
    {"category": "图片截图",         "match_type": "ext",     "pattern": "png|jpg|jpeg|gif|webp|heic|bmp", "priority": 100},
    {"category": "文档",             "match_type": "ext",     "pattern": "doc|docx|pdf|md|rtf|txt|pptx|ppt", "priority": 110},
    {"category": "表格",             "match_type": "ext",     "pattern": "xls|xlsx|csv", "priority": 120},
]

# 页面上默认列出的候选目录
SUGGESTED_DIRS = [HOME / "Downloads", HOME / "Documents", HOME / "Desktop"]


class OrganizerError(Exception):
    """路径非法、目录不存在等可预期错误，交给接口层转成 400。"""


@dataclass
class PlannedMove:
    filename: str
    category: str
    rule_id: int | None
    reason: str
    dest_name: str  # 落地后的文件名（可能因重名被改写）


@dataclass
class OrganizeResult:
    directory: str
    moved: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)


# ── 路径安全 ──────────────────────────────────────────────

def safe_dir(raw: str) -> Path:
    """把用户输入的路径收敛成一个可以安全整理的目录。

    这是整个功能唯一的信任边界：接口层拿到的任何路径都必须先过这里。
    """
    if not raw or not raw.strip():
        raise OrganizerError("目录不能为空")
    text = raw.strip()
    if text.startswith("~"):
        text = str(Path(text).expanduser())
    path = Path(text)
    if not path.is_absolute():
        raise OrganizerError("请使用绝对路径")
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise OrganizerError(f"目录不存在：{text}")
    if not path.is_dir():
        raise OrganizerError(f"不是目录：{text}")
    home = HOME.resolve()
    if path == home:
        raise OrganizerError("不能直接整理用户主目录，请指定其下的具体目录")
    if home not in path.parents:
        raise OrganizerError("出于安全考虑，只能整理主目录（~）下的目录")
    if any(part in PROTECTED_DIR_NAMES for part in path.parts):
        raise OrganizerError("该目录受保护，不允许整理")
    return path


# ── 规则匹配 ──────────────────────────────────────────────

def _split_pattern(pattern: str) -> list[str]:
    return [p.strip() for p in re.split(r"[|,，]", pattern or "") if p.strip()]


def match_rule(filename: str, rules: list[dict]) -> tuple[str, int | None, str]:
    """返回 (分类名, 命中规则id, 命中理由)。没命中则落到兜底分类。

    规则按 priority 升序，命中即停 —— 所以「信贷数据安全」的 priority 必须小于
    「信贷业务」，否则 "信贷系统数据安全改造.jpg" 会先被后者吃掉。
    这个"先具体后宽泛"的排序是规则库的核心约定。
    """
    name = filename.lower()
    ext = Path(filename).suffix.lstrip(".").lower()
    for rule in sorted(rules, key=lambda r: (r.get("priority", 999), r.get("id", 0))):
        if not rule.get("enabled", 1):
            continue
        tokens = _split_pattern(rule.get("pattern", ""))
        if rule.get("match_type") == "ext":
            if ext and ext in [t.lower() for t in tokens]:
                return rule["category"], rule.get("id"), f"扩展名 .{ext}"
        else:
            for token in tokens:
                if token.lower() in name:
                    return rule["category"], rule.get("id"), f"文件名含「{token}」"
    return FALLBACK_CATEGORY, None, "未命中任何规则"


# ── 扫描与执行 ────────────────────────────────────────────

def _loose_files(directory: Path, include_hidden: bool) -> list[Path]:
    """目录根下的散落文件（不含子目录）。隐藏文件默认跳过 —— .DS_Store /
    .localized 这类是系统文件，归类没有意义还会污染分类目录。"""
    items = []
    for entry in sorted(directory.iterdir()):
        if entry.is_dir():
            continue
        if entry.name.startswith(".") and not include_hidden:
            continue
        items.append(entry)
    return items


def _unique_dest(dest_dir: Path, filename: str, taken: set[str]) -> str:
    """同名不覆盖：追加 (1)(2)…。taken 记录本批次已占用的名字，
    避免同一批里两个同名文件互相踩。"""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate, n = filename, 1
    while (dest_dir / candidate).exists() or candidate in taken:
        candidate = f"{stem} ({n}){suffix}"
        n += 1
    taken.add(candidate)
    return candidate


def plan(directory: Path, rules: list[dict], include_hidden: bool = False) -> list[PlannedMove]:
    """算出每个散落文件该去哪，不动磁盘。执行和预览共用这套计算。"""
    planned: list[PlannedMove] = []
    taken: dict[str, set[str]] = {}
    for entry in _loose_files(directory, include_hidden):
        category, rule_id, reason = match_rule(entry.name, rules)
        dest_dir = directory / category
        seen = taken.setdefault(category, set())
        dest_name = _unique_dest(dest_dir, entry.name, seen)
        planned.append(PlannedMove(entry.name, category, rule_id, reason, dest_name))
    return planned


def organize(directory: Path, rules: list[dict], include_hidden: bool = False) -> OrganizeResult:
    """真正执行移动。逐个文件容错：单个失败不中断整批，失败项记进 skipped。"""
    result = OrganizeResult(directory=str(directory))
    for item in plan(directory, rules, include_hidden):
        src = directory / item.filename
        dest_dir = directory / item.category
        dest = dest_dir / item.dest_name
        if not src.exists():           # 计划到执行之间文件被动过
            result.skipped.append({"filename": item.filename, "reason": "文件已不存在"})
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
        except OSError as exc:
            result.skipped.append({"filename": item.filename, "reason": f"移动失败：{exc}"})
            continue
        result.moved.append({
            "filename": item.filename,
            "category": item.category,
            "reason": item.reason,
            "src": str(src),
            "dest": str(dest),
        })
        result.categories[item.category] = result.categories.get(item.category, 0) + 1
    return result


def undo(moves: list[dict]) -> dict:
    """把一次整理原样搬回去。只还原确实在目标位置、且源位置已空出来的文件，
    其余跳过 —— 撤销本身绝不能造成第二次覆盖。"""
    restored, failed = 0, []
    for move in reversed(moves):
        src, dest = Path(move["src"]), Path(move["dest"])
        if not dest.exists():
            failed.append({"filename": move["filename"], "reason": "文件已不在整理后的位置"})
            continue
        if src.exists():
            failed.append({"filename": move["filename"], "reason": "原位置已有同名文件"})
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            dest.rename(src)
            restored += 1
        except OSError as exc:
            failed.append({"filename": move["filename"], "reason": f"还原失败：{exc}"})
    # 顺手清掉因还原而变空的分类目录
    for move in moves:
        cat_dir = Path(move["dest"]).parent
        try:
            if cat_dir.is_dir() and not any(cat_dir.iterdir()):
                cat_dir.rmdir()
        except OSError:
            pass
    return {"restored": restored, "failed": failed}


def dir_summary(directory: Path, include_hidden: bool = False) -> dict:
    """页面上给每个目录显示的概况：散落几个文件、已有几个子目录。"""
    loose = _loose_files(directory, include_hidden)
    subdirs = [d for d in directory.iterdir() if d.is_dir() and not d.name.startswith(".")]
    return {
        "path": str(directory),
        "name": directory.name,
        "loose_count": len(loose),
        "subdir_count": len(subdirs),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
