from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Iterator, Optional, Union

from .config import WechatAssistantConfig, timestamp_slug
from .llm import LLMClient, LLMResponse
from .markdown_wechat import (
    markdown_to_wechat_html,
    markdown_to_wechat_html_publish,
    build_image_block_html,
)


# ── 文章生成模式配置 ──────────────────────────────────────────────────────────
_ARTICLE_MODES: dict[str, dict] = {
    "minimal": {
        "label": "极简",
        "word_count": "300字以内",
        "max_tokens": 900,
        "requirements": (
            "1. 全文严格控制在 300 字以内，字数是硬约束，不得超过。\n"
            "2. 只保留最核心的一个观点，直接切入，不要铺垫。\n"
            "3. 段落极短（1-2句），行文精炼，每个字都有分量，绝对不拖沓。\n"
            "4. 使用 Markdown，一级标题即文章标题，正文可以不加二级标题。\n"
            "5. 结尾一句话点睛，不要鸡汤式口号。\n"
            "6. 不要编造数据、新闻、名人原话。"
        ),
    },
    "standard": {
        "label": "标准",
        "word_count": "600字以内",
        "max_tokens": 1600,
        "requirements": (
            "1. 全文控制在 600 字以内。\n"
            "2. 使用 Markdown，包含一级标题和 2-3 个二级小标题。\n"
            "3. 每个小标题下的论述清晰，有理有据，面面俱到但不冗余。\n"
            "4. 开头快速切入话题，结尾给出明确收束。\n"
            "5. 必须尊重用户补充素材，不偏离核心观点。\n"
            "6. 不要编造数据、新闻、名人原话。"
        ),
    },
    "rich": {
        "label": "丰富",
        "word_count": "1500字以内",
        "max_tokens": 4200,
        "requirements": (
            "1. 正文长度约 1000-1500 字。\n"
            "2. 使用 Markdown，包含一级标题、二级小标题、正文段落。\n"
            "3. 主体至少包含 3 个分论点，每个分论点有解释、佐证和可感知的场景或案例。\n"
            "4. 开头要能快速进入问题，不要寒暄；一级标题优先使用备选标题。\n"
            "5. 结尾给出明确收束，不要用口号式鸡汤。\n"
            "6. 必须尊重用户补充素材，不偏离用户核心观点。\n"
            "7. 不要编造无法确认的具体数据、新闻、名人原话。"
        ),
    },
    "novel": {
        "label": "小说",
        "word_count": "不限字数",
        "max_tokens": 6000,
        "requirements": (
            "1. 字数不设上限，写够为止，文章要有完整的起承转合。\n"
            "2. 使用散文或小说的风格：叙事流畅，情感丰富，有画面感和文学性。\n"
            "3. 可以用第一人称、故事开头、场景描写等文学手法带入话题。\n"
            "4. 使用 Markdown 输出，标题自然融入叙事，不要机械地列小标题。\n"
            "5. 保持核心观点贯穿全文，但以故事或情感为载体，而非论说文形式。\n"
            "6. 结尾要有余韵，不要生硬地总结。\n"
            "7. 不要编造真实的新闻事件或真实人物的具体言论。"
        ),
    },
}

_DEFAULT_MODE = "rich"


def _mode_cfg(mode: str) -> dict:
    return _ARTICLE_MODES.get(mode) or _ARTICLE_MODES[_DEFAULT_MODE]


SYSTEM_PROMPT = """你是一个资深微信公众号内容助手，擅长把作者的核心观点转化为适合公众号发布的内容。
你的写作风格要求：
1. 观点清晰，有判断，不空泛堆砌。
2. 结构完整，有开头钩子、论证主体、案例或场景、结尾升华。
3. 适合中文微信公众号阅读，段落短，节奏清楚。
4. 不编造具体数据、人物言论或新闻事实；无法确认时用概括表达。
5. 输出遵循用户要求的格式。"""


@dataclass
class AssistantResult:
    data: dict
    output_files: list[str]
    provider: Optional[str] = None
    model: Optional[str] = None


class WechatContentAssistant:
    def __init__(self, config: WechatAssistantConfig):
        self.config = config
        self.client = LLMClient(config.provider, config.provider_config)
        self.temperature = float(config.generation.get("temperature") or 0.72)

    def generate_topics(self, keyword: str, audience: str = "", tone: str = "") -> AssistantResult:
        keyword = keyword.strip()
        if not keyword:
            raise ValueError("请输入关键词或核心观点")
        prompt = f"""请基于以下关键词/核心观点生成微信公众号选题。

关键词/核心观点：
{keyword}

目标读者：
{audience or self.config.generation.get("default_audience")}

内容气质：
{tone or self.config.generation.get("default_tone")}

请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
[
  {{
    "topic": "选题名称",
    "reason": "为什么值得写",
    "titles": ["备选标题1", "备选标题2", "备选标题3"]
  }}
]

要求：
1. 输出 5 个选题。
2. 每个选题必须有 3 个备选标题。
3. 标题适合公众号，但不要夸大承诺，不要标题党。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=int(self.config.generation.get("topic_max_tokens") or 1800),
            temperature=self.temperature,
        )
        topics = parse_json_list(resp.text)
        payload = {"keyword": keyword, "topics": topics}
        path = self._save_json(keyword, "topics", payload)
        return self._result(payload, [path], resp)

    def write_article(
        self,
        topic: str,
        core_viewpoint: str = "",
        content_notes: str = "",
        title: str = "",
        audience: str = "",
        tone: str = "",
        mode: str = "",
    ) -> AssistantResult:
        topic = topic.strip()
        core_viewpoint = core_viewpoint.strip()
        content_notes = content_notes.strip()
        title = title.strip()
        if not topic and not core_viewpoint:
            raise ValueError("请输入选题或核心观点")
        cfg = _mode_cfg(mode)
        prompt = _build_article_prompt(
            topic=topic, core_viewpoint=core_viewpoint, content_notes=content_notes,
            title=title, audience=audience or self.config.generation.get("default_audience"),
            tone=tone or self.config.generation.get("default_tone"),
            word_count=cfg["word_count"], requirements=cfg["requirements"],
        )
        resp = self.client.generate(
            system=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=cfg["max_tokens"],
            temperature=self.temperature,
        )
        markdown = resp.text.strip()
        path = self._save_text(topic or core_viewpoint, "article", markdown, ".md")
        return self._result({"markdown": markdown}, [path], resp)

    def optimize_titles(self, markdown: str) -> AssistantResult:
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("请输入正文内容")
        prompt = f"""请基于以下微信公众号正文，生成 10 个备选标题。

