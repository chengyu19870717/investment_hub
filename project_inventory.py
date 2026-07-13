from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9/3.10 compatibility.
    tomllib = None


DEFAULT_PROJECT_ROOT = Path.home() / "project"
LEGACY_PROJECT_ROOT = Path("/user/chengyu/project")

EXCLUDED_DIR_NAMES = {
    ".DS_Store",
    ".cache",
    ".claude",
    ".derivedData",
    ".derivedData-device",
    ".git",
    ".github",
    ".gradle",
    ".idea",
    ".playwright-cli",
    ".playwright-mcp",
    ".pytest_cache",
    ".qwen",
    ".swiftpm",
    ".venv",
    ".venv_py39_backup",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "logs",
    "node_modules",
    "output",
    "temp",
    "Temp",
    "venv",
}

README_CANDIDATES = (
    "README.md",
    "README.MD",
    "README.txt",
    "README",
    "README_OPTIMIZATION.md",
)

MARKER_FILES = (
    "README.md",
    "README.MD",
    "package.json",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "Package.swift",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "gradlew",
)


def resolve_project_root(root: str | Path | None = None) -> Path:
    if root:
        return Path(root).expanduser().resolve()
    if DEFAULT_PROJECT_ROOT.exists():
        return DEFAULT_PROJECT_ROOT.resolve()
    return LEGACY_PROJECT_ROOT.resolve()


def list_project_inventory(root: str | Path | None = None) -> dict:
    root_path = resolve_project_root(root)
    projects = []
    if root_path.exists():
        for path in sorted(root_path.iterdir(), key=lambda item: item.name.lower()):
            if is_top_level_project(path):
                projects.append(project_summary(path))

    updated_values = [item["updated_at"] for item in projects if item.get("updated_at")]
    return {
        "root": str(root_path),
        "exists": root_path.exists(),
        "total": len(projects),
        "updated_at": max(updated_values) if updated_values else "",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "projects": projects,
    }


def is_top_level_project(path: Path) -> bool:
    if not path.is_dir():
        return False
    name = path.name
    if name.startswith(".") or name in EXCLUDED_DIR_NAMES:
        return False
    return True


def project_summary(path: Path) -> dict:
    description, source = project_description(path)
    tags = project_tags(path)
    children = child_project_names(path)
    return {
        "name": path.name,
        "path": str(path),
        "description": description,
        "description_source": source,
        "tags": tags,
        "category": project_category(path, tags),
        "updated_at": format_mtime(path),
        "has_git": (path / ".git").exists(),
        "children": children,
        "child_count": len(children),
    }


def project_description(path: Path) -> tuple[str, str]:
    package_description = read_package_description(path)
    readme_description = read_readme_description(path)
    if readme_description:
        return readme_description, "README"
    if package_description:
        return package_description, "metadata"
    return infer_project_description(path), "inferred"


def read_package_description(path: Path) -> str:
    package_json = path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            desc = normalize_text(data.get("description", ""))
            if desc:
                return desc
        except Exception:
            pass

    pyproject = path / "pyproject.toml"
    if pyproject.exists() and tomllib:
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            desc = normalize_text((data.get("project") or {}).get("description", ""))
            if desc:
                return desc
        except Exception:
            pass
    if pyproject.exists():
        desc = read_pyproject_description_fallback(pyproject)
        if desc:
            return desc

    return ""


