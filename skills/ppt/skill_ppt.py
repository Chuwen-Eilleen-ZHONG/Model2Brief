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
    style        = cfg.get("style",        "architecture")
    total_slides = int(cfg.get("total_slides", 10))
    resolution   = cfg.get("resolution",   "2K")
    return style, total_slides, resolution


def load_language() -> str:
    """从 session.json["config"] 读取 report.language。"""
    session = read_session()
    return session.get("config", {}).get("report", {}).get("language", "zh")


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

def load_style(style_name: str = "architecture") -> str:
    """读取指定风格 .md 文件的基础提示词部分。"""
    style_path = _SCRIPT_DIR / "styles" / f"{style_name}.md"
    if not style_path.exists():
        print(f"  Warning: style '{style_name}' not found, falling back to architecture")
        style_path = _SCRIPT_DIR / "styles" / "architecture.md"
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

# render_index: 指定用第几张渲染图（0-based），多图时各slide使用不同角度
# render_index=-1 表示"gallery"幻灯片，会在运行时传入全部渲染图
SLIDE_PLAN = [
    {
        "number": 1,
        "type": "cover",
        "title": "封面",
        "use_render": True,
        "render_index": 0,
        "use_chart": None,
        "content_key": None,
    },
    {
        "number": 2,
        "type": "content",
        "title": "项目概览",
        "use_render": False,
        "render_index": None,
        "use_chart": None,
        "content_key": "综合评价",
    },
    {
        "number": 3,
        "type": "gallery",
        "title": "多角度渲染展示",
        "use_render": True,
        "render_index": -1,      # -1 = 传入全部渲染图
        "use_chart": None,
        "content_key": None,
        "multi_render_only": True,   # 单图时跳过此slide
    },
    {
        "number": 4,
        "type": "content",
        "title": "建筑形态分析",
        "use_render": True,
        "render_index": 0,
        "use_chart": None,
        "content_key": "建筑形态",
    },
    {
        "number": 5,
        "type": "content",
        "title": "立面与材质分析",
        "use_render": True,
        "render_index": 1,       # 优先用第2张（侧/背面）；不足时自动回退到最后一张
        "use_chart": None,
        "content_key": "立面材质",
    },
    {
        "number": 6,
        "type": "data",
        "title": "功能构成分析",
        "use_render": False,
        "render_index": None,
        "use_chart": "pie_chart.png",
        "content_key": "建筑类型",
    },
    {
        "number": 7,
        "type": "data",
        "title": "景观绿化分析",
        "use_render": False,
        "render_index": None,
        "use_chart": "greenery_chart.png",
        "content_key": "景观绿化",
    },
    {
        "number": 8,
        "type": "content",
        "title": "底层功能与城市界面",
        "use_render": False,
        "render_index": None,
        "use_chart": None,
        "content_key": "底层功能",
    },
    {
        "number": 9,
        "type": "content",
        "title": "设计亮点",
        "use_render": False,
        "render_index": None,
        "use_chart": None,
        "content_key": "设计亮点",
    },
    {
        "number": 10,
        "type": "data",
        "title": "综合评价",
        "use_render": False,
        "render_index": None,
        "use_chart": "bar_chart.png",
        "content_key": "综合评价",
    },
    {
        "number": 11,
        "type": "content",
        "title": "结语",
        "use_render": False,
        "render_index": None,
        "use_chart": None,
        "content_key": None,
    },
]


def _lang_constraint(language: str) -> str:
    """返回语言强约束指令字符串。"""
    if language == "en":
        return "All text in this slide must be in English only. No Chinese characters."
    return (
        "【语言强制要求】幻灯片中所有文字必须使用简体中文，"
        "严禁出现英文单词或拉丁字母（专有名词如 BIM、CAD、CBD 除外）。"
        "标题、正文、注释、标注、图例全部用简体中文。"
    )


