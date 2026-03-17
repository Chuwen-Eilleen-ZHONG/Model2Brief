#!/usr/bin/env python3
"""
总 Agent 编排器
按顺序调度三个 Skill，维护共享上下文 session.json

用法：
    python agent.py --image inputs/view1.png
    python agent.py --image inputs/view1.png inputs/view2.png inputs/view3.png inputs/view4.png
    python agent.py --image inputs/view1.png --topic "空间智能决策报告"
    python agent.py --image inputs/view1.png --style "architecture" --ppt-only
"""

import argparse
import io
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Windows GBK 终端不支持 emoji，强制 UTF-8 输出
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# =============================================================================
# Paths
# =============================================================================

_ROOT = Path(__file__).parent
_SESSION_FILE = _ROOT / "session.json"
_CONFIG_FILE  = _ROOT / "config.yaml"
_SKILL_RENDER = _ROOT / "skills" / "render" / "skill_render.py"
_SKILL_REPORT = _ROOT / "skills" / "report" / "skill_report.py"
_SKILL_PPT    = _ROOT / "skills" / "ppt"    / "skill_ppt.py"

# 虚拟环境 Python
if sys.platform == "win32":
    _VENV_PYTHON = _ROOT / "venv" / "Scripts" / "python.exe"
else:
    _VENV_PYTHON = _ROOT / "venv" / "bin" / "python"


# =============================================================================
# Config
# =============================================================================

_DEFAULT_CONFIG = {
    "ppt":    {"style": "gradient-glass", "total_slides": 10, "resolution": "2K"},
    "render": {"quality": "high",         "lighting": "golden_hour"},
    "report": {"language": "zh",          "depth": "detailed"},
    "output": {"session_prefix": "project"},
}


def load_config() -> dict:
    """读取 config.yaml，不存在时返回默认配置。"""
    if not _CONFIG_FILE.exists():
        return _DEFAULT_CONFIG.copy()
    with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    # 深度合并默认值
    merged = {}
    for section, defaults in _DEFAULT_CONFIG.items():
        merged[section] = {**defaults, **(data.get(section) or {})}
    return merged


def apply_cli_overrides(config: dict, args: argparse.Namespace) -> dict:
    """CLI 参数优先级高于 config.yaml，覆盖对应字段。"""
    if getattr(args, "style", None):
        config["ppt"]["style"] = args.style
    if getattr(args, "total_slides", None):
        config["ppt"]["total_slides"] = args.total_slides
    if getattr(args, "resolution", None):
        config["ppt"]["resolution"] = args.resolution
    if getattr(args, "quality", None):
        config["render"]["quality"] = args.quality
    if getattr(args, "lighting", None):
        config["render"]["lighting"] = args.lighting
    if getattr(args, "language", None):
        config["report"]["language"] = args.language
    if getattr(args, "depth", None):
        config["report"]["depth"] = args.depth
    return config


# =============================================================================
# Session helpers
# =============================================================================

def init_session(session_id: str, topic: str, images_rel: list, config: dict) -> None:
    session = {
        "session_id": session_id,
        "topic": topic,
        "input_image": images_rel[0],       # 向后兼容：保留第一张
        "input_images": images_rel,          # 新字段：完整列表
        "config": config,
        "render": {
            "status": "pending",
            "output_image": None,
            "output_images": [],
            "description": None,
        },
        "report": {
            "status": "pending",
            "output_text": None,
            "summary": None,
            "image_type": None,
        },
        "ppt": {
            "status": "pending",
            "output_dir": None,
        },
    }
    _SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def read_session() -> dict:
    return json.loads(_SESSION_FILE.read_text(encoding="utf-8"))


def update_session_config(config: dict) -> None:
    """仅更新现有 session 的 config 字段，保留 render/report 数据。"""
    session = read_session()
    session["config"] = config
    session["ppt"]["status"] = "pending"
    _SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_failed(skill_key: str, error: str) -> None:
    session = read_session()
    session[skill_key]["status"] = "failed"
    session[skill_key]["error"] = error
    _SESSION_FILE.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


# =============================================================================
# Skill runner
# =============================================================================

