from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import html
import re
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_REQUIREMENT_DOC_DIR = Path.home() / "Documents" / "需求文档工作流"
MODEL_OPTIONS = ("Deepseek", "Claude", "Codex")


SECTION_OPTIONS = [
    {"id": "overview", "label": "需求概览", "description": "背景、目标、核心价值、适用范围"},
    {"id": "stakeholders", "label": "用户角色与使用场景", "description": "用户角色、使用对象、典型业务场景"},
    {"id": "scope", "label": "范围边界", "description": "本期范围、不做范围、依赖前置条件"},
    {"id": "workflow", "label": "业务流程", "description": "主流程、异常流程、状态流转"},
    {"id": "features", "label": "功能需求清单", "description": "功能点、优先级、输入输出、说明"},
    {"id": "data", "label": "数据与字段", "description": "核心数据对象、字段、数据来源、留存规则"},
    {"id": "rules", "label": "业务规则", "description": "校验规则、权限规则、计算规则、限制条件"},
    {"id": "interaction", "label": "页面与交互", "description": "页面入口、操作控件、提示、复制/导出等交互"},
    {"id": "nonfunctional", "label": "非功能需求", "description": "性能、安全、兼容、稳定性、可维护性"},
    {"id": "acceptance", "label": "验收标准", "description": "可测试的验收条件和通过标准"},
    {"id": "risks", "label": "风险与待确认问题", "description": "风险、依赖、开放问题、后续补充"},
    {"id": "collaboration", "label": "模型协作与优化指令", "description": "Deepseek 初稿、Claude/Codex CLI 优化、人工合并规则"},
    {"id": "consensus", "label": "共识与人工确认", "description": "Deepseek、Claude、Codex、本人确认状态"},
    {"id": "versions", "label": "版本记录", "description": "版本编号、生成模型、变更说明"},
]


@dataclass
class RequirementDocResult:
    path: str
    filename: str
    model_name: str
    requirement_name: str
    version: str
    created_at: str


def parse_requirement_description(description: str) -> dict:
    description = normalize_space(description)
    requirement_name = infer_requirement_name(description)
    points = extract_feature_points(description)
    return {
        "requirement_name": requirement_name,
        "feature_points": points,
        "selected_sections": [item["id"] for item in SECTION_OPTIONS],
    }


def parse_requirement_docx(path: str | Path) -> dict:
    doc_path = Path(path).expanduser().resolve()
    if not doc_path.exists():
        raise FileNotFoundError(f"文档不存在：{doc_path}")
    if doc_path.suffix.lower() != ".docx":
        raise ValueError("仅支持解析 .docx 文件")

    paragraphs = read_docx_paragraphs(doc_path)
    raw_text = "\n".join(paragraphs)
    parsed_name = parse_doc_filename(doc_path.name)
    requirement_name = parsed_name.get("requirement_name") or infer_requirement_name(raw_text)
    feature_points = extract_points_between(paragraphs, "解析出的功能点", "需求要素选项")
    if not feature_points:
        feature_points = extract_points_between(paragraphs, "功能需求清单", "数据与字段")
    if not feature_points:
        feature_points = extract_feature_points(raw_text)
    selected_sections = extract_selected_section_ids(paragraphs)
    if not selected_sections:
        selected_sections = [item["id"] for item in SECTION_OPTIONS]

    description = extract_between_text(paragraphs, "原始描述", "目标说明")
    if not description:
        description = raw_text[:3000]

    return {
        "path": str(doc_path),
        "filename": doc_path.name,
        "model_name": parsed_name.get("model_name") or "",
        "requirement_name": requirement_name,
        "version": parsed_name.get("version") or "",
        "description": description,
        "feature_points": normalize_feature_points(feature_points),
        "selected_sections": selected_sections,
        "raw_text": raw_text,
    }


