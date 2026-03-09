#!/usr/bin/env python3
"""
Skill 2: 图片内容分析报告（自适应任意图片类型）

工作流程：
1. 读取 session.json -> render.output_image, topic
2. 阶段一：Gemini Vision 识别图片类型
3. 阶段二：根据类型动态选择分析维度，生成结构化分析
4. 提取 summary，生成 report.md
5. 写回 session.json
"""

import base64
import json
import os
import re
import sys
from pathlib import Path

import yaml
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from dotenv import load_dotenv


# =============================================================================
# Path Setup
# =============================================================================

_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
_SESSION_FILE = _PROJECT_ROOT / "session.json"


# =============================================================================
# Type → Analysis Prompt Mapping
# =============================================================================

# 每个类型的分析维度，统一用【维度名称】格式，便于解析
IMAGE_TYPE_PROMPTS: dict[str, str] = {
    "建筑/城市规划": (
        "请从以下维度详细分析，每个维度用【维度名称】开头，100-150字，专业建筑评论语言：\n"
        "【建筑类型】功能构成（住宅/商业/办公/综合体等）\n"
        "【建筑风格】设计语言与时代特征\n"
        "【建筑形态】层数、体量、平面布局、塔楼与裙房关系\n"
        "【立面材质】主要材质、窗墙比、阳台、幕墙系统、立面节奏\n"
        "【景观绿化】绿化率、植被类型、景观层次、屋顶花园\n"
        "【底层功能】商业裙房、主入口、架空层、公共空间\n"
        "【城市关系】街道界面、退线、与周边环境融合\n"
        "【设计亮点】最多3条，提炼最具创新性的设计特色"
    ),
    "室内设计": (
        "请从以下维度详细分析，每个维度用【维度名称】开头，100-150字，专业室内设计评论语言：\n"
        "【空间类型】空间功能定位与使用场景\n"
        "【设计风格】风格流派与设计语言\n"
        "【材质色彩】主要材质、色彩体系、质感搭配\n"
        "【家具陈设】家具选型、软装配置、陈设逻辑\n"
        "【灯光设计】自然采光、人工照明层次、氛围营造\n"
        "【功能布局】空间划分、动线组织、功能合理性\n"
        "【设计亮点】最多3条，最具创意或代表性的设计细节"
    ),
    "产品设计": (
        "请从以下维度详细分析，每个维度用【维度名称】开头，100-150字，专业产品设计评论语言：\n"
        "【产品类型】产品类别与使用场景推断\n"
        "【设计风格】造型语言、设计流派\n"
        "【造型特征】形态、比例、线条、体量感\n"
        "【材质工艺】可见材质、表面处理、工艺推断\n"
        "【色彩搭配】主色调、配色逻辑、视觉效果\n"
        "【功能推断】从外观推断的核心功能与交互方式\n"
        "【设计亮点】最多3条，最具创新性的设计特色"
    ),
    "自然景观": (
        "请从以下维度详细分析，每个维度用【维度名称】开头，100-150字，专业景观摄影/生态语言：\n"
        "【景观类型】地理类型与生态系统特征\n"
        "【地貌特征】地形、地貌、地质特点\n"
        "【植被分布】植被类型、覆盖率、层次结构\n"
        "【光线气候】光照条件、天气状态、时段推断\n"
        "【色彩层次】主色调、色彩过渡、季节特征\n"
        "【视觉焦点】画面构图、视觉中心、景深关系\n"
        "【生态价值】生物多样性或环境价值推断"
    ),
    "人物/肖像": (
        "请从以下维度详细分析，每个维度用【维度名称】开头，100-150字，专业摄影评论语言：\n"
        "【拍摄风格】摄影风格与创作取向\n"
        "【构图方式】取景、构图、视角选择\n"
        "【光线运用】光源类型、光线方向、明暗对比\n"
        "【色调处理】整体色调、色彩倾向、后期风格\n"
        "【情绪表达】画面情绪与氛围营造\n"
        "【技术特征】景深、快门、对焦等技术特点推断\n"
        "【视觉亮点】最多3条，最具表现力的摄影特色"
    ),
    "艺术作品": (
        "请从以下维度详细分析，每个维度用【维度名称】开头，100-150字，专业艺术评论语言：\n"
        "【作品类型】艺术形式（绘画/雕塑/装置/数字艺术等）\n"
        "【艺术风格】流派归属与风格特征\n"
        "【构图形式】画面结构、元素布局、视觉节奏\n"
        "【色彩运用】色彩选择、对比关系、情感表达\n"
        "【主题内容】作品主题、象征含义、叙事逻辑\n"
        "【技法特征】创作技法、笔触/质感/工艺特点\n"
        "【艺术价值】审美价值与创作意义推断"
    ),
    "工业/机械": (
        "请从以下维度详细分析，每个维度用【维度名称】开头，100-150字，专业工业设计语言：\n"
        "【设备类型】设备/机械类型与应用领域推断\n"
        "【结构特征】整体结构、主要部件、组装逻辑\n"
        "【材质工艺】材质选用、表面处理、制造工艺\n"
        "【功能推断】核心功能、工作原理推断\n"
        "【设计语言】工业美学、人机工程考量\n"
        "【技术亮点】最多3条，技术或设计层面的突出特点"
    ),
    "其他": (
        "请自由分析这张图片，用【维度名称】格式组织内容，每个维度100字左右：\n"
        "【内容概述】图片主体内容描述\n"
        "【视觉特征】主要视觉元素与构成\n"
        "【色彩分析】色彩搭配与视觉效果\n"
        "【技术质量】图片质量与呈现方式\n"
        "【核心信息】图片传达的核心信息或意义\n"
        "【突出亮点】最多3条，最值得关注的内容"
    ),
}

