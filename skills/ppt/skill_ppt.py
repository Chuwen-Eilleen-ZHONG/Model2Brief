#!/usr/bin/env python3
"""
Skill 3: PPT 生成
输入：session.json 中的 render.output_image + report.output_text + report/charts/
输出：10 页 PPTX，保存到 outputs/{session_id}/final_output/
"""

import base64
import json
import os
import re
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# 复用同目录的 images_to_pptx
sys.path.insert(0, str(Path(__file__).parent))
from images_to_pptx import images_to_pptx as convert_to_pptx

_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
_SESSION_FILE = _PROJECT_ROOT / "session.json"
_STYLE_FILE = _SCRIPT_DIR / "styles" / "gradient-glass.md"


# =============================================================================
# Environment & Session
# =============================================================================

def load_env() -> None:
    for parent in [_SCRIPT_DIR, *_SCRIPT_DIR.parents]:
        env_path = parent / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=True)
            print(f"Loaded environment from: {env_path}")
            return
    load_dotenv(override=True)


def read_session() -> dict:
    with open(_SESSION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def write_session(session: dict) -> None:
    with open(_SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)


def load_ppt_config() -> tuple[str, int, str]:
    """从 session.json["config"] 读取 ppt.style / total_slides / resolution。"""
    session = read_session()
    cfg = session.get("config", {}).get("ppt", {})
    style        = cfg.get("style",        "gradient-glass")
    total_slides = int(cfg.get("total_slides", 10))
    resolution   = cfg.get("resolution",   "2K")
    return style, total_slides, resolution


def update_ppt_status(status: str, **kwargs) -> None:
    session = read_session()
    session["ppt"]["status"] = status
    for k, v in kwargs.items():
        session["ppt"][k] = v
    write_session(session)


# =============================================================================
# Gemini Client
# =============================================================================

def get_gemini_client():
    try:
        from google import genai
    except ImportError:
        print("Error: google-genai not installed.")
        sys.exit(1)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY not set")
        sys.exit(1)
    return genai.Client(api_key=api_key)


# =============================================================================
# Style & Report Parsing
# =============================================================================

def load_style(style_name: str = "gradient-glass") -> str:
    """读取指定风格 .md 文件的基础提示词部分。"""
    style_path = _SCRIPT_DIR / "styles" / f"{style_name}.md"
    if not style_path.exists():
        print(f"  Warning: style '{style_name}' not found, falling back to gradient-glass")
        style_path = _SCRIPT_DIR / "styles" / "gradient-glass.md"
    text = style_path.read_text(encoding="utf-8")
    m = re.search(r"## 基础提示词模板\n+(.*?)(?=\n## |\Z)", text, re.S)
    return m.group(1).strip() if m else text


def parse_report(report_path: Path) -> dict[str, str]:
    """解析 report.md，返回 {section_name: content} 字典。"""
    text = report_path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []

    for line in text.split("\n"):
        if line.startswith("# "):
            sections["title"] = line[2:].strip()
        elif line.startswith("## "):
            if current:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        elif line.startswith("### "):
            # 子章节标题收入当前 section
            buf.append(line)
        else:
            if current:
                buf.append(line)

    if current:
        sections[current] = "\n".join(buf).strip()

    return sections


def encode_image(path: Path) -> tuple[str, str]:
    """返回 (base64_str, mime_type)。"""
    with open(path, "rb") as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    suffix = path.suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp"}.get(suffix, "image/jpeg")
    return b64, mime


# =============================================================================
# Slide Generation
# =============================================================================

SLIDE_PLAN = [
    {
        "number": 1,
        "type": "cover",
        "title": "封面",
        "use_render": True,
        "use_chart": None,
        "content_key": None,
    },
    {
        "number": 2,
        "type": "content",
        "title": "项目概览",
        "use_render": False,
        "use_chart": None,
        "content_key": "综合评价",
    },
    {
        "number": 3,
        "type": "content",
        "title": "建筑形态分析",
        "use_render": True,
        "use_chart": None,
        "content_key": "建筑形态",
    },
    {
        "number": 4,
        "type": "content",
        "title": "立面与材质分析",
        "use_render": True,
        "use_chart": None,
        "content_key": "立面材质",
    },
    {
        "number": 5,
        "type": "data",
        "title": "功能构成分析",
        "use_render": False,
        "use_chart": "pie_chart.png",
        "content_key": "建筑类型",
    },
    {
        "number": 6,
        "type": "data",
        "title": "景观绿化分析",
        "use_render": False,
        "use_chart": "greenery_chart.png",
        "content_key": "景观绿化",
    },
    {
        "number": 7,
        "type": "content",
        "title": "底层功能与城市界面",
        "use_render": False,
        "use_chart": None,
        "content_key": "底层功能",
    },
    {
        "number": 8,
        "type": "content",
        "title": "设计亮点",
        "use_render": False,
        "use_chart": None,
        "content_key": "设计亮点",
    },
    {
        "number": 9,
        "type": "data",
        "title": "综合评价",
        "use_render": False,
        "use_chart": "bar_chart.png",
        "content_key": "综合评价",
    },
    {
        "number": 10,
        "type": "content",
        "title": "结语",
        "use_render": False,
        "use_chart": None,
        "content_key": None,
    },
]


def build_slide_prompt(slide: dict, style: str, topic: str,
                       report: dict[str, str]) -> str:
    """为单张幻灯片构建 Gemini 生成 prompt。"""
    num = slide["number"]
    total = len(SLIDE_PLAN)
    title = slide["title"]
    content_key = slide["content_key"]
    content = report.get(content_key, "") if content_key else ""
    # 截取内容避免 prompt 过长
    content_snippet = content[:600] if content else ""

    base = (
        f"{style}\n\n"
        f"项目主题：{topic}\n"
        f"幻灯片：第 {num}/{total} 页  标题：{title}\n\n"
    )

    if slide["type"] == "cover":
        return base + (
            "这是封面页。构图逻辑：在中心放置一个巨大的复杂3D玻璃物体，"
            f"覆盖粗体大字：{topic}，背景有延伸的极光波浪。"
            "参考图片为实际建筑渲染效果图，请体现建筑的整体气质。"
        )

    elif slide["type"] == "data":
        return base + (
            f"这是数据分析页，标题：{title}。\n"
            "构图逻辑：左侧排版以下文字，右侧展示浮动的发光3D数据可视化图表。\n"
            f"内容文字：\n{content_snippet}\n\n"
            "参考图片为本页对应的分析图表，请在右侧创作视觉化的数据展示。"
        )

    else:  # content
        return base + (
            f"这是内容页，标题：{title}。\n"
            "构图逻辑：使用Bento网格布局，磨砂玻璃容器，保留大量留白。\n"
            f"内容：\n{content_snippet}\n"
        )


def generate_slide_image(client, prompt: str, ref_b64: str | None,
                         ref_mime: str | None, output_path: Path,
                         slide_num: int, resolution: str = "2K") -> bool:
    """调用 Gemini 生成单张幻灯片图片。"""
    from google.genai import types

    print(f"  Generating slide {slide_num:02d} ...")

    parts: list[dict] = []
    if ref_b64 and ref_mime:
        parts.append({"inline_data": {"mime_type": ref_mime, "data": ref_b64}})
    parts.append({"text": prompt})

    try:
        response = client.models.generate_content(
            model="gemini-3-pro-image-preview",
            contents=[{"parts": parts}],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio="16:9",
                    image_size=resolution,
                ),
            ),
        )
        for part in response.parts:
            if part.inline_data is not None:
                img = part.as_image()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                img.save(str(output_path))
                print(f"    Saved: {output_path.name}")
                return True
        print(f"    Slide {slide_num}: no image returned")
        return False
    except Exception as e:
        print(f"    Slide {slide_num} failed: {e}")
        return False


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    load_env()

    session = read_session()
    session_id = session.get("session_id") or "unknown"
    topic = session.get("topic") or "建筑分析报告"

    # 验证前置输出
    render_rel = session.get("render", {}).get("output_image")
    report_rel = session.get("report", {}).get("output_text")

    if not render_rel:
        print("Error: render.output_image not set. Run skill_render.py first.")
        sys.exit(1)
    if not report_rel:
        print("Error: report.output_text not set. Run skill_report.py first.")
        sys.exit(1)

    render_path = _PROJECT_ROOT / render_rel
    report_path = _PROJECT_ROOT / report_rel
    charts_dir = _PROJECT_ROOT / "outputs" / str(session_id) / "report" / "charts"
    images_dir = _PROJECT_ROOT / "outputs" / str(session_id) / "final_output" / "images"
    final_dir  = _PROJECT_ROOT / "outputs" / str(session_id) / "final_output"

    print("=" * 60)
    print("Skill PPT: Slide Generation")
    print("=" * 60)
    print(f"Session : {session_id}")
    print(f"Topic   : {topic}")
    print(f"Render  : {render_path.name}")
    print(f"Report  : {report_path.name}")
    print(f"Output  : {final_dir}")
    print()

    try:
        client = get_gemini_client()

        # 读取配置
        ppt_style, total_slides, resolution = load_ppt_config()
        print(f"  Config  : style={ppt_style}, slides={total_slides}, resolution={resolution}")

        style  = load_style(ppt_style)
        report = parse_report(report_path)

        # 按 total_slides 截取 SLIDE_PLAN
        active_plan = SLIDE_PLAN[:total_slides]

        # 预加载渲染图
        render_b64, render_mime = encode_image(render_path)

        images_dir.mkdir(parents=True, exist_ok=True)

        # 清理旧的幻灯片图片，避免 images_to_pptx 混入上次运行的文件
        for old in images_dir.glob("slide-*.png"):
            old.unlink(missing_ok=True)

        generated: list[Path] = []

        for slide in active_plan:
            num = slide["number"]
            img_path = images_dir / f"slide-{num:02d}.png"

            # 选择参考图
            ref_b64, ref_mime = None, None
            if slide["use_render"]:
                ref_b64, ref_mime = render_b64, render_mime
            elif slide["use_chart"]:
                chart_path = charts_dir / slide["use_chart"]
                if chart_path.exists():
                    ref_b64, ref_mime = encode_image(chart_path)

            prompt = build_slide_prompt(slide, style, topic, report)
            ok = generate_slide_image(client, prompt, ref_b64, ref_mime, img_path, num,
                                      resolution=resolution)
            if ok:
                generated.append(img_path)

        print(f"\n  Generated {len(generated)}/{len(active_plan)} slides")

        # 合并为 PPTX
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in topic
        ).strip() or "presentation"
        pptx_path = final_dir / f"{safe_title}.pptx"
        # 如果文件被占用（如 Windows 下已打开），自动使用带时间戳的备用名
        if pptx_path.exists():
            try:
                pptx_path.unlink()
            except PermissionError:
                from datetime import datetime as _dt
                suffix = _dt.now().strftime("%H%M%S")
                pptx_path = final_dir / f"{safe_title}_{suffix}.pptx"
                print(f"  Note: original PPTX is locked, saving as {pptx_path.name}")
        print(f"\nConverting to PPTX ...")
        convert_to_pptx(str(images_dir), str(pptx_path))

        pptx_rel = f"outputs/{session_id}/final_output/{safe_title}.pptx"
        update_ppt_status("done", output_dir=f"outputs/{session_id}/final_output/",
                          output_pptx=pptx_rel)

        print()
        print("=" * 60)
        print("PPT Generation Complete!")
        print("=" * 60)
        print(f"PPTX: {pptx_path}")

    except Exception as e:
        msg = str(e)
        print(f"\nError: {msg}")
        update_ppt_status("failed", error=msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