正文：
{markdown[:8000]}

请严格输出 JSON 数组，不要输出 Markdown 代码块。数组元素为字符串。
要求：
1. 10 个标题角度要有区分。
2. 标题要有吸引力，但不能夸张、恐吓或虚假承诺。
3. 每个标题不超过 28 个中文字符。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=int(self.config.generation.get("title_max_tokens") or 1200),
            temperature=self.temperature,
        )
        titles = parse_title_list(resp.text)
        payload = {"titles": titles}
        path = self._save_json(titles[0] if titles else "titles", "titles", payload)
        return self._result(payload, [path], resp)

    def generate_titles_for_topic(
        self,
        topic: str,
        core_viewpoint: str = "",
        content_notes: str = "",
        audience: str = "",
        tone: str = "",
    ) -> AssistantResult:
        topic = topic.strip()
        core_viewpoint = core_viewpoint.strip()
        content_notes = content_notes.strip()
        if not topic and not core_viewpoint:
            raise ValueError("请先选择选题或输入核心观点")
        prompt = f"""请基于以下微信公众号选题生成 10 个备选标题，并对每个标题评分。

选题：
{topic or core_viewpoint}

作者核心观点：
{core_viewpoint or topic}

用户补充素材/想写内容：
{content_notes or "无"}

目标读者：
{audience or self.config.generation.get("default_audience")}

内容气质：
{tone or self.config.generation.get("default_tone")}

请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
[
  {{
    "title": "标题文字",
    "score": 8,
    "type": "观点型",
    "reason": "一句话说明传播优势或适合场景"
  }}
]

要求：
1. 10 个标题角度要有区分，覆盖观点型、问题型、场景型、反常识型、数字型。
2. 标题要适合公众号传播，但不要夸张、恐吓或虚假承诺。
3. 每个标题不超过 28 个中文字符。
4. 标题必须贴合选题，不要偏离作者核心观点。
5. score 为 1-10 的整数，综合评估传播力（是否让人想点）、准确性（是否贴合内容）、新颖性（是否与众不同）。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=int(self.config.generation.get("title_max_tokens") or 2000),
            temperature=self.temperature,
        )
        try:
            raw = parse_json_value(resp.text)
        except (json.JSONDecodeError, ValueError):
            raw = []
        # Unwrap {"titles": [...]} or {"data": [...]} wrapper if present
        if isinstance(raw, dict):
            for key in ("titles", "data", "items"):
                if isinstance(raw.get(key), list):
                    raw = raw[key]
                    break
        if not isinstance(raw, list):
            raw = []
        titles_with_scores: list[dict] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                titles_with_scores.append({"title": item.strip(), "score": 0, "type": "", "reason": ""})
            elif isinstance(item, dict) and item.get("title"):
                titles_with_scores.append({
                    "title": str(item["title"]).strip(),
                    "score": int(item.get("score") or 0),
                    "type": str(item.get("type") or ""),
                    "reason": str(item.get("reason") or ""),
                })
        # Fallback: regex extraction from any format if JSON parse failed
        if not titles_with_scores:
            for m in re.finditer(r'"title"\s*:\s*"([^"]{4,60})"', resp.text):
                t = m.group(1).strip()
                if t:
                    titles_with_scores.append({"title": t, "score": 0, "type": "", "reason": ""})
        if not titles_with_scores:
            for line in resp.text.splitlines():
                clean = re.sub(r'^\s*(?:\d+[.)]\s*|[-*]\s*)', '', line).strip().strip('"').strip()
                if 4 <= len(clean) <= 40 and not clean.startswith('{') and not clean.startswith('['):
                    titles_with_scores.append({"title": clean, "score": 0, "type": "", "reason": ""})
        titles = [t["title"] for t in titles_with_scores]
        payload = {"topic": topic or core_viewpoint, "titles": titles, "titles_scored": titles_with_scores}
        path = self._save_json(topic or core_viewpoint or "titles", "topic_titles", payload)
        return self._result(payload, [path], resp)

    def generate_excerpt(
        self,
        markdown: str,
        title: str = "",
        core_viewpoint: str = "",
    ) -> AssistantResult:
        """Generate a WeChat article excerpt (≤120 chars)."""
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("请先生成正文")
        prompt = f"""请为以下微信公众号正文生成一段摘要（用于文章列表展示）。

文章标题：
{title or "（未填写）"}

作者核心观点：
{core_viewpoint or "（未填写）"}

正文（前1500字）：
{markdown[:1500]}

要求：
1. 摘要严格不超过 120 个中文字符（含标点）。
2. 要能勾起读者点进去看全文的兴趣，但不要剧透核心结论。
3. 语言自然流畅，像是正文的开篇引语或场景带入，而非机械总结。
4. 只输出摘要文字，不要加任何前缀标签、引号或说明。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=300, temperature=0.75,
        )
        excerpt = resp.text.strip().strip('""\'\'「」').strip()[:200]
        # Fallback: extract first non-heading paragraph from markdown
        if not excerpt:
            for line in markdown.splitlines():
                clean = line.strip()
                if clean and not clean.startswith('#') and not clean.startswith('---'):
                    excerpt = clean[:120]
                    break
        payload = {"excerpt": excerpt, "char_count": len(excerpt)}
        path = self._save_text(title or "excerpt", "excerpt", excerpt, ".txt")
        return self._result(payload, [path], resp)

    def rewrite_paragraph(
        self,
        paragraph: str,
        instruction: str = "",
        topic: str = "",
        tone: str = "",
    ) -> AssistantResult:
        """Rewrite a single paragraph according to user instruction."""
        paragraph = paragraph.strip()
        if not paragraph:
            raise ValueError("请输入要重写的段落")
        hint = instruction.strip() or "使语言更简练有力，保持原意"
        prompt = f"""请根据以下要求，重写这段微信公众号正文段落。

文章选题/上下文：
{topic or "（未填写）"}

重写要求：
{hint}

原段落：
{paragraph}

要求：
1. 只输出重写后的段落，不要任何前缀、说明或引号。
2. 保持与原文同样的 Markdown 格式（如是 ## 标题则输出 ## 标题）。
3. 不要改变段落的核心意思，只改变表达方式。
4. 内容气质：{tone or "客观理性，有温度"}。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=800, temperature=0.72,
        )
        rewritten = resp.text.strip()
        payload = {"original": paragraph, "rewritten": rewritten, "instruction": hint}
        return self._result(payload, [], resp)

    def generate_cover_image_idea(
        self,
        topic: str,
        core_viewpoint: str = "",
        tone: str = "",
    ) -> AssistantResult:
        """Generate 2 cover image concepts for WeChat 9:5 cover ratio."""
        topic = topic.strip()
        if not topic and not core_viewpoint:
            raise ValueError("请先选择选题")
        prompt = f"""请为微信公众号文章生成 2 个封面图方案（比例 9:5，用作文章列表封面）。