VALID_TYPES = list(IMAGE_TYPE_PROMPTS.keys())


# =============================================================================
# Environment
# =============================================================================

def load_env() -> None:
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


def load_report_config() -> tuple[str, str]:
    """从 session.json["config"] 读取 report.language 和 report.depth。"""
    session = read_session()
    cfg = session.get("config", {}).get("report", {})
    language = cfg.get("language", "zh")
    depth    = cfg.get("depth",    "detailed")
    return language, depth


def update_report_status(status: str, **kwargs) -> None:
    session = read_session()
    session["report"]["status"] = status
    for k, v in kwargs.items():
        session["report"][k] = v
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


def vision_call(client, image_b64: str, mime_type: str, text: str) -> str:
    """通用 Vision 调用，返回文本响应。"""
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            {
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                    {"text": text},
                ]
            }
        ],
    )
    return response.text.strip()


# =============================================================================
# Stage 1: Identify Image Type
# =============================================================================

def identify_image_type(client, image_b64: str, mime_type: str) -> str:
    """阶段一：识别图片类型，返回标准化类型字符串。"""
    print("  Stage 1: Identifying image type ...")

    type_list = "\n".join(f"- {t}" for t in VALID_TYPES)
    prompt = (
        f"请分析这张图片，判断它属于以下哪个类别。\n"
        f"只返回类别名称，不要其他任何内容：\n{type_list}"
    )

    raw = vision_call(client, image_b64, mime_type, prompt)

    # 匹配到已知类型
    for t in VALID_TYPES:
        if t in raw:
            print(f"  Identified type: {t}")
            return t

    # 未匹配则归入"其他"
    print(f"  Type not matched (got: {raw!r}), fallback to 其他")
    return "其他"


# =============================================================================
# Stage 2: Deep Analysis
# =============================================================================

