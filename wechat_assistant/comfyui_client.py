from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import random
import re
import time
import urllib.parse
import urllib.request
from uuid import uuid4


class ComfyUIError(Exception):
    pass


@dataclass(frozen=True)
class ComfyImageResult:
    image_path: str
    checkpoint: str
    seed: int
    width: int
    height: int
    prompt: str
    comfy_filename: str
    comfy_subfolder: str
    speed_lora: str = ""


DEFAULT_CHECKPOINT_ORDER = (
    "blue_pencil-XL-v7.0.0.safetensors",
    "CounterfeitXL_beta.safetensors",
    "animagine-xl-3.1.safetensors",
    "realisticvision-v6-sd15.safetensors",
    "pastelmix.safetensors",
)

# 少步数加速 LoRA（按优先级）：命中即启用 Lightning 模式
# （8步 euler/sgm_uniform/cfg1.0，cfg=1 时跳过负向分支，UNet 前向次数 16×2 → 8×1）
SPEED_LORA_CANDIDATES = (
    "sdxl_lightning_8step_lora.safetensors",
    "sdxl_lightning_4step_lora.safetensors",
    "Hyper-SDXL-8steps-lora.safetensors",
)

_SPEED_PRESETS = {
    "sdxl_lightning_8step_lora.safetensors": {"steps": 8, "cfg": 1.0},
    "sdxl_lightning_4step_lora.safetensors": {"steps": 4, "cfg": 1.0},
    "Hyper-SDXL-8steps-lora.safetensors": {"steps": 8, "cfg": 1.0},
}


def find_speed_lora(comfy_dir: Path) -> str:
    lora_dir = comfy_dir / "models" / "loras"
    for name in SPEED_LORA_CANDIDATES:
        if (lora_dir / name).is_file():
            return name
    return ""


def _is_sdxl_checkpoint(checkpoint: str) -> bool:
    return "xl" in checkpoint.lower()

DEFAULT_NEGATIVE_PROMPT = (
    "low quality, blurry, jpeg artifacts, watermark, signature, logo, text, "
    "bad anatomy, distorted hands, distorted face, extra fingers, cropped"
)


def list_checkpoints(comfy_dir: Path) -> list[str]:
    ckpt_dir = comfy_dir / "models" / "checkpoints"
    if not ckpt_dir.exists():
        return []
    return sorted(
        path.name
        for path in ckpt_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".safetensors", ".ckpt", ".pt"}
    )


def choose_checkpoint(comfy_dir: Path, preferred: str = "") -> str:
    checkpoints = list_checkpoints(comfy_dir)
    if not checkpoints:
        raise ComfyUIError("ComfyUI checkpoints 目录下没有可用模型")
    if preferred and preferred in checkpoints:
        return preferred
    for item in DEFAULT_CHECKPOINT_ORDER:
        if item in checkpoints:
            return item
    return checkpoints[0]


def generate_comfy_image(
    prompt: str,
    output_dir: Path,
    comfy_dir: Path,
    base_url: str = "http://127.0.0.1:8188",
    checkpoint: str = "",
    negative_prompt: str = "",
    width: int = 1024,
    height: int = 576,
    steps: int = 16,
    cfg: float = 6.5,
    sampler_name: str = "dpmpp_2m",
    scheduler: str = "karras",
    timeout_seconds: int = 360,
    speed: str = "auto",
) -> ComfyImageResult:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ComfyUIError("缺少 ComfyUI 图片提示词")

    checkpoint = choose_checkpoint(comfy_dir, checkpoint)
    width = _snap_dimension(width, default=1024)
    height = _snap_dimension(height, default=576)
    steps = max(1, min(int(steps or 16), 60))
    cfg = max(1.0, min(float(cfg or 6.5), 15.0))

    # Lightning 加速：SDXL 底模 + 加速 LoRA 在场时覆盖采样参数（speed=off 可关闭）
    speed_lora = ""
    if speed != "off" and _is_sdxl_checkpoint(checkpoint):
        speed_lora = find_speed_lora(comfy_dir)
        if speed_lora:
            preset = _SPEED_PRESETS[speed_lora]
            steps, cfg = preset["steps"], preset["cfg"]
            sampler_name, scheduler = "euler", "sgm_uniform"

    seed = random.randint(1, 2**63 - 1)
    positive = _enhance_prompt(prompt)
    negative = (negative_prompt or DEFAULT_NEGATIVE_PROMPT).strip()
    filename_prefix = "wechat_ops"

    workflow = _txt2img_workflow(
        checkpoint=checkpoint,
        positive=positive,
        negative=negative,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        sampler_name=sampler_name,
        scheduler=scheduler,
        seed=seed,
        filename_prefix=filename_prefix,
        speed_lora=speed_lora,
    )
    prompt_id = _queue_prompt(base_url, workflow)
    image_meta = _wait_for_image(base_url, prompt_id, timeout_seconds)
    image_bytes = _download_image(base_url, image_meta)

    image_dir = output_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(str(image_meta.get("filename") or "")).suffix or ".png"
    target = image_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:10]}{suffix}"
    target.write_bytes(image_bytes)

    return ComfyImageResult(
        image_path=str(target),
        checkpoint=checkpoint,
        seed=seed,
        width=width,
        height=height,
        prompt=positive,
        comfy_filename=str(image_meta.get("filename") or ""),
        comfy_subfolder=str(image_meta.get("subfolder") or ""),
        speed_lora=speed_lora,
    )