选题：
{topic or core_viewpoint}

作者核心观点：
{core_viewpoint or topic}

内容气质：
{tone or "客观理性，有温度"}

请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
[
  {{
    "name": "封面图名称",
    "scene": "画面构图描述（横构图，留空白给标题文字叠加）",
    "style": "视觉风格",
    "caption": "",
    "prompt": "用于 ComfyUI/SDXL 文生图的英文提示词",
    "reason": "为什么适合作封面"
  }}
]

要求：
1. 封面图是横向构图（9:5），视觉重心偏左或居中，右侧留空白可叠加标题文字。
2. 画面简洁有力，一眼能传达文章情绪或主题，不要信息密度过高。
3. 不要出现版权人物、真实品牌标识、文字、logo 或水印。
4. prompt 必须是英文，适合 SDXL；注明 wide banner, 9:5 aspect ratio, editorial photography style。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=1000, temperature=self.temperature,
        )
        images = parse_image_ideas(resp.text)
        payload = {"topic": topic or core_viewpoint, "cover_images": images}
        path = self._save_json(topic or core_viewpoint or "cover", "cover_ideas", payload)
        return self._result(payload, [path], resp)

    def generate_image_ideas(
        self,
        topic: str,
        core_viewpoint: str = "",
        content_notes: str = "",
        markdown: str = "",
        audience: str = "",
        tone: str = "",
    ) -> AssistantResult:
        topic = topic.strip()
        core_viewpoint = core_viewpoint.strip()
        content_notes = content_notes.strip()
        markdown = markdown.strip()
        if not topic and not core_viewpoint and not markdown:
            raise ValueError("请先选择选题或生成正文")
        prompt = f"""请为微信公众号文章生成 4 个可选配图方案。

选题：
{topic or core_viewpoint}

作者核心观点：
{core_viewpoint or topic}

用户补充素材/想写内容：
{content_notes or "无"}

正文摘要：
{markdown[:4000] if markdown else "暂未生成正文，请基于选题和核心观点构思。"}

目标读者：
{audience or self.config.generation.get("default_audience")}

内容气质：
{tone or self.config.generation.get("default_tone")}

请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
[
  {{
    "name": "配图名称",
    "scene": "画面内容",
    "style": "视觉风格",
    "caption": "可选图片说明",
    "prompt": "用于 ComfyUI/SDXL 文生图的英文提示词",
    "reason": "为什么适合这篇文章"
  }}
]

要求：
1. 输出 4 个方案，彼此视觉角度明显不同。
2. 不要出现版权人物、真实品牌标识、具体公司 logo。
3. 适合公众号首图或文中配图，画面清晰，不要抽象到看不懂。
4. prompt 必须使用英文，适合 SDXL 文生图；不要要求生成文字、标题、logo 或水印。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=int(self.config.generation.get("image_max_tokens") or 1600),
            temperature=self.temperature,
        )
        images = parse_image_ideas(resp.text)
        payload = {"topic": topic or core_viewpoint, "images": images}
        path = self._save_json(topic or core_viewpoint or "images", "image_ideas", payload)
        return self._result(payload, [path], resp)

    def write_article_stream(
        self,
        topic: str,
        core_viewpoint: str = "",
        content_notes: str = "",
        title: str = "",
        audience: str = "",
        tone: str = "",
        mode: str = "",
    ) -> Iterator[str]:
        """Stream article text chunks, then yield a final JSON sentinel with file path."""
        topic = topic.strip()
        core_viewpoint = core_viewpoint.strip()
        content_notes = content_notes.strip()
        title = title.strip()
        if not topic and not core_viewpoint:
            raise ValueError("请输入选题或核心观点")
        cfg = _mode_cfg(mode)
        prompt = _build_article_prompt(
            topic=topic, core_viewpoint=core_viewpoint, content_notes=content_notes,
            title=title, audience=audience or self.config.generation.get("default_audience"),
            tone=tone or self.config.generation.get("default_tone"),
            word_count=cfg["word_count"], requirements=cfg["requirements"],
        )
        chunks: list[str] = []
        for chunk in self.client.generate_stream(
            system=SYSTEM_PROMPT,
            prompt=prompt,
            max_tokens=cfg["max_tokens"],
            temperature=self.temperature,
        ):
            chunks.append(chunk)
            yield chunk

        markdown = "".join(chunks).strip()
        path = self._save_text(topic or core_viewpoint, "article", markdown, ".md")
        yield "\x00" + json.dumps({"output_files": [path]})

    def convert_markdown(self, markdown: str, title_hint: str = "wechat") -> AssistantResult:
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("请输入 Markdown 正文")
        html = markdown_to_wechat_html(markdown)
        path = self._save_text(title_hint, "wechat_html", html, ".html")
        return AssistantResult(data={"html": html}, output_files=[path])

    def format_publish_text(
        self,
        title: str,
        markdown: str,
        image: Optional[dict] = None,
        image_position: str = "after_intro",
    ) -> AssistantResult:
        title = title.strip()
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("请先生成 Markdown 正文")
        text = markdown_to_wechat_text(markdown, title=title)
        image_block = format_image_block(image or {})
        if image_block:
            text = insert_image_block(text, image_block, image_position)
        payload = {
            "text": text,
            "image_position": image_position,
            "image": image or {},
        }
        path = self._save_text(title or "wechat_publish", "publish_text", text, ".txt")
        return AssistantResult(data=payload, output_files=[path], provider=self.client.provider, model=self.client.model)

    def format_publish_html(
        self,
        title: str,
        markdown: str,
        image: Optional[dict] = None,
        image_position: str = "after_intro",
    ) -> AssistantResult:
        title = title.strip()
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("请先生成 Markdown 正文")
        image_block = build_image_block_html(image or {})
        html_content = markdown_to_wechat_html_publish(
            markdown, title=title, image_block_html=image_block, image_position=image_position
        )
        payload = {
            "html": html_content,
            "image_position": image_position,
            "image": image or {},
        }
        path = self._save_text(title or "wechat_publish", "publish_html", html_content, ".html")
        return AssistantResult(data=payload, output_files=[path])

    def diagnose_article(self, markdown: str, core_viewpoint: str = "") -> dict:
        """Rule-based article quality check, no LLM call needed."""
        if not markdown.strip():
            return {"ok": False, "error": "文章为空"}
        chinese_chars = len(re.findall(r"[一-鿿]", markdown))
        headings = re.findall(r"^#{1,4}\s+.+$", markdown, re.M)
        paragraphs = [p for p in markdown.split("\n\n") if p.strip() and not p.strip().startswith("#")]
        viewpoint_words = [w for w in re.split(r"[，。！？\s、,]+", core_viewpoint) if len(w) > 1] if core_viewpoint else []
        matched = [w for w in viewpoint_words if w in markdown]
        coverage = len(matched) / len(viewpoint_words) if viewpoint_words else 1.0
        numbers_count = len(re.findall(r"\d+(?:\.\d+)?%|\d+亿|\d+万|\d{4}年", markdown))

        issues = []
        if chinese_chars < 800:
            issues.append({"level": "warn", "msg": f"字数较少（{chinese_chars}字），建议不低于1500字"})
        elif chinese_chars > 3500:
            issues.append({"level": "warn", "msg": f"字数较多（{chinese_chars}字），阅读完成率可能偏低"})
        else:
            issues.append({"level": "ok", "msg": f"字数合适：{chinese_chars}字"})

        if len(headings) < 2:
            issues.append({"level": "warn", "msg": "小标题偏少，建议增加分节结构提升可读性"})
        else:
            issues.append({"level": "ok", "msg": f"结构完整：{len(headings)}个标题，{len(paragraphs)}段正文"})

        if viewpoint_words:
            if coverage < 0.5:
                issues.append({"level": "warn", "msg": f"核心观点关键词覆盖率{coverage:.0%}，文章可能偏题"})
            else:
                issues.append({"level": "ok", "msg": f"核心观点覆盖率{coverage:.0%}"})

        if numbers_count > 6:
            issues.append({"level": "warn", "msg": f"含{numbers_count}处具体数据，请核实数据来源避免失实"})

        return {
            "ok": True,
            "word_count": chinese_chars,
            "heading_count": len(headings),
            "paragraph_count": len(paragraphs),
            "viewpoint_coverage": round(coverage * 100),
            "issues": issues,
        }

    def generate_full(
        self,
        core_viewpoint: str,
        keyword: str = "",
        audience: str = "",
        tone: str = "",
    ) -> AssistantResult:
        seed = (keyword or core_viewpoint).strip()
        topics_result = self.generate_topics(seed, audience=audience, tone=tone)
        topics = topics_result.data.get("topics") or []
        selected_topic = topics[0].get("topic") if topics and isinstance(topics[0], dict) else seed
        article_result = self.write_article(
            selected_topic,
            core_viewpoint=core_viewpoint,
            audience=audience,
            tone=tone,
        )
        markdown = article_result.data["markdown"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            titles_future = pool.submit(self.optimize_titles, markdown)
            html_future = pool.submit(self.convert_markdown, markdown, selected_topic)
            titles_result = titles_future.result()
            html_result = html_future.result()
        payload = {
            "core_viewpoint": core_viewpoint,
            "keyword": seed,
            "selected_topic": selected_topic,
            "topics": topics,
            "markdown": markdown,
            "titles": titles_result.data.get("titles", []),
            "html": html_result.data["html"],
        }
        manifest = self._save_json(selected_topic, "full", payload)
        files = topics_result.output_files + article_result.output_files + titles_result.output_files + html_result.output_files + [manifest]
        return AssistantResult(
            data=payload,
            output_files=files,
            provider=article_result.provider,
            model=article_result.model,
        )

    def generate_hooks(
        self,
        topic: str,
        core_viewpoint: str = "",
        article_opening: str = "",
        tone: str = "",
    ) -> AssistantResult:
        """Generate 4 hook options for article opening (different styles)."""
        topic = topic.strip()
        if not topic and not core_viewpoint:
            raise ValueError("请先选择话题")
        context = f"文章当前开篇：\n{article_opening[:300]}\n\n" if article_opening.strip() else ""
        prompt = f"""请为以下微信公众号文章生成 4 种不同风格的开篇钩子（Hook），每种控制在 80 字以内。