def list_requirement_documents(save_dir: str | Path) -> list[dict]:
    directory = resolve_save_dir(save_dir)
    if not directory.exists():
        return []

    docs = []
    for path in sorted(directory.glob("*.docx"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        parsed = parse_doc_filename(path.name)
        docs.append({
            "filename": path.name,
            "path": str(path),
            "model_name": parsed.get("model_name", ""),
            "requirement_name": parsed.get("requirement_name", ""),
            "version": parsed.get("version", ""),
            "size_kb": max(1, round(stat.st_size / 1024)),
            "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return docs


def next_requirement_version(
    save_dir: str | Path,
    model_name: str,
    requirement_name: str,
    source_doc_path: str | Path = "",
) -> str:
    directory = resolve_save_dir(save_dir)
    safe_model = sanitize_component(model_name, fallback="Deepseek")
    safe_req = sanitize_component(requirement_name, fallback="需求文档")
    start_major = next_major_from_source(source_doc_path, requirement_name)
    if start_major <= 0:
        start_major = max_requirement_major(directory, requirement_name) + 1

    major = max(1, start_major)
    while (directory / f"{safe_model}_{safe_req}_V{major}.0.docx").exists():
        major += 1
    return f"V{major}.0"


def create_requirement_doc(
    save_dir: str | Path,
    model_name: str,
    requirement_name: str,
    description: str,
    feature_points: list[str],
    selected_sections: list[str],
    author: str = "程钰",
    source_doc_path: str = "",
    source_doc_text: str = "",
    iteration_notes: str = "",
) -> RequirementDocResult:
    model_name = normalize_model_name(model_name)
    requirement_name = normalize_space(requirement_name) or infer_requirement_name(description)
    description = normalize_space(description)
    feature_points = normalize_feature_points(feature_points or extract_feature_points(description))
    selected_sections = [item for item in selected_sections if item in {x["id"] for x in SECTION_OPTIONS}]
    if not selected_sections:
        selected_sections = [item["id"] for item in SECTION_OPTIONS]

    directory = resolve_save_dir(save_dir)
    directory.mkdir(parents=True, exist_ok=True)
    version = next_requirement_version(directory, model_name, requirement_name, source_doc_path)
    filename = f"{sanitize_component(model_name)}_{sanitize_component(requirement_name)}_{version}.docx"
    path = directory / filename
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    peer_versions = {
        model: next_requirement_version(directory, model, requirement_name, str(path))
        for model in MODEL_OPTIONS
    }

    document_xml = build_document_xml(
        model_name=model_name,
        requirement_name=requirement_name,
        version=version,
        description=description,
        feature_points=feature_points,
        selected_sections=selected_sections,
        author=author,
        created_at=created_at,
        output_path=str(path),
        save_dir=str(directory),
        peer_versions=peer_versions,
        source_doc_path=source_doc_path,
        source_doc_text=source_doc_text,
        iteration_notes=iteration_notes,
    )
    write_docx_package(path, document_xml, requirement_name, author, created_at)
    return RequirementDocResult(
        path=str(path),
        filename=filename,
        model_name=model_name,
        requirement_name=requirement_name,
        version=version,
        created_at=created_at,
    )


def resolve_save_dir(save_dir: str | Path) -> Path:
    raw = str(save_dir or "").strip()
    if not raw:
        return DEFAULT_REQUIREMENT_DOC_DIR
    return Path(raw).expanduser().resolve()


def normalize_model_name(model_name: str) -> str:
    normalized = str(model_name or "").strip().lower()
    mapping = {
        "deepseek": "Deepseek",
        "deepseek-v3": "Deepseek",
        "deepseek-v4": "Deepseek",
        "claude": "Claude",
        "claude code": "Claude",
        "codex": "Codex",
    }
    return mapping.get(normalized, "Deepseek")


def infer_requirement_name(description: str) -> str:
    text = normalize_space(description)
    if not text:
        return "未命名需求"

    explicit = re.search(r"(?:需求名称|需求名|功能名称|功能名)\s*(?:叫|为|是|[:：])\s*[“”\"']?([^，。；;、\n\"'“”]+)", text)
    if explicit:
        name = explicit.group(1).strip()
        if name:
            return name[:24]

    for line in split_nonempty_lines(text):
        clean = re.sub(r"^(需求名称|需求名|标题|功能名称|功能名)\s*[:：]\s*", "", line).strip()
        clean = re.sub(r"^[#\-*、\d.()\s]+", "", clean).strip()
        if 2 <= len(clean) <= 36:
            return clean[:24]

    sentence = re.split(r"[。！？!?；;\n]", text, maxsplit=1)[0].strip()
    sentence = re.sub(r"^(我要|我想|需要|帮我|请|支持|实现|增加|新增|做一个|创建一个)", "", sentence).strip()
    return (sentence or text)[:24]


def extract_feature_points(description: str) -> list[str]:
    text = normalize_space(description)
    if not text:
        return []

    candidates: list[str] = []
    for line in split_nonempty_lines(text):
        normalized = re.sub(r"^\s*(?:[-*•]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*", "", line).strip()
        parts = re.split(r"[；;。]\s*", normalized)
        candidates.extend(part for part in parts if part.strip())

    if len(candidates) < 2:
        candidates = [part.strip() for part in re.split(r"[，,。；;\n]", text) if part.strip()]

    points = []
    seen = set()
    keywords = ("支持", "需要", "可以", "能够", "通过", "生成", "解析", "创建", "打开", "展示", "复制", "选择", "维护", "配置")
    for item in candidates:
        clean = re.sub(r"\s+", " ", item).strip(" ：:，,。")
        if not clean:
            continue
        if len(clean) > 80:
            clean = clean[:80]
        if len(clean) < 4:
            continue
        if points and not any(word in clean for word in keywords) and len(points) >= 3:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        points.append(clean)
        if len(points) >= 18:
            break

    if not points:
        points.append(text[:80])
    return points


def normalize_feature_points(points: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for point in points:
        clean = normalize_space(str(point or "")).strip(" ：:，,。")
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(clean)
    return normalized


def parse_doc_filename(filename: str) -> dict:
    match = re.match(r"^(Deepseek|Claude|Codex)_(.+)_(V\d+\.0)\.docx$", filename, re.I)
    if not match:
        return {}
    return {
        "model_name": normalize_model_name(match.group(1)),
        "requirement_name": match.group(2),
        "version": match.group(3),
    }


def version_major(version: str) -> int:
    match = re.match(r"V(\d+)\.0$", str(version or "").strip(), re.I)
    return int(match.group(1)) if match else 0


def next_major_from_source(source_doc_path: str | Path, requirement_name: str) -> int:
    raw = str(source_doc_path or "").strip()
    if not raw:
        return 0
    parsed = parse_doc_filename(Path(raw).name)
    if not parsed:
        return 0
    source_requirement = parsed.get("requirement_name") or ""
    if requirement_name and source_requirement and source_requirement != sanitize_component(requirement_name):
        return 0
    return version_major(parsed.get("version", "")) + 1


def max_requirement_major(directory: Path, requirement_name: str) -> int:
    safe_req = sanitize_component(requirement_name, fallback="需求文档")
    max_major = 0
    if not directory.exists():
        return max_major
    pattern = re.compile(
        rf"^(Deepseek|Claude|Codex)_{re.escape(safe_req)}_V(\d+)\.0\.docx$",
        re.I,
    )
    for path in directory.glob("*.docx"):
        match = pattern.match(path.name)
        if match:
            max_major = max(max_major, int(match.group(2)))
    return max_major


def build_cli_optimization_instruction(
    source_doc_path: str | Path,
    save_dir: str | Path,
    target_model: str,
    requirement_name: str,
) -> str:
    directory = resolve_save_dir(save_dir)
    target_model = normalize_model_name(target_model)
    if target_model == "Deepseek":
        target_model = "Claude"
    requirement_name = normalize_space(requirement_name) or parse_doc_filename(Path(source_doc_path).name).get("requirement_name", "需求文档")
    version = next_requirement_version(directory, target_model, requirement_name, source_doc_path)
    target_filename = f"{sanitize_component(target_model)}_{sanitize_component(requirement_name)}_{version}.docx"
    target_path = directory / target_filename
    return f"""请读取下面这份业务需求 Word 文档，并在同一目录下生成优化后的业务需求文档。

源文档：
{Path(source_doc_path).expanduser()}

输出目录：
{directory}

目标文件名：
{target_filename}

完整输出路径：
{target_path}

硬性要求：
1. 输出必须是 .docx 文件，不要只输出 Markdown 或纯文本。
2. 文件命名必须遵循：模型名称_需求名称_版本编号.docx。
3. 本次模型名称必须使用：{target_model}。
4. 需求名称必须保持为：{requirement_name}。
5. 版本编号必须使用：{version}，即基于源文档版本向后递增一版。
6. 必须保持与源文档相同的章节结构和标题层级，包括：文档信息、解析出的功能点、需求要素选项、需求概览、功能需求清单、验收标准、模型协作与优化指令、共识与人工确认、版本记录等。
7. 可以优化业务表述、补充遗漏规则、补充验收条件、指出风险，但不要删除源文档中的核心诉求。
8. 在“共识与人工确认”部分写明你的确认结论、仍有分歧的问题、建议我人工决策的事项。
9. 生成完成后，请只返回新文件的完整路径和关键优化摘要。"""


def read_docx_paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path, "r") as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = []
    for para in root.findall(".//w:p", ns):
        texts = [node.text or "" for node in para.findall(".//w:t", ns)]
        text = normalize_space("".join(texts))
        if text:
            paragraphs.append(text)
    return paragraphs


def extract_between_text(paragraphs: list[str], start_label: str, end_label: str) -> str:
    lines = extract_between(paragraphs, start_label, end_label)
    return "\n".join(line for line in lines if line not in {start_label, end_label}).strip()


def extract_points_between(paragraphs: list[str], start_label: str, end_label: str) -> list[str]:
    lines = extract_between(paragraphs, start_label, end_label)
    points = []
    skip = {start_label, end_label, "编号", "功能点", "优先级", "说明", "P1", "由原始描述自动解析，待后续确认细化。"}
    for line in lines:
        clean = re.sub(r"^F\d{2}\s*", "", line).strip()
        if not clean or clean in skip:
            continue
        if clean.startswith("F") and len(clean) <= 4:
            continue
        if 2 <= len(clean) <= 120:
            points.append(clean)
    return points


def extract_selected_section_ids(paragraphs: list[str]) -> list[str]:
    label_to_id = {item["label"]: item["id"] for item in SECTION_OPTIONS}
    option_lines = extract_between(paragraphs, "需求要素选项", "需求概览")
    selected: list[str] = []
    for index, line in enumerate(option_lines):
        if line != "☑":
            continue
        label = option_lines[index + 1] if index + 1 < len(option_lines) else ""
        section_id = label_to_id.get(label)
        if section_id:
            selected.append(section_id)
    return selected


def extract_between(paragraphs: list[str], start_label: str, end_label: str) -> list[str]:
    started = False
    result = []
    for line in paragraphs:
        if line == start_label:
            started = True
            continue
        if started and line == end_label:
            break
        if started:
            result.append(line)
    return result


def sanitize_component(text: str, fallback: str = "需求文档") -> str:
    clean = normalize_space(text)
    clean = re.sub(r'[\\/:*?"<>|]+', "", clean)
    clean = re.sub(r"\s+", "_", clean).strip("._-")
    return (clean or fallback)[:48]


def normalize_space(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", str(text or "")).strip()


def split_nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def build_document_xml(
    model_name: str,
    requirement_name: str,
    version: str,
    description: str,
    feature_points: list[str],
    selected_sections: list[str],
    author: str,
    created_at: str,
    output_path: str = "",
    save_dir: str = "",
    peer_versions: dict | None = None,
    source_doc_path: str = "",
    source_doc_text: str = "",
    iteration_notes: str = "",
) -> str:
    selected_set = set(selected_sections)
    peer_versions = peer_versions or {}
    body: list[str] = []
    title = f"{requirement_name} 需求文档"
    body.append(paragraph(title, style="Title"))
    body.append(paragraph(f"{model_name} · {version} · {created_at}", style="Subtitle"))
    body.append(paragraph("本文档属于 Deepseek 初稿、Claude/Codex CLI 优化、人工确认的协作迭代链路。所有优化版必须放在同一目录，使用相同命名格式和相同文档结构。", style="Callout"))

    body.append(paragraph("文档信息", style="Heading1"))
    body.append(table([
        ("需求名称", requirement_name),
        ("生成模型", model_name),
        ("版本编号", version),
        ("创建时间", created_at),
        ("作者", author or "程钰"),
        ("协作阶段", collaboration_stage(model_name)),
        ("当前文件路径", output_path or "生成后由系统确定"),
        ("来源文档路径", source_doc_path or "原始描述生成"),
        ("文件命名规则", "模型名称_需求名称_版本编号.docx"),
    ], widths=(2600, 6400)))

    body.append(paragraph("解析出的功能点", style="Heading1"))
    if feature_points:
        for point in feature_points:
            body.append(list_paragraph(point, num_id=1))
    else:
        body.append(paragraph("待补充。"))

    body.append(paragraph("需求要素选项", style="Heading1"))
    option_rows = [("是否纳入", "要素", "说明")]
    for item in SECTION_OPTIONS:
        option_rows.append(("☑" if item["id"] in selected_set else "☐", item["label"], item["description"]))
    body.append(table(option_rows, widths=(1300, 2600, 5100), header=True))

    for section in SECTION_OPTIONS:
        if section["id"] not in selected_set:
            continue
        body.extend(section_xml(
            section["id"],
            description,
            feature_points,
            model_name=model_name,
            requirement_name=requirement_name,
            version=version,
            output_path=output_path,
            save_dir=save_dir,
            peer_versions=peer_versions,
            source_doc_path=source_doc_path,
            source_doc_text=source_doc_text,
            iteration_notes=iteration_notes,
        ))

    body.append(paragraph("后续优化说明", style="Heading1"))
    body.append(paragraph("将 Claude/Codex 优化版放回同一目录后，可在本功能中解析、人工合并并生成下一轮，直到 Deepseek、Claude、Codex 和本人确认一致。"))

    return document_wrapper("".join(body))


def section_xml(
    section_id: str,
    description: str,
    feature_points: list[str],
    model_name: str = "",
    requirement_name: str = "",
    version: str = "",
    output_path: str = "",
    save_dir: str = "",
    peer_versions: dict | None = None,
    source_doc_path: str = "",
    source_doc_text: str = "",
    iteration_notes: str = "",
) -> list[str]:
    title = next((item["label"] for item in SECTION_OPTIONS if item["id"] == section_id), section_id)
    peer_versions = peer_versions or {}
    body = [page_break()] if section_id == "consensus" else []
    body.append(paragraph(title, style="Heading1"))
    if section_id == "overview":
        body.append(paragraph("原始描述", style="Heading2"))
        body.append(paragraph(description or "待补充。"))
        if source_doc_path:
            body.append(paragraph("来源文档", style="Heading2"))
            body.append(paragraph(source_doc_path))
        if source_doc_text:
            body.append(paragraph("导入文档摘要", style="Heading2"))
            body.append(paragraph(source_doc_text[:1800]))
        if iteration_notes:
            body.append(paragraph("人工修订说明", style="Heading2"))
            body.append(paragraph(iteration_notes))
        body.append(paragraph("目标说明", style="Heading2"))
        body.append(paragraph("围绕上述描述，将零散想法沉淀为结构化业务需求。Deepseek 负责初稿业务化表达；Claude 与 Codex 分别基于 CLI 阅读本文档后输出优化版；本人负责取舍、合并与最终确认。"))
    elif section_id == "stakeholders":
        body.append(table([
            ("角色", "使用目标", "关键关注点"),
            ("需求提出人", "描述业务诉求并确认范围", "表达清楚、版本可追踪"),
            ("需求分析助手", "解析功能点并生成标准文档", "结构完整、命名规范"),
            ("后续优化模型", "阅读原文档并输出优化版本", "上下文充分、可迭代"),
        ], widths=(2200, 3600, 3200), header=True))
    elif section_id == "scope":
        body.append(paragraph("本期范围", style="Heading2"))
        for point in feature_points[:8]:
            body.append(list_paragraph(point, num_id=1))
        body.append(paragraph("不做范围", style="Heading2"))
        body.append(list_paragraph("不直接替代人工最终确认，生成结果仍需人工审阅。", num_id=1))
    elif section_id == "workflow":
        for step in (
            "录入原始需求描述，由 Deepseek 生成初始业务需求文档。",
            "将 Deepseek 文档路径交给 Claude CLI，要求 Claude 在同目录生成 Claude_需求名称_版本编号.docx。",
            "将 Deepseek 或 Claude 文档路径交给 Codex CLI，要求 Codex 在同目录生成 Codex_需求名称_版本编号.docx。",
            "在本功能中解析 Claude/Codex 新生成的 Word 文档，回填功能点和需求内容。",
            "本人手工修改、补充、合并分歧，再生成下一轮 Deepseek/Claude/Codex 文档。",
            "循环迭代，直到 Deepseek、Claude、Codex 和本人都确认业务需求一致。",
        ):
            body.append(list_paragraph(step, num_id=2))
    elif section_id == "features":
        rows = [("编号", "功能点", "优先级", "说明")]
        for idx, point in enumerate(feature_points, start=1):
            rows.append((f"F{idx:02d}", point, "P1", "由原始描述自动解析，待后续确认细化。"))
        body.append(table(rows, widths=(1000, 4400, 1200, 2400), header=True))
    elif section_id == "data":
        body.append(table([
            ("数据对象", "字段/内容", "来源", "备注"),
            ("需求元数据", "模型名称、需求名称、版本号、创建时间", "用户输入/系统生成", "用于文件命名和版本追踪"),
            ("功能点", "功能点名称、说明、优先级", "原始描述解析", "可在后续版本中调整"),
            ("文档文件", "文件名、完整路径、版本", "本地目录", "支持展示和复制"),
        ], widths=(2200, 3000, 1800, 2000), header=True))
    elif section_id == "rules":
        for rule in (
            "文件命名规则为：模型名称_需求名称_版本编号.docx。",
            "模型名称限定为 Deepseek、Claude、Codex。",
            "没有来源文档时，按同一需求名称在目录中的最大版本号继续递增。",
            "有来源文档时，优先按来源文档版本号向后递增一版；若目标文件已存在，再继续递增。",
            "初始版本为 V1.0，后续协作轮次按 V2.0、V3.0 递增。",
            "Claude/Codex CLI 输出的优化版必须与源文档保持相同章节结构，便于本工具解析回表单继续迭代。",
            "优化版不得删除原始核心诉求；对分歧、风险和建议必须写入共识与人工确认部分。",
        ):
            body.append(list_paragraph(rule, num_id=1))
    elif section_id == "interaction":
        for item in ("目录输入与打开目录按钮", "模型选择", "需求名称输入", "原始描述输入", "要素选项勾选", "功能点解析结果展示", "文档列表路径复制"):
            body.append(list_paragraph(item, num_id=1))
    elif section_id == "nonfunctional":
        body.append(table([
            ("类别", "要求"),
            ("可用性", "缺省目录不存在时自动创建。"),
            ("可维护性", "文档生成逻辑与页面/API 解耦。"),
            ("兼容性", "输出为标准 docx，可被 Word、WPS、Claude、Codex 等读取。"),
            ("安全性", "仅操作用户指定的本地目录，不上传文档内容。"),
        ], widths=(2200, 6800), header=True))
    elif section_id == "acceptance":
        checks = (
            "输入描述后可以解析出需求功能点。",
            "可以选择 Deepseek、Claude、Codex 作为文档模型名称。",
            "可以在指定目录生成规范命名的 Word 文档。",
            "有来源文档时，新版本号按来源版本向后递增。",
            "页面可以展示文档路径并复制。",
            "页面按钮可以打开文件存放目录。",
        )
        for item in checks:
            body.append(list_paragraph(item, num_id=1))
    elif section_id == "risks":
        for item in ("原始描述过短时，自动解析的功能点可能需要人工补充。", "需求名称变更会被视为新的版本序列。", "若目标目录无写入权限，文档生成会失败。"):
            body.append(list_paragraph(item, num_id=1))
    elif section_id == "collaboration":
        body.append(paragraph("Claude/Codex CLI 输出约束", style="Heading2"))
        for item in (
            "必须读取当前 Word 文档作为唯一源文档，不要脱离上下文重新编造需求。",
            "必须在同一目录下输出 .docx 文件。",
            "必须使用相同命名格式：模型名称_需求名称_版本编号.docx。",
            "必须保持相同章节结构和标题层级，方便本工具解析回表单。",
            "必须补充优化建议、风险、验收标准和仍需人工判断的问题。",
        ):
            body.append(list_paragraph(item, num_id=1))
        body.append(paragraph("建议输出文件", style="Heading2"))
        rows = [("目标模型", "建议版本", "建议文件名")]
        for target in ("Claude", "Codex"):
            target_version = peer_versions.get(target, "V1.0")
            rows.append((target, target_version, f"{target}_{sanitize_component(requirement_name)}_{target_version}.docx"))
        body.append(table(rows, widths=(1800, 1800, 5400), header=True))
        body.append(paragraph("CLI 提示词模板", style="Heading2"))
        body.append(paragraph(build_cli_optimization_instruction(
            source_doc_path=output_path or source_doc_path or "请替换为当前文档完整路径",
            save_dir=save_dir or DEFAULT_REQUIREMENT_DOC_DIR,
            target_model="Claude",
            requirement_name=requirement_name,
        )))
    elif section_id == "consensus":
        body.append(table([
            ("参与方", "确认状态", "意见摘要", "待人工决策"),
            ("Deepseek", "待确认", "初稿结构化与业务化表达", "是否准确覆盖原始诉求"),
            ("Claude", "待确认", "待 CLI 输出优化建议", "是否采纳 Claude 的补充"),
            ("Codex", "待确认", "待 CLI 输出优化建议", "是否采纳 Codex 的补充"),
            ("本人", "待确认", "最终业务判断与取舍", "确认最终版本是否可进入开发"),
        ], widths=(1600, 1600, 3500, 2300), header=True))
    elif section_id == "versions":
        body.append(table([
            ("版本", "生成模型", "变更说明", "时间"),
            (version or "V1.0", model_name or "Deepseek", collaboration_stage(model_name), "当前版本"),
            ("后续版本", "Claude / Codex / Deepseek", "CLI 优化、人工合并、再生成", "再次生成时递增"),
        ], widths=(1400, 2400, 3600, 1600), header=True))
    return body


def collaboration_stage(model_name: str) -> str:
    model = normalize_model_name(model_name)
    if model == "Deepseek":
        return "业务需求初稿 / 人工合并后再生成"
    if model == "Claude":
        return "Claude CLI 优化版"
    if model == "Codex":
        return "Codex CLI 优化版"
    return "协作迭代版本"


def document_wrapper(body_xml: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {body_xml}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>'''


def paragraph(text: str, style: str = "Normal") -> str:
    text = xml_escape(text)
    style_xml = f"<w:pStyle w:val=\"{style}\"/>" if style != "Normal" else ""
    return f'''<w:p><w:pPr>{style_xml}</w:pPr><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'''


def list_paragraph(text: str, num_id: int = 1) -> str:
    text = xml_escape(text)
    return f'''<w:p>
  <w:pPr><w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num_id}"/></w:numPr></w:pPr>
  <w:r><w:t xml:space="preserve">{text}</w:t></w:r>
</w:p>'''


def page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def table(rows: list[tuple], widths: tuple[int, ...], header: bool = False) -> str:
    grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
    body = []
    for row_index, row in enumerate(rows):
        cells = []
        for index, value in enumerate(row):
            width = widths[min(index, len(widths) - 1)]
            fill = '<w:shd w:fill="EAF2F8"/>' if header and row_index == 0 else ""
            bold = '<w:b/>' if header and row_index == 0 else ""
            cells.append(f'''<w:tc>
  <w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{fill}<w:vAlign w:val="center"/></w:tcPr>
  <w:p><w:r><w:rPr>{bold}</w:rPr><w:t xml:space="preserve">{xml_escape(str(value))}</w:t></w:r></w:p>
</w:tc>''')
        body.append(f"<w:tr>{''.join(cells)}</w:tr>")
    return f'''<w:tbl>
  <w:tblPr>
    <w:tblW w:w="{sum(widths)}" w:type="dxa"/>
    <w:tblBorders>
      <w:top w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>
      <w:left w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>
      <w:bottom w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>
      <w:right w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>
      <w:insideH w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>
      <w:insideV w:val="single" w:sz="4" w:space="0" w:color="D9E2EC"/>
    </w:tblBorders>
    <w:tblCellMar>
      <w:top w:w="120" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>
      <w:bottom w:w="120" w:type="dxa"/><w:right w:w="120" w:type="dxa"/>
    </w:tblCellMar>
  </w:tblPr>
  <w:tblGrid>{grid}</w:tblGrid>
  {''.join(body)}
</w:tbl>'''


def xml_escape(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def write_docx_package(path: Path, document_xml: str, title: str, author: str, created_at: str) -> None:
    created_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml())
        zf.writestr("_rels/.rels", package_rels_xml())
        zf.writestr("docProps/core.xml", core_props_xml(title, author, created_iso))
        zf.writestr("docProps/app.xml", app_props_xml())
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", styles_xml())
        zf.writestr("word/numbering.xml", numbering_xml())
        zf.writestr("word/_rels/document.xml.rels", document_rels_xml())


def content_types_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''


def package_rels_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''


def document_rels_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
</Relationships>'''


def core_props_xml(title: str, author: str, created_iso: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:dcmitype="http://purl.org/dc/dcmitype/"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{xml_escape(title)}</dc:title>
  <dc:creator>{xml_escape(author or "程钰")}</dc:creator>
  <cp:lastModifiedBy>{xml_escape(author or "程钰")}</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created_iso}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created_iso}</dcterms:modified>
</cp:coreProperties>'''


def app_props_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
  xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>程钰的百宝箱</Application>
</Properties>'''


def styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:eastAsia="PingFang SC" w:hAnsi="Arial"/><w:sz w:val="22"/><w:color w:val="1F2937"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:before="0" w:after="180"/></w:pPr>
    <w:rPr><w:b/><w:rFonts w:ascii="Arial" w:eastAsia="PingFang SC" w:hAnsi="Arial"/><w:sz w:val="40"/><w:color w:val="0F766E"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:after="220"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:eastAsia="PingFang SC" w:hAnsi="Arial"/><w:sz w:val="20"/><w:color w:val="64748B"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="Heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:before="260" w:after="120"/><w:keepNext/></w:pPr>
    <w:rPr><w:b/><w:rFonts w:ascii="Arial" w:eastAsia="PingFang SC" w:hAnsi="Arial"/><w:sz w:val="30"/><w:color w:val="111827"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="Heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:before="160" w:after="80"/><w:keepNext/></w:pPr>
    <w:rPr><w:b/><w:rFonts w:ascii="Arial" w:eastAsia="PingFang SC" w:hAnsi="Arial"/><w:sz w:val="24"/><w:color w:val="0F766E"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Callout">
    <w:name w:val="Callout"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:spacing w:before="60" w:after="160"/><w:ind w:left="180"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Arial" w:eastAsia="PingFang SC" w:hAnsi="Arial"/><w:sz w:val="21"/><w:color w:val="334155"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph">
    <w:name w:val="List Paragraph"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="720" w:hanging="360"/><w:spacing w:after="80"/></w:pPr>
  </w:style>
</w:styles>'''


def numbering_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="1">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="2">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num>
</w:numbering>'''