def run_skill(label: str, script: Path, skill_key: str) -> bool:
    """运行单个 Skill，实时打印输出，返回是否成功。"""
    if not _VENV_PYTHON.exists():
        msg = f"Virtual environment not found: {_VENV_PYTHON}"
        print(f"  ERROR: {msg}")
        mark_failed(skill_key, msg)
        return False

    env = {"PYTHONUTF8": "1"}
    import os
    full_env = {**os.environ, **env}

    result = subprocess.run(
        [str(_VENV_PYTHON), str(script)],
        cwd=str(_ROOT),
        capture_output=False,   # 直接继承 stdout/stderr，实时输出
        env=full_env,
    )

    if result.returncode != 0:
        session = read_session()
        error = session.get(skill_key, {}).get("error", f"exit code {result.returncode}")
        print(f"\n  ERROR: {label} failed — {error}")
        return False

    return True


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="My Banana Workflow — 多 Agent 建筑分析流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python agent.py --image inputs/test.png\n"
            "  python agent.py --image inputs/test.png --style architecture\n"
            "  python agent.py --image inputs/test.png --style architecture --ppt-only"
        ),
    )
    parser.add_argument("--image",        default=None,  nargs="+", help="输入图片路径（支持多张：view1.png view2.png view3.png view4.png）")
    parser.add_argument("--topic",        default="空间智能决策报告", help="报告主题（可选）")
    # CLI 覆盖 config.yaml 的参数
    parser.add_argument("--style",        default=None,  help="PPT风格，覆盖config.yaml ppt.style")
    parser.add_argument("--total-slides", default=None,  type=int, dest="total_slides",
                        help="PPT总页数，覆盖config.yaml ppt.total_slides")
    parser.add_argument("--resolution",   default=None,  help="图片分辨率：2K/4K")
    parser.add_argument("--quality",      default=None,  help="渲染质量：high/medium")
    parser.add_argument("--lighting",     default=None,  help="光照风格：golden_hour/daylight/dramatic")
    parser.add_argument("--language",     default=None,  help="报告语言：zh/en")
    parser.add_argument("--depth",        default=None,  help="分析深度：detailed/brief")
    parser.add_argument("--ppt-only",     action="store_true", dest="ppt_only",
                        help="跳过 render/report，只重新生成 PPT（使用现有 session 数据）")
    args = parser.parse_args()

    # 1. 加载并合并配置
    config = load_config()
    config = apply_cli_overrides(config, args)

    # 2. PPT-only 模式：复用已有 session
    if args.ppt_only:
        if not _SESSION_FILE.exists():
            print("ERROR: session.json not found. Run without --ppt-only first.")
            sys.exit(1)
        update_session_config(config)
        session = read_session()
        session_id = session.get("session_id", "unknown")
        topic = session.get("topic", args.topic)

        print(f"\n{'=' * 60}")
        print(f"  My Banana Workflow [PPT-only]")
        print(f"{'=' * 60}")
        print(f"  Session : {session_id}")
        print(f"  Topic   : {topic}")
        print(f"  Style   : {config['ppt']['style']}")
        print(f"  Slides  : {config['ppt']['total_slides']}")
        print(f"{'=' * 60}\n")

        print("[3/3] 正在生成PPT...")
        ok = run_skill("[3/3] 正在生成PPT...", _SKILL_PPT, "ppt")
        if not ok:
            print("\n工作流中止。请检查 session.json 中 ppt.error 获取详情。")
            sys.exit(1)
        print()

        session = read_session()
        pptx = session.get("ppt", {}).get("output_pptx", "")
        print(f"✅ 完成！输出目录：outputs/{session_id}/")
        if pptx:
            print(f"📄 PPT 文件  ：{pptx}")
        return

    # 3. 验证图片（全流程模式必须提供）
    if not args.image:
        print("ERROR: --image is required (or use --ppt-only to skip render/report)")
        sys.exit(1)

    images_rel = []
    for raw in args.image:
        p = Path(raw)
        if not p.is_absolute():
            p = _ROOT / raw
        if not p.exists():
            print(f"ERROR: Image not found: {p}")
            sys.exit(1)
        try:
            rel = str(p.relative_to(_ROOT)).replace("\\", "/")
        except ValueError:
            rel = str(p).replace("\\", "/")
        images_rel.append(rel)

    # 4. 生成 session_id（含 prefix）
    prefix = config.get("output", {}).get("session_prefix", "project")
    session_id = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 5. 初始化 session.json（含 config）
    init_session(session_id, args.topic, images_rel, config)
    print(f"\n{'=' * 60}")
    print(f"  My Banana Workflow")
    print(f"{'=' * 60}")
    print(f"  Session : {session_id}")
    print(f"  Topic   : {args.topic}")
    print(f"  Images  : {len(images_rel)} 张  {images_rel}")
    print(f"  Style   : {config['ppt']['style']}")
    print(f"  Slides  : {config['ppt']['total_slides']}")
    print(f"{'=' * 60}\n")

    # 6. 创建输出目录
    base = _ROOT / "outputs" / session_id
    for sub in ["render_result", "report", "report/charts", "final_output"]:
        (base / sub).mkdir(parents=True, exist_ok=True)

    # 7. 顺序执行三个 Skill
    steps = [
        ("🎨 [1/3] 正在渲染图片...",   _SKILL_RENDER, "render"),
        ("📝 [2/3] 正在分析报告...",   _SKILL_REPORT, "report"),
        ("📊 [3/3] 正在生成PPT...",    _SKILL_PPT,    "ppt"),
    ]

    for label, script, key in steps:
        print(label)
        ok = run_skill(label, script, key)
        if not ok:
            print(f"\n工作流中止。请检查 session.json 中 {key}.error 获取详情。")
            sys.exit(1)
        print()

    # 8. 完成
    session = read_session()
    pptx = session.get("ppt", {}).get("output_pptx", "")
    print(f"✅ 完成！输出目录：outputs/{session_id}/")
    if pptx:
        print(f"📄 PPT 文件  ：{pptx}")


if __name__ == "__main__":
    main()