选题：{topic or core_viewpoint}
核心观点：{core_viewpoint or topic}
内容气质：{tone or "客观理性，有温度"}
{context}
请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
[
  {{
    "type": "冲突型",
    "hook": "开篇文字（纯文字，不含 Markdown 格式）",
    "reason": "一句话说明这种开篇的效果"
  }}
]

4 种风格必须覆盖：
1. 冲突型：制造认知冲突或反常识，让读者觉得"这和我想的不一样"
2. 场景型：用具体生活场景带入，让读者感同身受
3. 问题型：抛出一个读者会自问的真实问题，激发代入感
4. 数据/事实型：用一个令人意外的数字或事实开场，建立权威感

要求：每种 hook 直接切入，第一句话就要有吸引力，严禁"近年来…""随着…""在当今…"等无效铺垫。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=800, temperature=0.82,
        )
        hooks = parse_json_list(resp.text)
        payload = {"topic": topic or core_viewpoint, "hooks": hooks}
        return self._result(payload, [], resp)

    def extract_quotes(
        self,
        markdown: str,
        topic: str = "",
    ) -> AssistantResult:
        """Extract 3-5 shareable quotes from article."""
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("请先生成正文")
        prompt = f"""请从以下微信公众号正文中，提炼 4-5 句最具传播力的金句。

选题：{topic or "（未填写）"}

正文：
{markdown[:4000]}

请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
[
  {{
    "quote": "金句原文（直接从正文中摘取或轻度润色，不超过 50 字）",
    "why": "为什么这句话有传播力（一句话）"
  }}
]

筛选标准：
1. 有观点，有判断，让人读完想截图发出去
2. 脱离上下文也能独立成立，不需要前因后果
3. 语言精炼，节奏感强，不是流水账
4. 优先选择有对比、有反转、有画面感的句子
5. 避免"我们应该…""要…才能…"这类空泛说教"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=600, temperature=0.7,
        )
        quotes = parse_json_list(resp.text)
        payload = {"topic": topic, "quotes": quotes}
        path = self._save_json(topic or "quotes", "quotes", payload)
        return self._result(payload, [path], resp)

    def generate_engagement_ending(
        self,
        topic: str,
        core_viewpoint: str = "",
        markdown: str = "",
        audience: str = "",
        tone: str = "",
    ) -> AssistantResult:
        """Generate 3 engagement-optimised article endings."""
        topic = topic.strip()
        if not topic and not core_viewpoint:
            raise ValueError("请先选择话题")
        prompt = f"""请为以下微信公众号文章生成 3 种结尾，每种专门设计互动引导，控制在 100 字以内。

