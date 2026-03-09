#!/usr/bin/env python3
"""
Skill 1: 图片渲染增强
输入：session.json 中的 input_image
输出：渲染后的图片路径，写回 session.json render.output_image

工作流程：
1. 读取 session.json -> input_image
2. 用 Gemini Vision (gemini-2.0-flash) 理解图片内容
3. 根据理解结果构建渲染增强 prompt
4. 用 Gemini 图像生成 (gemini-3-pro-image) 生成高质量渲染图
5. 保存渲染图，写回 session.json
"""

import base64
import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from PIL import Image


# =============================================================================
# Path Setup
# =============================================================================

_SCRIPT_DIR = Path(__file__).parent          # skills/render/
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent    # my-banana-workflow/
_SESSION_FILE = _PROJECT_ROOT / "session.json"
_CONFIG_FILE  = _PROJECT_ROOT / "config.yaml"

# 光照描述映射
_LIGHTING_MAP = {
    "golden_hour": (
        "natural golden hour sunlight (late afternoon warm light), "
        "strong directional light with clear shadow casting, warm orange-gold highlights "
        "on facades, cool blue shadows, volumetric atmospheric haze"
    ),
    "daylight": (
        "clear midday sunlight, neutral white natural light, even illumination, "
        "minimal harsh shadows, bright and airy atmosphere, crisp shadows"
    ),
    "dramatic": (
        "dramatic overcast storm lighting, high contrast chiaroscuro, "
        "dark brooding clouds with breaking light, deep shadows on recessed surfaces, "
        "cinematic spotlight effect on key architectural elements"
    ),
}

# 渲染质量映射
_QUALITY_MAP = {
    "high":   "ultra-high definition, professional architectural CGI rendering, 8K clarity, sharp details on facades and surroundings",
    "medium": "high definition, professional architectural visualization, 4K quality",
}


# =============================================================================
# Environment
# =============================================================================

def load_env() -> None:
    """从项目根目录 .env 加载环境变量，向上查找直到找到为止。"""
    for parent in [_SCRIPT_DIR, *_SCRIPT_DIR.parents]:
        env_path = parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
            print(f"Loaded environment from: {env_path}")
            return
    load_dotenv(override=True)
    print("Warning: No .env file found, using system environment variables")


# =============================================================================
# Session
# =============================================================================

def read_session() -> dict:
    with open(_SESSION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_session(session: dict) -> None:
    with open(_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


def load_render_config() -> tuple[str, str]:
    """从 session.json["config"] 读取 render.quality 和 render.lighting。"""
    session = read_session()
    cfg = session.get("config", {}).get("render", {})
    quality  = cfg.get("quality",  "high")
    lighting = cfg.get("lighting", "golden_hour")
    return quality, lighting


def update_render_status(status: str, **kwargs) -> None:
    session = read_session()
    session["render"]["status"] = status
    for k, v in kwargs.items():
        session["render"][k] = v
    write_session(session)


# =============================================================================
# Gemini Client
# =============================================================================

def get_gemini_client():
    try:
        from google import genai
    except ImportError:
        print("Error: google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)

    return genai.Client(api_key=api_key)


# =============================================================================
# Aspect Ratio
# =============================================================================

def select_aspect_ratio(image_path: Path) -> tuple[str, int, int]:
    """读取原图尺寸，返回 (Gemini aspect_ratio 字符串, width, height)。"""
    with Image.open(image_path) as img:
        w, h = img.size
    ratio = w / h
    if ratio >= 1.7:
        aspect = "16:9"
    elif ratio >= 1.2:
        aspect = "4:3"
    elif ratio >= 0.9:
        aspect = "1:1"
    elif ratio >= 0.6:
        aspect = "3:4"
    else:
        aspect = "9:16"
    print(f"  Image size: {w}x{h} (ratio={ratio:.2f}) → aspect={aspect}")
    return aspect, w, h


# =============================================================================
# Step 1: Vision — Understand the image
# =============================================================================

def analyze_image(client, image_b64: str, mime_type: str) -> str:
    """用 gemini-2.5-flash + 原图 inline_data 分析建筑模型形态。"""
    print("  Analyzing image with Gemini Vision ...")

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            {
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                    {
                        "text": (
                            "This is a photo of an architectural physical scale model "
                            "(white foam/resin material). Please analyze it in detail for "
                            "the purpose of generating a photorealistic architectural rendering. "
                            "Describe: "
                            "1) Building form: overall shape, massing, number of floors, "
                            "facade composition, roof type, notable architectural features; "
                            "2) Spatial layout: building footprint, surrounding site, "
                            "relationship between volumes; "
                            "3) Viewing angle and perspective: camera height, angle, "
                            "which facades are visible; "
                            "4) Proportions and scale: relative dimensions of key elements. "
                            "Be specific and precise. Focus on architectural geometry, not model materials."
                        )
                    },
                ]
            }
        ],
    )

    description = response.text.strip()
    print(f"  Vision description: {description[:120]}...")
    return description


