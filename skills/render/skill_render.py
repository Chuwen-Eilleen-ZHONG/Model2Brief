#!/usr/bin/env python3
"""
Skill 1: 图片渲染增强
输入：session.json 中的 input_images（多张）或 input_image（单张，向后兼容）
输出：每张输入图对应一张渲染图，写回 session.json render.output_images

工作流程：
1. 读取 session.json -> input_images
2. 用 Gemini Vision (gemini-2.5-flash) 一次性分析全部输入图（多角度综合理解）
3. 根据综合描述构建渲染 prompt
4. 逐张调用 Gemini 图像生成，分别保存为 rendered_01.jpg, rendered_02.jpg...
5. 写回 session.json render.output_images（列表）和 render.output_image（第一张）
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


def load_render_config() -> tuple[str, str, str]:
    """从 session.json["config"] 读取 render.quality、render.lighting 和 report.language。"""
    session = read_session()
    cfg = session.get("config", {}).get("render", {})
    quality  = cfg.get("quality",  "high")
    lighting = cfg.get("lighting", "golden_hour")
    language = session.get("config", {}).get("report", {}).get("language", "zh")
    return quality, lighting, language


def update_render_status(status: str, **kwargs) -> None:
    session = read_session()
    if "render" not in session:
        session["render"] = {}
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
# Step 1: Vision — Understand the image(s)
# =============================================================================

def analyze_images_multi(client, images: list, language: str = "zh") -> str:
    """用 gemini-2.5-flash 一次性分析多张建筑模型图（多角度综合理解）。

    images: list of (image_b64: str, mime_type: str)
    language: "zh" 或 "en"，控制描述输出语言
    """
    n = len(images)
    if n == 1:
        angle_note = (
            "This is a photo of an architectural physical scale model "
            "(white foam/resin material)."
        )
    else:
        angle_note = (
            f"These are {n} photos of the same architectural physical scale model "
            f"(white foam/resin material) taken from different angles "
            f"(e.g. front view, rear view, left side, right side). "
            "Analyze them together as a comprehensive multi-angle record of the same building."
        )

    if language == "zh":
        lang_inst = "请用简体中文描述，所有内容必须使用中文（建筑专业术语可用英文缩写，如 BIM、CAD）。"
    else:
        lang_inst = "Please respond in English."

    print(f"  Analyzing {n} image(s) with Gemini Vision ...")

    parts = []
    for i, (b64, mime) in enumerate(images):
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
        if n > 1:
            parts.append({"text": f"[View {i + 1} of {n}]"})

    parts.append({
        "text": (
            f"{angle_note} {lang_inst} "
            "Please analyze in detail for the purpose of generating "
            "photorealistic architectural renderings. Describe: "
            "1) Building form: overall shape, massing, number of floors, "
            "facade composition, roof type, notable architectural features; "
            "2) Spatial layout: building footprint, surrounding site, "
            "relationship between volumes; "
            "3) Viewing angles and perspectives visible across all photos: "
            "camera height, angle, which facades are shown in each view; "
            "4) Proportions and scale: relative dimensions of key elements. "
            "Be specific and precise. Focus on architectural geometry, not model materials."
        )
    })

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[{"parts": parts}],
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
        "no model artifacts, no white foam material visible, "
        "absolutely no text labels, no watermarks, no overlay text or annotations "
        "in any language on the image."
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

    # 1. 读取 session.json，支持 input_images（新）和 input_image（旧，向后兼容）
    session = read_session()
    input_images_rel = session.get("input_images") or []
    if not input_images_rel:
        single = session.get("input_image")
        if single:
            input_images_rel = [single]
        else:
            print("Error: session.json -> input_images is not set")
            sys.exit(1)

    session_id = session.get("session_id") or "unknown"

    print("=" * 60)
    print("Skill Render: Image Enhancement")
    print("=" * 60)
    print(f"Input : {len(input_images_rel)} image(s)  {input_images_rel}")
    print()

    try:
        client = get_gemini_client()

        # 读取配置
        quality, lighting, language = load_render_config()
        print(f"  Config  : quality={quality}, lighting={lighting}, language={language}")

        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp"}

        # 预加载所有输入图片
        loaded = []   # list of (Path, b64, mime)
        for rel in input_images_rel:
            p = _PROJECT_ROOT / rel
            if not p.exists():
                msg = f"Input image not found: {p}"
                print(f"Error: {msg}")
                update_render_status("failed", error=msg)
                sys.exit(1)
            with open(p, "rb") as f:
                data = f.read()
            b64 = base64.b64encode(data).decode()
            mime = mime_map.get(p.suffix.lower(), "image/jpeg")
            loaded.append((p, b64, mime))

        # Step 1: 一次性多角度 Vision 分析
        description = analyze_images_multi(
            client, [(b64, mime) for _, b64, mime in loaded], language=language
        )

        # Step 2: 构建渲染 prompt（共用一份描述）
        prompt = build_render_prompt(description, quality=quality, lighting=lighting)

        # Step 3: 逐张生成渲染图
        output_images_rel = []
        render_result_dir = _PROJECT_ROOT / "outputs" / str(session_id) / "render_result"

        for i, (img_path, img_b64, img_mime) in enumerate(loaded):
            suffix_num = f"{i + 1:02d}"
            out_path = render_result_dir / f"rendered_{suffix_num}.jpg"
            out_rel  = f"outputs/{session_id}/render_result/rendered_{suffix_num}.jpg"

            aspect_ratio, _, _ = select_aspect_ratio(img_path)
            print(f"\n  [View {i + 1}/{len(loaded)}] {img_path.name}")
            generate_render(client, img_b64, img_mime, prompt, aspect_ratio, out_path)
            output_images_rel.append(out_rel)

        # Step 4: 写回 session.json
        update_render_status(
            "done",
            output_image=output_images_rel[0],    # 向后兼容
            output_images=output_images_rel,       # 新字段
            description=description,
        )

        print()
        print("=" * 60)
        print("Render Complete!")
        print("=" * 60)
        for rel in output_images_rel:
            print(f"  {rel}")

    except Exception as e:
        msg = str(e)
        print(f"\nError: {msg}")
        update_render_status("failed", error=msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