def analyze_image(client, image_b64: str, mime_type: str,
                  image_type: str, topic: str,
                  language: str = "zh", depth: str = "detailed") -> str:
    """阶段二：根据图片类型动态分析，支持语言和深度配置。"""
    print(f"  Stage 2: Deep analysis for type='{image_type}' (lang={language}, depth={depth}) ...")

    type_prompt = IMAGE_TYPE_PROMPTS.get(image_type, IMAGE_TYPE_PROMPTS["其他"])

    # 根据 depth 调整字数要求
    if depth == "brief":
        type_prompt = type_prompt.replace("100-150字", "50-80字").replace("100字", "50字")

    # 根据 language 设置语言指令
    if language == "en":
        lang_inst = "Please answer in English."
        topic_hint = f"Analysis topic: {topic}\n" if topic else ""
        # 将中文维度名翻译为英文（用英文版提示词框架覆盖）
        type_prompt = type_prompt  # 保持原有结构，Gemini 会理解 topic 上下文
    else:
        lang_inst = "中文回答。"
        topic_hint = f"分析主题：{topic}\n" if topic else ""

    full_prompt = f"{topic_hint}{lang_inst}\n\n{type_prompt}"
    return vision_call(client, image_b64, mime_type, full_prompt)


# =============================================================================
# Summary Extraction
# =============================================================================

def extract_summary(client, image_type: str, analysis: str, topic: str) -> str:
    """根据图片类型生成3-5句话核心摘要。"""
    print("  Extracting summary ...")

    topic_hint = f"主题：{topic}。" if topic else ""
    type_hint = f"图片类型：{image_type}。"

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=(
            f"{topic_hint}{type_hint}\n"
            f"以下是图片分析报告：\n\n{analysis}\n\n"
            "请用3-5句话提炼核心亮点，作为综合评价摘要。"
            "语言简洁专业，语气和侧重点须与图片类型匹配，"
            "适合出现在PPT或项目介绍中。中文回答。"
        ),
    )
    return response.text.strip()


# =============================================================================
# Chart Generation (建筑/城市规划 专用)
# =============================================================================

def extract_chart_data(client, analysis: str) -> dict:
    """让 Gemini 从分析文本中提取图表所需数据，返回 dict。"""
    print("  Extracting chart data ...")
    prompt = (
        "根据以下建筑分析报告，请估算并返回JSON格式的图表数据（只返回JSON，不要其他文字）：\n\n"
        f"{analysis[:2000]}\n\n"
        "返回格式：\n"
        "{\n"
        '  "functions": [{"name":"住宅","pct":65},{"name":"商业","pct":20},...],\n'
        '  "materials": [{"name":"清水混凝土","pct":40},{"name":"玻璃幕墙","pct":35},...],\n'
        '  "greenery": {"building": 30, "city_average": 18}\n'
        "}\n"
        "functions 列表总和须为100，materials 列表总和须为100，"
        "greenery 单位为百分比（整数）。"
    )
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    raw = response.text.strip()
    # 提取 JSON 块
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        return json.loads(m.group())
    raise ValueError(f"Failed to parse chart data JSON: {raw[:200]}")


