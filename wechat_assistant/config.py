from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import copy
import re
from typing import Optional

import yaml


@dataclass(frozen=True)
class WechatAssistantConfig:
    root_dir: Path
    provider: str
    providers: dict
    provider_config: dict
    generation: dict
    output_dir: Path


def load_config(
    root_dir: Path,
    config_path: Optional[Path] = None,
    provider_override: str = "",
    provider_overrides: Optional[dict] = None,
) -> WechatAssistantConfig:
    path = config_path or root_dir / "config" / "wechat_assistant.yaml"
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    provider = str(provider_override or raw.get("provider") or "deepseek")
    providers = copy.deepcopy(raw.get("providers") or {})
    provider_config = providers.get(provider)
    if not isinstance(provider_config, dict):
        raise ValueError(f"未配置公众号内容助手 provider：{provider}")
    if provider_overrides:
        for key, value in provider_overrides.items():
            if value not in (None, ""):
                provider_config[key] = value

    output_dir = Path(raw.get("output_dir") or "output/wechat")
    if not output_dir.is_absolute():
        output_dir = root_dir / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    return WechatAssistantConfig(
        root_dir=root_dir,
        provider=provider,
        providers=providers,
        provider_config=provider_config,
        generation=raw.get("generation") or {},
        output_dir=output_dir,
    )


def timestamp_slug(text: str, suffix: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean = re.sub(r"\s+", "_", text.strip())
    clean = re.sub(r"[^\w\u4e00-\u9fff-]+", "", clean)
    clean = clean.strip("_-")[:32] or "wechat"
    return f"{stamp}_{clean}_{suffix}"