def read_pyproject_description_fallback(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    in_project = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_project = line == "[project]"
            continue
        if in_project and line.startswith("description"):
            match = re.match(r"description\s*=\s*['\"](.+)['\"]\s*$", line)
            if match:
                return normalize_text(match.group(1))
    return ""


def read_readme_description(path: Path) -> str:
    for name in README_CANDIDATES:
        readme = path / name
        if not readme.exists():
            continue
        try:
            text = readme.read_text(encoding="utf-8", errors="ignore")[:12000]
        except Exception:
            continue
        description = first_meaningful_readme_paragraph(text, path.name)
        if description:
            return description
    return ""


def first_meaningful_readme_paragraph(text: str, project_name: str = "") -> str:
    lines: list[str] = []
    fallback_title = ""
    in_code = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if line.startswith("# "):
            cleaned_title = clean_markdown_text(line.lstrip("# "))
            if cleaned_title and not fallback_title:
                fallback_title = cleaned_title
            continue
        if should_skip_readme_line(line):
            if lines:
                break
            continue
        cleaned = clean_markdown_text(line)
        if not cleaned:
            if lines:
                break
            continue
        is_quote = line.startswith(">")
        if is_probably_title(cleaned) and not lines and not is_quote and same_project_name(cleaned, project_name):
            continue
        lines.append(cleaned)
        if len(" ".join(lines)) >= 180:
            break
    description = trim_sentence(" ".join(lines), 220)
    if description:
        return description
    if fallback_title and not same_project_name(fallback_title, project_name):
        return trim_sentence(fallback_title, 220)
    return ""


def should_skip_readme_line(line: str) -> bool:
    if not line:
        return True
    lower = line.lower()
    if lower in {"---", "----"}:
        return True
    if line.startswith(("#", "<div", "</div", "<br", "<img", "<p", "</p")):
        return True
    if line.startswith(("[![", "![", "[english]", "[中文]", "[简体中文]")):
        return True
    if is_readme_language_switcher(line) or is_readme_navigation_line(line):
        return True
    if re.match(r"^\[[^\]]+\]:\s*https?://", line):
        return True
    if re.match(r"^\d+[.)]\s+", line):
        return True
    if line.startswith("|") and line.endswith("|"):
        return True
    if "shields.io" in lower or "github.com" in lower and "badge" in lower:
        return True
    if lower in {"table of contents", "features", "installation", "usage", "development"}:
        return True
    return False


def is_readme_language_switcher(line: str) -> bool:
    language_words = ("english", "日本語", "简体中文", "繁體中文", "한국어", "français")
    lower = line.lower()
    return "|" in line and sum(word in lower for word in language_words) >= 2


def is_readme_navigation_line(line: str) -> bool:
    return "](#" in line and ("|" in line or "•" in line or "·" in line)


def clean_markdown_text(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
    cleaned = re.sub(r"[*_`>#-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -:：")


def is_probably_title(text: str) -> bool:
    return len(text) <= 36 and not any(ch in text for ch in "，。；,.") and len(text.split()) <= 4


def same_project_name(text: str, project_name: str) -> bool:
    if not project_name:
        return False
    normalize = lambda value: re.sub(r"[^0-9a-z]+", "", value.lower())
    return normalize(text) == normalize(project_name)


def infer_project_description(path: Path) -> str:
    tags = project_tags(path)
    child_names = child_project_names(path)
    if child_names:
        children = "、".join(child_names[:4])
        return f"本地项目工作区，包含 {children} 等子目录。"
    if "iOS" in tags or "Swift" in tags:
        return "Apple 平台应用项目，包含 Xcode/Swift 相关工程文件。"
    if "Java" in tags:
        return "Java/Gradle 工程项目，包含后端或桌面应用构建结构。"
    if "Python" in tags:
        return "Python 项目目录，包含脚本、数据处理或服务端代码。"
    if "前端" in tags:
        return "前端或 Web 应用项目，包含页面、构建配置或静态资源。"
    if "知识库" in tags:
        return "文档知识库项目，用于沉淀主题资料和经验文档。"
    return "本地项目目录，暂未识别到 README 或标准项目元数据。"


def project_tags(path: Path) -> list[str]:
    tags: list[str] = []
    add = tags.append

    if (path / ".git").exists():
        add("Git")
    if (path / "pyproject.toml").exists() or (path / "requirements.txt").exists() or any(path.glob("*.py")):
        add("Python")
    if (path / "package.json").exists() or (path / "frontend").exists():
        add("前端")
    if (path / "package.json").exists():
        add("Node")
    if (path / "build.gradle").exists() or (path / "gradlew").exists():
        add("Java")
    if (path / "Package.swift").exists():
        add("Swift")
    if any(path.glob("*.xcodeproj")):
        add("iOS")
    if (path / "docs").exists() or (path / "Docs").exists():
        add("文档")
    if (path / "data").exists() or (path / "MuseumData").exists():
        add("数据")
    if path.name in {"kg"}:
        add("知识库")
    if path.name in {"old_version"}:
        add("归档")
    if path.name in {"ComfyUI", "kohya_ss"}:
        add("AI")
    if path.name in {"investment_hub", "toolbox", "flowtool"}:
        add("工具箱")

    return dedupe(tags) or ["目录"]


def project_category(path: Path, tags: list[str]) -> str:
    if "归档" in tags:
        return "归档"
    if "知识库" in tags:
        return "知识库"
    if "AI" in tags:
        return "AI 工具"
    if "iOS" in tags or "Swift" in tags:
        return "Apple 应用"
    if "工具箱" in tags:
        return "工具应用"
    if "Java" in tags or "前端" in tags or "Python" in tags:
        return "开发项目"
    if child_project_names(path):
        return "工作区"
    return "项目"


def child_project_names(path: Path) -> list[str]:
    names: list[str] = []
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name.lower())
    except Exception:
        return names
    for child in children:
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in EXCLUDED_DIR_NAMES:
            continue
        if looks_like_subproject(child) or len(names) < 4:
            names.append(child.name)
        if len(names) >= 8:
            break
    return names


def looks_like_subproject(path: Path) -> bool:
    if (path / ".git").exists():
        return True
    if any((path / marker).exists() for marker in MARKER_FILES):
        return True
    if any(path.glob("*.xcodeproj")):
        return True
    return False


def format_mtime(path: Path) -> str:
    try:
        stat = path.stat()
        return datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def trim_sentence(text: str, limit: int) -> str:
    text = normalize_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ，,。.;；") + "…"


def dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