# =============================================================================
# Step 2: Build render prompt
# =============================================================================

def build_render_prompt(description: str, quality: str = "high", lighting: str = "golden_hour") -> str:
    """根据 Vision 描述和配置参数，构建写实建筑效果图渲染 prompt。"""
    lighting_desc = _LIGHTING_MAP.get(lighting, _LIGHTING_MAP["golden_hour"])
    quality_desc  = _QUALITY_MAP.get(quality,   _QUALITY_MAP["high"])
    return (
        f"The following is an architectural analysis of a physical scale model photo:\n"
        f"{description}\n\n"
        "Task: This image is a white foam/resin architectural scale model. "
        "Transform it into a photorealistic architectural visualization rendering "
        "as if it were a real, completed building.\n\n"
        "Rendering requirements:\n"
        "- Style: photorealistic architectural visualization, photo-quality realism\n"
        f"- Lighting: {lighting_desc}\n"
        "- Materials: replace all white model surfaces with real building materials — "
        "  glass curtain walls with reflections and sky mirroring, "
        "  concrete with visible texture and weathering, "
        "  metal panels with specular highlights, "
        "  stone cladding with surface detail\n"
        "- Environment: blue sky with scattered white clouds, "
        "  surrounding landscaping with mature trees and shrubs, "
        "  ground-level paving, roads, and pedestrian areas appropriate to building scale\n"
        "- Perspective: strictly preserve the exact camera angle, viewing direction, "
        "  building proportions, and composition from the original model photo\n"
        f"- Quality: {quality_desc}\n"
        "Output: a single photorealistic architectural rendering, "
        "no model artifacts, no white foam material visible."
    )


# =============================================================================
# Step 3: Generate rendered image
# =============================================================================

def generate_render(client, image_b64: str, mime_type: str, prompt: str,
                    aspect_ratio: str, output_path: Path) -> str:
    """用 gemini-3-pro-image-preview + 原图 inline_data 生成写实建筑效果图。"""
    from google.genai import types

    print(f"  Generating rendered image (aspect={aspect_ratio})...")

    response = client.models.generate_content(
        model="gemini-3-pro-image-preview",
        contents=[
            {
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                    {"text": prompt},
                ]
            }
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=aspect_ratio,
                image_size="2K",
            ),
        ),
    )

    for part in response.parts:
        if part.inline_data is not None:
            image = part.as_image()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(output_path))
            print(f"  Rendered image saved: {output_path}")
            return str(output_path)

    raise RuntimeError("Gemini returned no image data")


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    load_env()

    # 1. 读取 session.json
    session = read_session()
    input_image_rel = session.get("input_image")
    if not input_image_rel:
        print("Error: session.json -> input_image is not set")
        sys.exit(1)

    input_image_path = _PROJECT_ROOT / input_image_rel
    if not input_image_path.exists():
        msg = f"Input image not found: {input_image_path}"
        print(f"Error: {msg}")
        update_render_status("failed", error=msg)
        sys.exit(1)

    # 确定 session_id（有则用，无则用 unknown）
    session_id = session.get("session_id") or "unknown"

    # 输出路径
    output_image_path = _PROJECT_ROOT / "outputs" / str(session_id) / "render_result" / "rendered.jpg"
    output_image_rel = f"outputs/{session_id}/render_result/rendered.jpg"

    print("=" * 60)
    print("Skill Render: Image Enhancement")
    print("=" * 60)
    print(f"Input : {input_image_path}")
    print(f"Output: {output_image_path}")
    print()

    try:
        client = get_gemini_client()

        # 读取配置
        quality, lighting = load_render_config()
        print(f"  Config  : quality={quality}, lighting={lighting}")

        # 读取图片尺寸，选择最接近的 aspect ratio
        aspect_ratio, orig_w, orig_h = select_aspect_ratio(input_image_path)

        # 读取图片二进制，转 base64，确定 MIME
        with open(input_image_path, "rb") as f:
            image_data = f.read()
        image_b64 = base64.b64encode(image_data).decode()
        suffix = input_image_path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp"}
        mime_type = mime_map.get(suffix, "image/jpeg")

        # Step 1: Vision 理解原图
        description = analyze_image(client, image_b64, mime_type)

        # Step 2: 构建渲染 prompt（注入 quality / lighting）
        prompt = build_render_prompt(description, quality=quality, lighting=lighting)

        # Step 3: 生成渲染图（传入原图 + 自适应比例）
        generate_render(client, image_b64, mime_type, prompt, aspect_ratio, output_image_path)

        # Step 4: 写回 session.json
        update_render_status(
            "done",
            output_image=output_image_rel,
            description=description,
        )

        print()
        print("=" * 60)
        print("Render Complete!")
        print("=" * 60)
        print(f"Output: {output_image_path}")

    except Exception as e:
        msg = str(e)
        print(f"\nError: {msg}")
        update_render_status("failed", error=msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