def build_slide_prompt(slide: dict, style: str, topic: str,
                       report: dict[str, str], total_slides: int,
                       language: str = "zh") -> str:
    """为单张幻灯片构建 Gemini 生成 prompt。"""
    num = slide["number"]
    title = slide["title"]
    content_key = slide["content_key"]
    content = report.get(content_key, "") if content_key else ""
    content_snippet = content[:600] if content else ""
    lang_note = _lang_constraint(language)

    base = (
        f"{style}\n\n"
        f"{lang_note}\n\n"
        f"项目主题：{topic}\n"
        f"幻灯片：第 {num}/{total_slides} 页  标题：{title}\n\n"
    )

    if slide["type"] == "cover":
        return base + (
            "这是封面页。严格按照建筑规划院汇报风格（architecture 风格）：\n"
            "构图：建筑渲染图占上方 70% 版面，图片下方纯白区域左对齐放置项目名称和副标题。\n"
            f"项目名称粗体大字：{topic}。禁止在图片上叠加文字。\n"
            "参考图片为实际建筑渲染效果图，直接裁切嵌入，无任何特效。"
        )

    elif slide["type"] == "gallery":
        return base + (
            "这是多角度渲染展示页，标题：多角度渲染展示。\n"
            "严格按照建筑规划院汇报风格：纯白背景，均匀网格排列各角度渲染图。\n"
            "每张图下方用简洁小字标注视角名称（如：北立面、南立面、东立面、鸟瞰）。\n"
            "图间距均等，无任何装饰边框或特效，整体干净专业。\n"
            "参考图片为各个角度的渲染效果图，请按网格均匀排列展示。"
        )

    elif slide["type"] == "data":
        return base + (
            f"这是数据分析页，标题：{title}。\n"
            "严格按照建筑规划院汇报风格：纯白背景，左侧文字区，右侧图表区。\n"
            "左侧文字内容：\n"
            f"{content_snippet}\n\n"
            "右侧展示简洁柱状图或饼图（无3D效果，配色仅用 #005BAC 和灰色阶）。\n"
            "参考图片为本页对应的数据图表，请直接嵌入，配细边框线。"
        )

    else:  # content
        return base + (
            f"这是内容页，标题：{title}。\n"
            "严格按照建筑规划院汇报风格：纯白背景，顶部细横线+标题，\n"
            "左侧 60% 放渲染图或示意图，右侧 40% 放文字要点（编号列表，每条不超过 25 字）。\n"
            f"文字内容：\n{content_snippet}\n"
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
        for part in (response.parts or []):
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


def _generate_gallery_slide(client, prompt: str, ref_parts: list,
                            output_path: Path, slide_num: int,
                            resolution: str = "2K") -> bool:
    """Gallery slide：把全部渲染图作为 inline_data 传给 Gemini。"""
    from google.genai import types

    print(f"  Generating slide {slide_num:02d} (gallery, {len(ref_parts)} renders) ...")

    parts = []
    for b64, mime in ref_parts:
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
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
        for part in (response.parts or []):
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
    topic = session.get("topic") or "空间智能决策报告"

    render_info = session.get("render", {})
    report_rel  = session.get("report", {}).get("output_text")

    # 支持 output_images（新）和 output_image（旧，向后兼容）
    render_rels = render_info.get("output_images") or []
    if not render_rels:
        single = render_info.get("output_image")
        if single:
            render_rels = [single]
        else:
            print("Error: render.output_images not set. Run skill_render.py first.")
            sys.exit(1)

    if not report_rel:
        print("Error: report.output_text not set. Run skill_report.py first.")
        sys.exit(1)

    report_path = _PROJECT_ROOT / report_rel
    charts_dir  = _PROJECT_ROOT / "outputs" / str(session_id) / "report" / "charts"
    images_dir  = _PROJECT_ROOT / "outputs" / str(session_id) / "final_output" / "images"
    final_dir   = _PROJECT_ROOT / "outputs" / str(session_id) / "final_output"

    print("=" * 60)
    print("Skill PPT: Slide Generation")
    print("=" * 60)
    print(f"Session : {session_id}")
    print(f"Topic   : {topic}")
    print(f"Renders : {len(render_rels)} image(s)  {render_rels}")
    print(f"Report  : {report_path.name}")
    print(f"Output  : {final_dir}")
    print()

    try:
        client = get_gemini_client()

        # 读取配置
        ppt_style, total_slides, resolution = load_ppt_config()
        language = load_language()
        print(f"  Config  : style={ppt_style}, slides={total_slides}, resolution={resolution}, language={language}")

        style  = load_style(ppt_style)
        report = parse_report(report_path)

        # 预加载所有渲染图
        render_images = []    # list of (b64, mime)
        for rel in render_rels:
            p = _PROJECT_ROOT / rel
            if p.exists():
                b64, mime = encode_image(p)
                render_images.append((b64, mime))
            else:
                print(f"  Warning: rendered image not found, skipping: {p}")

        if not render_images:
            raise RuntimeError("No valid rendered images found")

        n_renders = len(render_images)
        multi_render = n_renders > 1

        # 构建 active_plan：过滤/展开 SLIDE_PLAN
        # multi_render_only 的 slide 只在多图时保留
        base_plan = [s for s in SLIDE_PLAN
                     if not (s.get("multi_render_only") and not multi_render)]

        # 按 total_slides 截取
        active_plan = base_plan[:total_slides]
        # 重新编号（保持顺序即可，编号用于文件名）
        for i, slide in enumerate(active_plan):
            slide = dict(slide)   # shallow copy，避免修改全局 SLIDE_PLAN
            active_plan[i] = slide
            slide["_seq"] = i + 1   # 实际序号

        images_dir.mkdir(parents=True, exist_ok=True)

        # 清理旧的幻灯片图片
        for old in images_dir.glob("slide-*.png"):
            old.unlink(missing_ok=True)

        generated: list[Path] = []

        for slide in active_plan:
            seq = slide["_seq"]
            img_path = images_dir / f"slide-{seq:02d}.png"

            # 选择参考图
            ref_parts: list[tuple[str, str]] = []   # list of (b64, mime)

            if slide["use_render"]:
                r_idx = slide.get("render_index", 0)
                if r_idx == -1:
                    # gallery: 传入全部渲染图
                    ref_parts = render_images
                else:
                    # 普通 render slide：按 index 取，超出则取最后一张
                    clamped = min(r_idx, n_renders - 1)
                    ref_parts = [render_images[clamped]]
            elif slide["use_chart"]:
                chart_path = charts_dir / slide["use_chart"]
                if chart_path.exists():
                    ref_parts = [encode_image(chart_path)]

            # 合并为 Gemini parts 格式
            ref_b64_combined = None
            ref_mime_combined = None
            if len(ref_parts) == 1:
                ref_b64_combined, ref_mime_combined = ref_parts[0]
            elif len(ref_parts) > 1:
                # 多图：用第一张作为主参考（prompt 中已说明多角度）
                ref_b64_combined, ref_mime_combined = ref_parts[0]

            prompt = build_slide_prompt(slide, style, topic, report, len(active_plan), language)

            # gallery slide 特殊处理：把所有渲染图作为 parts 传给 Gemini
            if slide["type"] == "gallery" and len(ref_parts) > 1:
                ok = _generate_gallery_slide(client, prompt, ref_parts, img_path, seq, resolution)
            else:
                ok = generate_slide_image(client, prompt, ref_b64_combined, ref_mime_combined,
                                          img_path, seq, resolution=resolution)
            if ok:
                generated.append(img_path)

        print(f"\n  Generated {len(generated)}/{len(active_plan)} slides")

        # 合并为 PPTX
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in topic
        ).strip() or "presentation"
        pptx_path = final_dir / f"{safe_title}.pptx"
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