def _txt2img_workflow(
    checkpoint: str,
    positive: str,
    negative: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler_name: str,
    scheduler: str,
    seed: int,
    filename_prefix: str,
    speed_lora: str = "",
) -> dict:
    # 有加速 LoRA 时插入 LoraLoader 节点，model/clip 均改走 LoRA 输出
    model_src = ["8", 0] if speed_lora else ["1", 0]
    clip_src = ["8", 1] if speed_lora else ["1", 1]
    workflow = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": clip_src, "text": positive},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": clip_src, "text": negative},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": model_src,
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler_name,
                "scheduler": scheduler,
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": filename_prefix},
        },
    }
    if speed_lora:
        workflow["8"] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["1", 0],
                "clip": ["1", 1],
                "lora_name": speed_lora,
                "strength_model": 1.0,
                "strength_clip": 1.0,
            },
        }
    return workflow


def _queue_prompt(base_url: str, workflow: dict) -> str:
    response = _request_json(
        f"{base_url.rstrip('/')}/prompt",
        method="POST",
        payload={"prompt": workflow, "client_id": uuid4().hex},
        timeout=30,
    )
    prompt_id = str(response.get("prompt_id") or "").strip()
    if not prompt_id:
        raise ComfyUIError(f"ComfyUI 没有返回 prompt_id：{response}")
    return prompt_id


def _wait_for_image(base_url: str, prompt_id: str, timeout_seconds: int) -> dict:
    started = time.time()
    history_url = f"{base_url.rstrip('/')}/history/{urllib.parse.quote(prompt_id)}"
    while time.time() - started < timeout_seconds:
        history = _request_json(history_url, timeout=15)
        record = history.get(prompt_id)
        if record:
            status = record.get("status") or {}
            if status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise ComfyUIError(f"ComfyUI 生成失败：{messages}")
            outputs = record.get("outputs") or {}
            for output in outputs.values():
                for image in output.get("images") or []:
                    return image
        time.sleep(1.5)
    raise ComfyUIError("ComfyUI 图片生成超时")


def _download_image(base_url: str, image_meta: dict) -> bytes:
    query = urllib.parse.urlencode({
        "filename": image_meta.get("filename") or "",
        "subfolder": image_meta.get("subfolder") or "",
        "type": image_meta.get("type") or "output",
    })
    url = f"{base_url.rstrip('/')}/view?{query}"
    return _request_bytes(url, timeout=60)


def _request_json(url: str, method: str = "GET", payload: dict | None = None, timeout: int = 30) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener().open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise ComfyUIError(body or f"HTTP {resp.status}")
            return json.loads(body or "{}")
    except ComfyUIError:
        raise
    except Exception as exc:
        raise ComfyUIError(f"连接 ComfyUI 失败：{exc}") from exc


def _request_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "image/png,image/jpeg,*/*"})
    try:
        with _opener().open(req, timeout=timeout) as resp:
            data = resp.read()
            if resp.status >= 400:
                raise ComfyUIError(data.decode("utf-8", errors="replace") or f"HTTP {resp.status}")
            return data
    except ComfyUIError:
        raise
    except Exception as exc:
        raise ComfyUIError(f"下载 ComfyUI 图片失败：{exc}") from exc


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _snap_dimension(value: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    n = max(256, min(n, 1536))
    return max(256, (n // 8) * 8)


def _enhance_prompt(prompt: str) -> str:
    prompt = re.sub(r"\s+", " ", prompt).strip()
    suffix = (
        "professional editorial illustration, WeChat official account article cover, "
        "clean composition, high detail, cinematic lighting, no text, no logo"
    )
    if suffix.lower() in prompt.lower():
        return prompt
    return f"{prompt}, {suffix}"