def _find_chinese_font() -> str | None:
    """尝试找到系统中可用的中文字体，返回字体路径或 None。"""
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",   # 黑体
        "C:/Windows/Fonts/simsun.ttc",   # 宋体
        "C:/Windows/Fonts/STZHONGS.TTF", # 华文中宋
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def generate_charts(chart_data: dict, charts_dir: Path) -> list[str]:
    """用 matplotlib 生成3张图表，返回生成的文件路径列表。"""
    charts_dir.mkdir(parents=True, exist_ok=True)
    generated = []

    # 设置中文字体
    font_path = _find_chinese_font()
    font_prop = fm.FontProperties(fname=font_path) if font_path else None
    plt.rcParams["axes.unicode_minus"] = False

    def fp(text_or_label=None):
        """返回 fontproperties 参数 dict（仅当有中文字体时）。"""
        return {"fontproperties": font_prop} if font_prop else {}

    # ---- 图1：建筑功能构成饼图 ----
    funcs = chart_data.get("functions", [])
    if funcs:
        fig, ax = plt.subplots(figsize=(7, 5), facecolor="#1a1a2e")
        ax.set_facecolor("#1a1a2e")
        labels = [f["name"] for f in funcs]
        sizes  = [f["pct"]  for f in funcs]
        colors = ["#6c63ff", "#3ec9ff", "#ff6b6b", "#ffd93d", "#6bcb77"][:len(funcs)]
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, autopct="%1.0f%%",
            colors=colors, startangle=140,
            textprops={"color": "white", "fontsize": 11,
                       **({"fontproperties": font_prop} if font_prop else {})},
            wedgeprops={"linewidth": 2, "edgecolor": "#1a1a2e"},
        )
        for at in autotexts:
            at.set_fontsize(10)
            at.set_color("white")
        ax.set_title("建筑功能构成", color="white", fontsize=14,
                     pad=15, **fp())
        fig.tight_layout()
        path = charts_dir / "pie_chart.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        generated.append(str(path))
        print(f"  Chart saved: {path.name}")

    # ---- 图2：立面材质分布柱状图 ----
    mats = chart_data.get("materials", [])
    if mats:
        fig, ax = plt.subplots(figsize=(7, 4), facecolor="#1a1a2e")
        ax.set_facecolor("#1a1a2e")
        names = [m["name"] for m in mats]
        pcts  = [m["pct"]  for m in mats]
        colors = ["#6c63ff", "#3ec9ff", "#ff6b6b", "#ffd93d"][:len(mats)]
        bars = ax.barh(names, pcts, color=colors, height=0.5,
                       edgecolor="#1a1a2e", linewidth=1.5)
        for bar, pct in zip(bars, pcts):
            ax.text(pct + 0.5, bar.get_y() + bar.get_height() / 2,
                    f"{pct}%", va="center", color="white", fontsize=10)
        ax.set_xlim(0, 110)
        ax.set_xlabel("占比 (%)", color="#aaa", fontsize=10, **fp())
        ax.set_title("立面材质分布", color="white", fontsize=14, **fp())
        ax.tick_params(colors="white", labelsize=10)
        ax.spines[:].set_visible(False)
        for tick in ax.get_yticklabels():
            if font_prop:
                tick.set_fontproperties(font_prop)
        fig.tight_layout()
        path = charts_dir / "bar_chart.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        generated.append(str(path))
        print(f"  Chart saved: {path.name}")

    # ---- 图3：绿化率对比图 ----
    gr = chart_data.get("greenery", {})
    if gr:
        fig, ax = plt.subplots(figsize=(6, 4), facecolor="#1a1a2e")
        ax.set_facecolor("#1a1a2e")
        categories = ["本项目绿化率", "城市平均绿化率"]
        values = [gr.get("building", 30), gr.get("city_average", 20)]
        colors = ["#6bcb77", "#aaa"]
        bars = ax.bar(categories, values, color=colors, width=0.4,
                      edgecolor="#1a1a2e", linewidth=1.5)
        for bar, val in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, val + 0.5,
                    f"{val}%", ha="center", color="white", fontsize=12,
                    fontweight="bold")
        ax.set_ylim(0, max(values) * 1.4)
        ax.set_ylabel("绿化率 (%)", color="#aaa", fontsize=10, **fp())
        ax.set_title("绿化率对比", color="white", fontsize=14, **fp())
        ax.tick_params(colors="white", labelsize=10)
        ax.spines[:].set_visible(False)
        for tick in ax.get_xticklabels():
            if font_prop:
                tick.set_fontproperties(font_prop)
        fig.tight_layout()
        path = charts_dir / "greenery_chart.png"
        fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        generated.append(str(path))
        print(f"  Chart saved: {path.name}")

    return generated


# =============================================================================
# Report Generation
# =============================================================================

def parse_dimensions(analysis: str) -> list[tuple[str, str]]:
    """从分析文本中解析【维度名称】段落，返回有序列表 [(name, content), ...]。"""
    result = []
    current_key = None
    buffer: list[str] = []

    for line in analysis.split("\n"):
        # 检测【...】开头
        if line.lstrip().startswith("【") and "】" in line:
            if current_key is not None:
                result.append((current_key, "\n".join(buffer).strip()))
            bracket_end = line.index("】")
            current_key = line[line.index("【") + 1:bracket_end]
            rest = line[bracket_end + 1:].strip()
            buffer = [rest] if rest else []
        else:
            if current_key is not None:
                buffer.append(line)

    if current_key is not None:
        result.append((current_key, "\n".join(buffer).strip()))

    return result


