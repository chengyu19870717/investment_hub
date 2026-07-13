from __future__ import annotations

import html
import re
from typing import Optional


def inline_markdown(text: str) -> str:
    escaped = html.escape(text.strip())
    escaped = re.sub(
        r"`([^`]+)`",
        lambda m: f'<code style="background:#f3f4f6;padding:2px 5px;border-radius:4px;font-size:90%;">{m.group(1)}</code>',
        escaped,
    )
    escaped = re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", lambda m: f"<em>{m.group(1)}</em>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        lambda m: f'<a href="{m.group(2)}" style="color:#2563eb;text-decoration:none;">{m.group(1)}</a>',
        escaped,
    )
    return escaped


def markdown_to_wechat_html(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    parts: list[str] = [
        '<section style="font-size:16px;line-height:1.85;color:#242933;letter-spacing:0.02em;">'
    ]
    list_stack: list[str] = []
    in_code = False
    code_lines: list[str] = []

    def close_lists() -> None:
        while list_stack:
            parts.append(f"</{list_stack.pop()}>")

    def close_code() -> None:
        nonlocal in_code, code_lines
        if not in_code:
            return
        code = html.escape("\n".join(code_lines))
        parts.append(
            '<pre style="white-space:pre-wrap;background:#111827;color:#f9fafb;'
            'padding:12px 14px;border-radius:8px;overflow:auto;font-size:13px;'
            f'line-height:1.65;">{code}</pre>'
        )
        in_code = False
        code_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                close_code()
            else:
                close_lists()
                in_code = True
                code_lines = []
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not stripped:
            close_lists()
            continue

        h_match = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if h_match:
            close_lists()
            level = len(h_match.group(1))
            text = inline_markdown(h_match.group(2))
            if level == 1:
                parts.append(
                    '<h1 style="font-size:24px;line-height:1.35;margin:18px 0 14px;'
                    f'font-weight:800;color:#111827;">{text}</h1>'
                )
            elif level == 2:
                parts.append(
                    '<h2 style="font-size:19px;line-height:1.45;margin:22px 0 12px;'
                    'padding-left:10px;border-left:4px solid #2563eb;'
                    f'font-weight:800;color:#111827;">{text}</h2>'
                )
            else:
                parts.append(
                    '<h3 style="font-size:17px;line-height:1.5;margin:18px 0 10px;'
                    f'font-weight:750;color:#1f2937;">{text}</h3>'
                )
            continue

        if stripped.startswith(">"):
            close_lists()
            text = inline_markdown(stripped.lstrip(">").strip())
            parts.append(
                '<blockquote style="margin:14px 0;padding:10px 12px;border-left:4px solid #94a3b8;'
                f'background:#f8fafc;color:#475569;border-radius:6px;">{text}</blockquote>'
            )
            continue

        bullet_match = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet_match:
            if not list_stack or list_stack[-1] != "ul":
                close_lists()
                list_stack.append("ul")
                parts.append('<ul style="margin:10px 0 14px;padding-left:22px;">')
            parts.append(f'<li style="margin:6px 0;">{inline_markdown(bullet_match.group(1))}</li>')
            continue

        ordered_match = re.match(r"^\d+[.)]\s+(.+)$", stripped)
        if ordered_match:
            if not list_stack or list_stack[-1] != "ol":
                close_lists()
                list_stack.append("ol")
                parts.append('<ol style="margin:10px 0 14px;padding-left:22px;">')
            parts.append(f'<li style="margin:6px 0;">{inline_markdown(ordered_match.group(1))}</li>')
            continue

        close_lists()
        parts.append(f'<p style="margin:12px 0;">{inline_markdown(stripped)}</p>')

    close_code()
    close_lists()
    parts.append("</section>")
    return "\n".join(parts)


# ── WeChat publish HTML (styled, for copy-paste into WeCom editor) ────────────

def markdown_to_wechat_html_publish(
    markdown: str,
    title: str = "",
    image_block_html: str = "",
    image_position: str = "after_intro",
) -> str:
    """
    Convert Markdown to WeChat-compatible HTML with inline styles.
    Designed for paste-into-WeChat-editor with formatting preserved.
    """
    lines = markdown.replace("\r\n", "\n").split("\n")

    # Group lines into logical blocks (separated by blank lines)
    raw_blocks: list[str] = []
    buf: list[str] = []
    for line in lines:
        if not line.strip():
            if buf:
                raw_blocks.append("\n".join(buf))
                buf = []
        else:
            buf.append(line)
    if buf:
        raw_blocks.append("\n".join(buf))

    html_parts: list[str] = []
    title_written = False

    if title:
        html_parts.append(_pub_h1(html.escape(title.strip())))
        title_written = True

    for block in raw_blocks:
        lines_in = block.split("\n")
        first = lines_in[0].strip()

        # Heading
        h = re.match(r"^(#{1,4})\s+(.+)$", first)
        if h and len(lines_in) == 1:
            level = len(h.group(1))
            text = _pub_inline(h.group(2))
            if level == 1:
                if not title_written:
                    html_parts.append(_pub_h1(text))
                    title_written = True
                # skip duplicate H1
                continue
            elif level == 2:
                html_parts.append(
                    f'<h2 style="font-size:18px;font-weight:bold;color:#0f766e;'
                    f'margin:20px 0 8px;padding-left:10px;border-left:4px solid #0f766e;'
                    f'line-height:1.4;">{text}</h2>'
                )
            else:
                html_parts.append(
                    f'<h3 style="font-size:16px;font-weight:bold;color:#1a1a1a;'
                    f'margin:14px 0 6px;line-height:1.4;">{text}</h3>'
                )
            continue

        # Horizontal rule ─ styled WeChat divider
        if re.match(r"^[-*_]{3,}$", first) and len(lines_in) == 1:
            html_parts.append(
                '<div style="text-align:center;margin:24px 0;">'
                '<span style="display:inline-block;width:40px;height:2px;background:#e2e8f0;vertical-align:middle;"></span>'
                '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#0f766e;vertical-align:middle;margin:0 8px;"></span>'
                '<span style="display:inline-block;width:40px;height:2px;background:#e2e8f0;vertical-align:middle;"></span>'
                '</div>'
            )
            continue

        # Blockquote ─ detect big quote card (>  **text** or > text)
        if first.startswith(">"):
            inner = re.sub(r"^>\s*", "", first)
            # If only bold text or short quote (≤40 chars), render as quote card
            is_quote_card = re.match(r"^\*\*[^*]{4,}\*\*$", inner.strip()) or (
                len(inner) <= 40 and not inner.startswith("[") and len(lines_in) == 1
            )
            text = _pub_inline(inner)
            if is_quote_card:
                html_parts.append(
                    f'<div style="margin:20px 0;padding:18px 20px;background:#f0fdfa;'
                    f'border-radius:8px;text-align:center;">'
                    f'<p style="font-size:18px;font-weight:bold;color:#0f766e;'
                    f'line-height:1.6;margin:0;letter-spacing:0.02em;">{text}</p>'
                    f'</div>'
                )
            else:
                html_parts.append(
                    f'<blockquote style="margin:14px 0;padding:10px 16px;'
                    f'border-left:4px solid #0f766e;background:#f0fdfa;color:#155e75;'
                    f'border-radius:0 4px 4px 0;font-size:15px;line-height:1.8;">{text}</blockquote>'
                )
            continue

        # Bullet list
        bullets = [re.match(r"^[-*]\s+(.+)$", l.strip()) for l in lines_in]
        if all(bullets):
            items = "".join(
                f'<li style="margin:4px 0;font-size:15px;line-height:1.8;color:#333333;">'
                f'{_pub_inline(m.group(1))}</li>'
                for m in bullets if m
            )
            html_parts.append(f'<ul style="padding-left:20px;margin:10px 0;">{items}</ul>')
            continue

        # Ordered list
        ordered = [re.match(r"^\d+[.)]\s+(.+)$", l.strip()) for l in lines_in]
        if all(ordered):
            items = "".join(
                f'<li style="margin:4px 0;font-size:15px;line-height:1.8;color:#333333;">'
                f'{_pub_inline(m.group(1))}</li>'
                for m in ordered if m
            )
            html_parts.append(f'<ol style="padding-left:20px;margin:10px 0;">{items}</ol>')
            continue

        # Paragraph
        text = _pub_inline(" ".join(l.strip() for l in lines_in if l.strip()))
        html_parts.append(
            f'<p style="font-size:15px;line-height:1.9;color:#333333;margin:10px 0;">{text}</p>'
        )

    # Insert image block at requested position
    if image_block_html:
        html_parts = _pub_insert_image(html_parts, image_block_html, image_position)

    wrapper = (
        'font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",'
        '"Microsoft YaHei",sans-serif;max-width:677px;margin:0 auto;padding:0 16px 24px;'
        'color:#333333;word-break:break-word;'
    )
    return f'<div style="{wrapper}">{"".join(html_parts)}</div>'


def build_image_block_html(image: Optional[dict]) -> str:
    """Build a <figure> HTML block for a WeChat article image."""
    if not image:
        return ""
    image_url = str(image.get("image_url") or image.get("unsplash_url") or "").strip()
    name = html.escape(str(image.get("name") or "配图").strip())
    caption = html.escape(str(image.get("caption") or image.get("scene") or "").strip())

    if image_url:
        safe_url = html.escape(image_url)
        cap_html = (
            f'<figcaption style="font-size:13px;color:#888888;margin-top:6px;text-align:center;">'
            f'{caption}</figcaption>'
            if caption else ""
        )
        return (
            f'<figure style="margin:20px 0;text-align:center;">'
            f'<img src="{safe_url}" alt="{caption or name}" '
            f'style="max-width:100%;border-radius:8px;display:block;margin:0 auto;"/>'
            f'{cap_html}</figure>'
        )
    else:
        cap_html = (
            f'<p style="font-size:12px;color:#94a3b8;margin:4px 0 0;">{caption}</p>'
            if caption else ""
        )
        return (
            f'<figure style="margin:20px 0;padding:20px 16px;background:#f8fafc;'
            f'border:2px dashed #cbd5e1;border-radius:8px;text-align:center;">'
            f'<p style="color:#64748b;font-size:13px;margin:0;">【此处插入配图：{name}】</p>'
            f'{cap_html}</figure>'
        )


def _pub_h1(text: str) -> str:
    return (
        f'<h1 style="font-size:22px;font-weight:bold;color:#1a1a1a;text-align:center;'
        f'margin:20px 0 16px;line-height:1.4;">{text}</h1>'
    )


def _pub_inline(text: str) -> str:
    escaped = html.escape(text.strip())
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"`([^`]+)`",
        r'<code style="background:#f1f5f9;padding:1px 4px;border-radius:3px;font-size:13px;">\1</code>',
        escaped,
    )
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2" style="color:#0f766e;text-decoration:underline;">\1</a>',
        escaped,
    )
    return escaped


def _pub_insert_image(parts: list[str], image_html: str, position: str) -> list[str]:
    if position == "cover":
        return [image_html] + parts
    if position == "before_end" and len(parts) > 1:
        return parts[:-1] + [image_html, parts[-1]]
    if position == "after_first_heading":
        for i, p in enumerate(parts):
            if p.startswith("<h"):
                return parts[: i + 1] + [image_html] + parts[i + 1 :]
    # after_intro: after second paragraph-level element
    para_indices = [i for i, p in enumerate(parts) if p.startswith("<p ")]
    insert_after = para_indices[1] if len(para_indices) > 1 else (para_indices[0] if para_indices else 0)
    return parts[: insert_after + 1] + [image_html] + parts[insert_after + 1 :]