选题：{topic or core_viewpoint}
核心观点：{core_viewpoint or topic}
目标读者：{audience or "普通微信用户"}
内容气质：{tone or "客观理性，有温度"}
正文结尾段（参考）：
{markdown[-500:] if markdown.strip() else "（暂未生成）"}

请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
[
  {{
    "type": "留言引导型",
    "ending": "结尾文字",
    "goal": "这种结尾的目标行为（留言/转发/在看）"
  }}
]

3 种结尾必须覆盖：
1. 留言引导型：提一个开放性问题，让读者有话说，并说明会在留言区回复精选
2. 转发引导型：说明这篇文章对哪类人有价值，引导读者转发给那类人
3. 在看+收藏型：给出一个"以后用得上"的理由，引导收藏和点在看

要求：互动引导要自然，不要命令式语气；结尾必须与文章内容有关联，不能是通用模板。"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=700, temperature=0.78,
        )
        endings = parse_json_list(resp.text)
        payload = {"topic": topic or core_viewpoint, "endings": endings}
        return self._result(payload, [], resp)

    def plan_content_series(
        self,
        topic: str,
        core_viewpoint: str = "",
        audience: str = "",
        tone: str = "",
        num_episodes: int = 5,
    ) -> AssistantResult:
        """Plan a multi-episode content series around the current topic."""
        topic = topic.strip()
        if not topic and not core_viewpoint:
            raise ValueError("请先选择话题")
        prompt = f"""请基于以下选题，规划一个微信公众号系列文章方案（共 {num_episodes} 篇）。

系列主题：{topic or core_viewpoint}
核心观点方向：{core_viewpoint or topic}
目标读者：{audience or "普通微信用户"}
内容气质：{tone or "客观理性，有温度"}

请严格输出 JSON，不要输出 Markdown 代码块。JSON 结构如下：
{{
  "series_name": "系列名称（如：xxx系列）",
  "series_hook": "系列整体钩子（一句话说明读完这个系列能获得什么）",
  "episodes": [
    {{
      "episode": 1,
      "title": "建议标题",
      "angle": "这一篇的独特角度",
      "core_point": "这一篇要表达的核心观点",
      "content_type": "干货型/故事型/观点型/互动型",
      "publish_timing": "建议在系列中的发布顺序和理由"
    }}
  ]
}}

要求：
1. {num_episodes} 篇各有侧重，不重复，内容类型搭配合理（干货/故事/观点/互动均衡）
2. 第1篇要最能打开读者兴趣（最强钩子），最后1篇要有收束感或留下期待
3. 每篇相互关联但独立成立，不读前篇也能看懂
4. 角度要有梯度，从浅入深或从宏观到具体"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=2000, temperature=0.75,
        )
        raw = parse_json_value(resp.text)
        if isinstance(raw, list):
            raw = {"series_name": topic, "series_hook": "", "episodes": raw}
        elif not isinstance(raw, dict):
            raw = {"series_name": topic, "series_hook": "", "episodes": []}
        payload = {"topic": topic or core_viewpoint, "plan": raw}
        path = self._save_json(topic or core_viewpoint or "series", "series_plan", payload)
        return self._result(payload, [path], resp)

    def check_account_positioning(
        self,
        article_topic: str,
        article_summary: str,
        account_positioning: str,
    ) -> dict:
        """Rule-based positioning consistency check (no LLM needed for basic check)."""
        if not account_positioning.strip():
            return {"ok": True, "skipped": True, "message": "未设置账号定位，跳过检查"}
        prompt = f"""请判断以下公众号文章是否符合该账号的定位。

账号定位：
{account_positioning}

本篇文章话题：{article_topic}
本篇内容摘要：{article_summary or article_topic}