def build_report_md(image_type: str, analysis: str, summary: str,
                    topic: str, image_path: str) -> str:
    """生成 markdown 格式报告。"""
    title = topic if topic else "图片分析报告"
    dimensions = parse_dimensions(analysis)

    lines = [
        f"# {title}",
        "",
        f"> 分析来源：`{image_path}`",
        "",
        "## 图片类型",
        "",
        image_type,
        "",
        "## 详细分析",
        "",
    ]

    if dimensions:
        for name, content in dimensions:
            lines.append(f"### {name}")
            lines.append("")
            lines.append(content if content else "（未提取到内容）")
            lines.append("")
    else:
        # 解析失败时直接输出原文
        lines.append(analysis)
        lines.append("")

    lines += [
        "## 综合评价",
        "",
        summary,
        "",
    ]

    return "\n".join(lines)


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    load_env()

    session = read_session()
    render_output = session.get("render", {}).get("output_image")
    if not render_output:
        print("Error: session.json -> render.output_image is not set. Run skill_render.py first.")
        sys.exit(1)

    image_path = _PROJECT_ROOT / render_output
    if not image_path.exists():
        msg = f"Rendered image not found: {image_path}"
        print(f"Error: {msg}")
        update_report_status("failed", error=msg)
        sys.exit(1)

    session_id = session.get("session_id") or "unknown"
    topic = session.get("topic") or ""

    report_path = _PROJECT_ROOT / "outputs" / str(session_id) / "report" / "report.md"
    report_rel = f"outputs/{session_id}/report/report.md"

    print("=" * 60)
    print("Skill Report: Adaptive Image Analysis")
    print("=" * 60)
    print(f"Input : {image_path}")
    print(f"Output: {report_path}")
    print()

    try:
        client = get_gemini_client()

        with open(image_path, "rb") as f:
            image_data = f.read()
        image_b64 = base64.b64encode(image_data).decode()
        suffix = image_path.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp"}
        mime_type = mime_map.get(suffix, "image/jpeg")

        # 读取配置
        language, depth = load_report_config()
        print(f"  Config  : language={language}, depth={depth}")

        # 阶段一：类型识别
        image_type = identify_image_type(client, image_b64, mime_type)

        # 阶段二：深度分析
        analysis = analyze_image(client, image_b64, mime_type, image_type, topic,
                                 language=language, depth=depth)

        # 提取 summary
        summary = extract_summary(client, image_type, analysis, topic)

        # 生成并保存 report.md
        report_md = build_report_md(image_type, analysis, summary, topic, render_output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")
        print(f"  Report saved: {report_path}")

        # 如果是建筑类型，额外生成分析图表
        charts_dir = _PROJECT_ROOT / "outputs" / str(session_id) / "report" / "charts"
        chart_paths: list[str] = []
        if image_type == "建筑/城市规划":
            try:
                chart_data = extract_chart_data(client, analysis)
                chart_paths = generate_charts(chart_data, charts_dir)
                print(f"  {len(chart_paths)} charts generated")
            except Exception as ce:
                print(f"  Warning: chart generation failed: {ce}")

        # 写回 session.json（新增 image_type 字段）
        update_report_status(
            "done",
            image_type=image_type,
            output_text=report_rel,
            summary=summary,
            charts_dir=f"outputs/{session_id}/report/charts/" if chart_paths else None,
        )

        print()
        print("=" * 60)
        print("Report Complete!")
        print("=" * 60)
        print(f"Type   : {image_type}")
        print(f"Report : {report_path}")
        print()
        print("--- Summary ---")
        print(summary)

    except Exception as e:
        msg = str(e)
        print(f"\nError: {msg}")
        update_report_status("failed", error=msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