请严格输出 JSON：
{{
  "consistent": true/false,
  "score": 1-10,
  "verdict": "一句话判断结论",
  "risk": "如果偏离，说明偏离风险；如果吻合，说明吻合理由",
  "suggestion": "如有偏离，给出让文章更贴合定位的建议（不超过50字）"
}}"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt, max_tokens=400, temperature=0.3,
        )
        result = parse_json_value(resp.text)
        if not isinstance(result, dict):
            result = {"consistent": True, "score": 5, "verdict": "无法解析结果", "risk": "", "suggestion": ""}
        return {"ok": True, "check": result}

    # ── multi-platform formatting ────────────────────────────────────────────

    def format_for_platform(
        self,
        markdown: str,
        title: str = "",
        platform: str = "zhihu",
        core_viewpoint: str = "",
    ) -> AssistantResult:
        """Reformat article content for different publishing platforms."""
        markdown = markdown.strip()
        if not markdown:
            raise ValueError("请先生成正文")
        platforms = {
            "zhihu": {
                "name": "知乎",
                "instructions": (
                    "1. 保留长篇论述结构，适当补充专业术语和逻辑论证。\n"
                    "2. 标题用[问题式]或[观点式]重写（如[为什么...?][...的本质是什么]）。\n"
                    "3. 正文保持 Markdown，段落可以更长，论证要更严密有据。\n"
                    "4. 在文末加[作者结语]和1-2条适合知乎的互动话语。\n"
                    "5. 去除微信专属的表达（如[点在看][转发给...]），改为知乎风格的收尾。\n"
                    "6. 输出为 Markdown 格式。"
                ),
                "max_tokens": 3500,
            },
            "xiaohongshu": {
                "name": "小红书",
                "instructions": (
                    "1. 标题改为小红书风格：加 emoji、口语化、突出[你能获得什么]（控制在20字内）。\n"
                    "2. 正文每段最多2句话，段与段之间空行，使用大量换行制造呼吸感。\n"
                    "3. 每个核心观点前加相关 emoji（如✅❌💡🔥⚡️📌🎯）。\n"
                    "4. 语气要第一人称、亲切、有温度，像在和朋友聊天。\n"
                    "5. 正文末尾换行后列出 5-8 个话题标签，格式：#话题名称。\n"
                    "6. 控制在 400-600 字以内，纯文本输出（不用 Markdown 符号）。"
                ),
                "max_tokens": 1200,
            },
            "weibo": {
                "name": "微博",
                "instructions": (
                    "1. 将核心观点压缩为 140-200 字以内的精华版本。\n"
                    "2. 第一句话就是最强论点，不需要铺垫，直接输出观点。\n"
                    "3. 可以用「」引用文章中最打动人的一句金句。\n"
                    "4. 末尾加 1-2 个话题标签（#话题#格式）和1个问题引导互动。\n"
                    "5. 语气简洁有力，可以适当犀利，适合微博传播调性。\n"
                    "6. 纯文本输出，不用 Markdown 符号。"
                ),
                "max_tokens": 500,
            },
            "bilibili": {
                "name": "哔哩哔哩",
                "instructions": (
                    "1. 将文章改写为视频脚本大纲，包含：开场白（15秒）→ 分节讲解 → 结尾 CTA。\n"
                    "2. 口语化改写，每句话控制在一口气能说完的长度（约15字以内）。\n"
                    "3. 开场白要有强钩子：先说结论或制造悬念，让观众想看完。\n"
                    "4. 每个分节加【分节标题】，便于剪辑分段。\n"
                    "5. 结尾加：总结精华（1句话）+ 引导三连（点赞收藏关注）的话术。\n"
                    "6. 同时输出：视频标题（含关键词，控制在20字内）、简介（100字）、标签（5个）。\n"
                    "7. 用 Markdown 格式输出脚本结构。"
                ),
                "max_tokens": 2500,
            },
        }
        cfg = platforms.get(platform) or platforms["zhihu"]
        prompt = f"""请将以下微信公众号文章改写为适合【{cfg['name']}】发布的格式。

原文标题：{title or "（未填写）"}
核心观点：{core_viewpoint or "（未填写）"}

原文正文（Markdown）：
{markdown[:5000]}

改写要求：
{cfg['instructions']}"""
        resp = self.client.generate(
            system=SYSTEM_PROMPT, prompt=prompt,
            max_tokens=cfg["max_tokens"], temperature=0.72,
        )
        content = resp.text.strip()
        platform_name = cfg["name"]
        payload = {"platform": platform, "platform_name": platform_name, "content": content}
        path = self._save_text(title or platform, f"platform_{platform}", content, ".txt")
        return self._result(payload, [path], resp)

    # ── compliance check ─────────────────────────────────────────────────────

    _COMPLIANCE_RULES: list[tuple[str, str, str]] = [
        # (pattern, risk_type, suggestion)
        (r"最好|最快|第一|全网唯一|业界领先|史上最|独家|无可替代", "夸大宣传",
         "此类绝对化表述易被认定为虚假宣传，建议改为有数据支撑的相对表述"),
        (r"治疗|治愈|包治|特效|根治|药到病除", "医疗声明",
         "未经资质认证不得作医疗效果声明，建议改为[可能有助于]等中性表述"),
        (r"保证盈利|稳赚|零风险|高回报|躺赚|轻松月入", "金融承诺",
         "金融类内容不得承诺收益，建议加风险提示或删除此类表述"),
        (r"点击领取|免费送|扫码抽奖|转发抽奖|限时免费", "诱导行为",
         "此类引导动作在微信平台可能触发违规检测，建议改为自然引导"),
        (r"内部资料|绝密|泄露|不对外公开|仅限内部", "信息安全",
         "此类措辞可能造成误导或引发版权/保密问题，建议删除或改写"),
        (r"\d+%以上|\d{4,}万?元|\d+倍增长", "数据来源",
         "具体数字若无来源说明，可能被质疑为捏造数据，建议注明来源"),
    ]

    def check_compliance(self, markdown: str, title: str = "", deep: bool = False) -> dict:
        """Hybrid compliance check: rule-based (fast) + optional LLM (deep)."""
        text = (title + "\n" + markdown).strip()
        rule_hits: list[dict] = []
        for pattern, risk_type, suggestion in self._COMPLIANCE_RULES:
            matches = re.findall(pattern, text)
            if matches:
                rule_hits.append({
                    "type": risk_type,
                    "matched": list(set(matches))[:5],
                    "suggestion": suggestion,
                    "level": "warn",
                })
        result: dict = {
            "ok": True,
            "rule_hits": rule_hits,
            "rule_pass": len(rule_hits) == 0,
            "total_issues": len(rule_hits),
        }
        if deep:
            prompt = f"""请对以下微信公众号文章进行深度合规审查，找出可能引发平台限流或违规的隐性风险。

标题：{title or "（未填写）"}
正文：
{markdown[:3000]}

请严格输出 JSON：
{{
  "risks": [
    {{
      "type": "风险类型",
      "content": "引用有问题的原文片段（不超过30字）",
      "reason": "为什么有风险",
      "suggestion": "修改建议（不超过30字）",
      "level": "high/medium/low"
    }}
  ],
  "overall": "总体评估（一句话）",
  "publishable": true/false
}}

重点关注：未标注来源的统计数据、隐性金融承诺、可能引发争议的绝对化观点、
诱导分享/关注的文案、可能侵权的引用内容。如无风险，risks 输出空数组。"""
            try:
                resp = self.client.generate(
                    system=SYSTEM_PROMPT, prompt=prompt, max_tokens=800, temperature=0.2,
                )
                llm_result = parse_json_value(resp.text)
                if isinstance(llm_result, dict):
                    result["llm_risks"] = llm_result.get("risks") or []
                    result["overall"] = llm_result.get("overall") or ""
                    result["publishable"] = llm_result.get("publishable", True)
                    result["total_issues"] += len(result["llm_risks"])
            except Exception:
                result["llm_risks"] = []
        return result

    # ── materials library ────────────────────────────────────────────────────

    @property
    def _materials_path(self) -> Path:
        return self.config.output_dir.parent / "materials_library.json"

    def _load_materials(self) -> list[dict]:
        try:
            return json.loads(self._materials_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_materials(self, materials: list[dict]) -> None:
        self._materials_path.write_text(
            json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list_materials(self, category: str = "", query: str = "") -> list[dict]:
        mats = self._load_materials()
        if category:
            mats = [m for m in mats if m.get("category") == category]
        if query:
            q = query.lower()
            mats = [m for m in mats if q in m.get("title", "").lower() or q in m.get("content", "").lower()]
        return mats

    def add_material(self, title: str, content: str, category: str = "通用", tags: list[str] | None = None) -> dict:
        import uuid, datetime
        if not title.strip() or not content.strip():
            raise ValueError("标题和内容不能为空")
        material = {
            "id": str(uuid.uuid4())[:8],
            "title": title.strip(),
            "content": content.strip(),
            "category": category.strip() or "通用",
            "tags": [t.strip() for t in (tags or []) if t.strip()],
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        mats = self._load_materials()
        mats.insert(0, material)
        self._save_materials(mats)
        return material

    def delete_material(self, material_id: str) -> bool:
        mats = self._load_materials()
        before = len(mats)
        mats = [m for m in mats if m.get("id") != material_id]
        if len(mats) < before:
            self._save_materials(mats)
            return True
        return False

    # ── knowledge extraction ─────────────────────────────────────────────────

    def extract_knowledge(self, markdown: str, title: str = "", topic: str = "") -> dict:
        """Extract structured knowledge points from article for saving to materials library."""
        if not markdown.strip():
            raise ValueError("文章内容不能为空")
        client = self._llm_client()
        system = (
            "你是一位专业的内容知识管理专家，擅长从文章中提炼有价值的知识点。"
            "提炼的知识点要简洁、独立、可复用，方便未来写作时直接引用。"
        )
        title_hint = f"文章标题：{title}\n" if title else ""
        topic_hint = f"核心话题：{topic}\n" if topic else ""
        prompt = f"""{title_hint}{topic_hint}
请从以下文章中提炼 5-10 个有价值的知识点，每个知识点必须：
1. 独立成立（不依赖上下文即可理解）
2. 有明确的类型：观点 / 数据 / 案例 / 名言 / 行业洞察
3. 内容简洁（50-150字）
4. 附上 2-3 个标签便于检索

以 JSON 格式返回，格式如下：
{{
  "knowledge_points": [
    {{
      "type": "观点",
      "title": "知识点标题（15字内）",
      "content": "知识点正文（50-150字）",
      "tags": ["标签1", "标签2"],
      "source_hint": "来自文章哪个部分的简短说明"
    }}
  ],
  "summary": "一句话概括文章核心价值（30字内）"
}}

类型映射到素材库分类：观点→观点，数据→数据，案例→案例，名言→名言，行业洞察→观点

文章内容：
---
{markdown[:3000]}
---"""
        resp = client.generate(system, prompt, max_tokens=2000, temperature=0.4)
        text = resp.text.strip()
        import re as _re
        m = _re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError("模型返回格式解析失败")
        data = json.loads(m.group())
        points = data.get("knowledge_points") or []
        # Map type to materials category
        _type_to_cat = {"观点": "观点", "数据": "数据", "案例": "案例", "名言": "名言", "行业洞察": "观点"}
        for p in points:
            p["category"] = _type_to_cat.get(p.get("type", ""), "通用")
        return {"knowledge_points": points, "summary": data.get("summary", ""), "total": len(points)}

    # ── topic lifecycle analysis ─────────────────────────────────────────────

    def analyze_topic_lifecycle(self, topic: str, source_count: int = 1) -> dict:
        """Classify topic as event-driven / trend / evergreen and give publish urgency."""
        topic = topic.strip()
        if not topic:
            return {"ok": False, "error": "话题为空"}
        prompt = f"""请判断以下话题的热度生命周期，给出写作时效性建议。

话题：{topic}
当前热搜来源平台数：{source_count}（1=单平台，3=全平台爆发）

请严格输出 JSON：
{{
  "lifecycle": "事件型/趋势型/常青型",
  "urgency": "立即写（24小时内）/尽快写（3天内）/从容写（1-2周）/随时可写",
  "heat_duration": "预计热度持续时长（如 1-3天/1-2周/长期）",
  "reason": "判断依据（不超过50字）",
  "best_angle": "结合时效性推荐的最佳写作角度（不超过40字）",
  "risk": "追这个话题的潜在风险（如有）"
}}

判断标准：
- 事件型：由特定新闻/事件引发，热度3天内衰退（如突发新闻、明星八卦）
- 趋势型：反映某种社会/行业趋势，热度持续数周（如政策变化、行业报告）
- 常青型：普世话题，无时效压力，随时可写（如个人成长、职场技巧）"""
        try:
            resp = self.client.generate(
                system=SYSTEM_PROMPT, prompt=prompt, max_tokens=400, temperature=0.3,
            )
            result = parse_json_value(resp.text)
            if not isinstance(result, dict):
                result = {}
        except Exception:
            result = {}
        return {"ok": True, "topic": topic, "analysis": result}

    def _save_text(self, seed: str, suffix: str, content: str, ext: str) -> str:
        path = self.config.output_dir / f"{timestamp_slug(seed, suffix)}{ext}"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def _save_json(self, seed: str, suffix: str, content: Union[dict, list]) -> str:
        path = self.config.output_dir / f"{timestamp_slug(seed, suffix)}.json"
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def _result(self, data: dict, files: list[str], resp: LLMResponse) -> AssistantResult:
        return AssistantResult(data=data, output_files=files, provider=resp.provider, model=resp.model)


def _build_article_prompt(
    topic: str,
    core_viewpoint: str,
    content_notes: str,
    title: str,
    audience: str,
    tone: str,
    word_count: str,
    requirements: str,
) -> str:
    return f"""请写一篇微信公众号正文，使用 Markdown 输出。

选题：
{topic or core_viewpoint}

备选标题：
{title or "暂未确定，请基于正文自然拟定主标题"}

作者核心观点：
{core_viewpoint or topic}

用户补充素材/想写内容：
{content_notes or "无"}

目标读者：
{audience or "普通微信用户"}

内容气质：
{tone or "客观理性，有温度"}

字数目标：{word_count}

要求：
{requirements}"""


def parse_json_list(text: str) -> list[dict]:
    parsed = parse_json_value(text)
    if isinstance(parsed, dict):
        for key in ("topics", "data", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise ValueError("模型没有返回可解析的 JSON 列表")
    normalized = []
    for item in parsed[:5]:
        if not isinstance(item, dict):
            continue
        titles = item.get("titles") or item.get("备选标题") or []
        if isinstance(titles, str):
            titles = [titles]
        normalized.append({
            "topic": str(item.get("topic") or item.get("选题") or "").strip(),
            "reason": str(item.get("reason") or item.get("理由") or "").strip(),
            "titles": [str(x).strip() for x in titles[:3] if str(x).strip()],
        })
    return normalized


def parse_title_list(text: str) -> list[str]:
    try:
        parsed = parse_json_value(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        for key in ("titles", "data", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if isinstance(parsed, list):
        return [str(x).strip() for x in parsed[:10] if str(x).strip()]

    lines = []
    for line in text.splitlines():
        clean = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        clean = clean.strip('"“”')
        if clean:
            lines.append(clean)
    return lines[:10]

def parse_image_ideas(text: str) -> list[dict]:
    parsed = parse_json_value(text)
    if isinstance(parsed, dict):
        for key in ("images", "data", "items"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        raise ValueError("模型没有返回可解析的配图方案")
    normalized = []
    for item in parsed[:4]:
        if not isinstance(item, dict):
            continue
        scene = str(item.get("scene") or item.get("画面内容") or "").strip()
        style = str(item.get("style") or item.get("视觉风格") or "").strip()
        caption = str(item.get("caption") or item.get("图片说明") or "").strip()
        prompt = str(item.get("prompt") or item.get("提示词") or "").strip()
        if not prompt:
            prompt_parts = [part for part in (scene, style, caption) if part]
            prompt = "，".join(prompt_parts)
        normalized.append({
            "name": str(item.get("name") or item.get("名称") or "配图方案").strip(),
            "scene": scene,
            "style": style,
            "caption": caption,
            "prompt": prompt,
            "reason": str(item.get("reason") or item.get("理由") or "").strip(),
        })
    return normalized


def _sanitize_json_str(text: str) -> str:
    """Replace smart/curly quotes inside JSON string values with escaped ASCII quotes."""
    # Replace Chinese/typographic curly quotes with straight double-quote, escaped for JSON
    result = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_string:
            result.append(ch)
            i += 1
            if i < len(text):
                result.append(text[i])
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
        elif ch in '“”‘’' and in_string:
            # curly quotes inside a JSON string value → replace with escaped quote
            result.append('\\"')
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


def parse_json_value(text: str):
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1).strip()
    for candidate in (stripped, _sanitize_json_str(stripped)):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            start = min([idx for idx in (candidate.find("["), candidate.find("{")) if idx >= 0], default=-1)
            end = max(candidate.rfind("]"), candidate.rfind("}"))
            if start >= 0 and end > start:
                try:
                    return json.loads(candidate[start:end + 1])
                except json.JSONDecodeError:
                    pass
    raise json.JSONDecodeError("无法解析模型返回的 JSON", stripped, 0)


def markdown_to_wechat_text(markdown: str, title: str = "") -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    title_written = False

    if title:
        out.extend([title.strip(), ""])
        title_written = True

    for raw in lines:
        line = raw.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        if heading:
            text = clean_inline_markdown(heading.group(2))
            if len(heading.group(1)) == 1:
                if not title_written:
                    out.extend([text, ""])
                    title_written = True
                continue
            out.extend([f"▌{text}", ""])
            continue
        quote = re.match(r"^>\s*(.+)$", line)
        if quote:
            out.append(f"「{clean_inline_markdown(quote.group(1))}」")
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            out.append(f"· {clean_inline_markdown(bullet.group(1))}")
            continue
        ordered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if ordered:
            out.append(clean_inline_markdown(line))
            continue
        out.append(clean_inline_markdown(line))

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def clean_inline_markdown(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1（\2）", text)
    return text.strip()


def format_image_block(image: dict) -> str:
    if not image:
        return ""
    name = str(image.get("name") or "配图").strip()
    caption = str(image.get("caption") or "").strip()
    prompt = str(image.get("prompt") or "").strip()
    scene = str(image.get("scene") or "").strip()
    image_path = str(image.get("image_path") or "").strip()
    image_url = str(image.get("image_url") or "").strip()
    lines = [f"【配图：{name}】"]
    if image_path:
        lines.append(f"图片文件：{image_path}")
    elif image_url:
        lines.append(f"图片地址：{image_url}")
    if caption:
        lines.append(caption)
    elif scene:
        lines.append(scene)
    if prompt:
        lines.append(f"配图提示词：{prompt}")
    return "\n".join(lines).strip()


def insert_image_block(text: str, image_block: str, position: str) -> str:
    if not image_block:
        return text
    paragraphs = text.split("\n\n")
    if position == "cover":
        return image_block + "\n\n" + text
    if position == "before_end" and len(paragraphs) > 1:
        return "\n\n".join(paragraphs[:-1] + [image_block, paragraphs[-1]])
    if position == "after_first_heading":
        for idx, para in enumerate(paragraphs):
            if para.startswith("▌"):
                return "\n\n".join(paragraphs[:idx + 1] + [image_block] + paragraphs[idx + 1:])
    insert_at = 2 if len(paragraphs) > 2 else 1
    return "\n\n".join(paragraphs[:insert_at] + [image_block] + paragraphs[insert_at:])
