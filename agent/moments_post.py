"""Write and publish a text-only WeChat Moments (朋友圈) post.

Topics: ai (AI tech/products), bayarea (Bay Area news), events (Bay Area events), stocks (market analysis).

Usage:
  python -m agent.moments_post --topic ai --dry-run    # just print the text
  python -m agent.moments_post --topic ai --test       # fill the compose box, then cancel
  python -m agent.moments_post --topic ai              # publish
  python -m agent.moments_post --topic ai --text "..."  # publish given text
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from agent.group_post import write_post

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".moments_post_state.json"
RPA_SCRIPT = Path(__file__).resolve().parent / "moments_rpa.py"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
CONTAINER = "wechat-ai"

STYLE = """要求：
- 身份：这是一个 AI 机器人整理的资讯卡片，以「AI 小助手」的口吻客观播报，不用第一人称「我」，不评论、不说教、不给建议。
- 格式（纯文本，不要 Markdown、话题标签、链接）：
  第一行：「🤖 AI 小助手｜{title}｜{date}」
  第二行：一句话点题，概括这期最重要的变化（客观陈述，不抒情）。
  然后 3-4 条，每条以「· 」开头，一行一件事：主体 + 发生了什么 + 关键数字或时间，简短。
  最后一行：「{footer}」
- 总长度 120-220 字（朋友圈折叠前能看完）。
- 只写搜到并能确认的事实，数字要准确，不要编造；不涉及任何公司内部信息。
- 不要和上一期重复，除非有新进展。上一期：
{previous}
- 只输出卡片本身。"""

FOOTER = "—— AI 自动整理，仅供参考"

TOPICS = {
    "ai": {
        "title": "AI 科技速递",
        "brief": "请先用网络搜索查最近 3-7 天最新的 AI 技术和产品动态：新模型发布（OpenAI、Google Gemini、Anthropic、Meta、xAI、"
        "国内大模型等）、新产品和功能上线、重要开源项目、AI 芯片和硬件（Nvidia、AMD 等）。挑最有分量的 3-4 条。",
    },
    "bayarea": {
        "title": "湾区新闻",
        "brief": "请先用网络搜索查最近 3-7 天旧金山湾区（San Jose、硅谷、旧金山、奥克兰等）的重大本地新闻：政策和选举、交通、"
        "公共安全、房价和租金、大公司裁员或扩张、自动驾驶上路等对本地居民有实际影响的事。挑最重要的 3-4 条，不写天气和节日。",
    },
    "events": {
        "title": "湾区活动推荐",
        "brief": "请先用网络搜索查未来 7-10 天湾区（San Jose、硅谷、旧金山、东湾）值得参加的活动：科技大会和 meetup、展览、"
        "音乐会和演出、市集、户外活动、免费活动。挑 3-4 个，每条写清日期、地点（城市）、是否免费。",
    },
    "stocks": {
        "title": "美股科技观察",
        "brief": "请先用网络搜索查最近一个交易日和本周的美股表现：三大指数（纳指、标普500、道指）涨跌，"
        "科技股（Nvidia、Apple、Microsoft、Google、Meta、Amazon、Tesla{extra_tickers}）的明显异动及原因（财报、宏观数据、美联储）。"
        "第二行点题写本周市场主线；3-4 条写具体数据和原因；只陈述事实和市场观点，不给任何买卖建议。",
        "footer": "—— AI 自动整理，仅供参考，不构成投资建议",
    },
}


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def generate(topic: str, now: float) -> str:
    spec = TOPICS[topic]
    local = datetime.fromtimestamp(now, LOCAL_TZ)
    style = STYLE.format(
        title=spec["title"],
        date=f"{local.month}/{local.day}",
        footer=spec.get("footer", FOOTER),
        previous=_load_state().get(topic, {}).get("text") or "（无）",
    )
    extra = os.environ.get("MOMENTS_EXTRA_TICKERS", "").strip()
    brief = spec["brief"].replace("{extra_tickers}", "、" + extra if extra else "")
    return write_post(f"现在是 {local:%Y-%m-%d}（太平洋时间）。" + brief + "\n" + style)


def publish(text: str, mode: str) -> dict:
    env = {"DISPLAY": ":99", "MOMENTS_MODE": mode, "MOMENTS_TEXT_B64": base64.b64encode(text.encode()).decode()}
    if os.environ.get("MOMENTS_RPA_DIRECT") == "1":
        # Panel container shares the host network, so WeChat's X display :99 is reachable directly.
        cmd, stdin = [sys.executable, str(RPA_SCRIPT)], None
        env = {**os.environ, **env}
    else:
        flags = [arg for k, v in env.items() for arg in ("-e", f"{k}={v}")]
        cmd = ["docker", "exec", "-i", *flags, CONTAINER, "/opt/venv-bot/bin/python", "-"]
        stdin, env = RPA_SCRIPT.read_text(), None
    proc = subprocess.run(cmd, input=stdin, env=env, capture_output=True, text=True, timeout=120)
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    if not lines:
        return {"status": "error", "message": (proc.stderr or proc.stdout)[-500:]}
    return json.loads(lines[-1])


def save_state(topic: str, text: str, status: str, now: float) -> None:
    state = _load_state()
    state[topic] = {"text": text, "posted_at": int(now), "status": status}
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.moments_post")
    parser.add_argument("--topic", choices=list(TOPICS), required=True)
    parser.add_argument("--text", help="Publish this text instead of generating one")
    parser.add_argument("--dry-run", action="store_true", help="Print the generated text only")
    parser.add_argument("--test", action="store_true", help="Fill the compose box, then cancel")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env", override=True)
    now = time.time()
    text = (args.text or generate(args.topic, now)).strip()
    if not text:
        print("error: empty text", file=sys.stderr)
        return 1
    if args.dry_run:
        print(text)
        return 0

    result = publish(text, "test" if args.test else "publish")
    stamp = datetime.fromtimestamp(now, LOCAL_TZ).strftime("%m-%d %H:%M")
    print(f"{stamp} moments topic={args.topic} status={result['status']} {result.get('message', '')}")
    print(text)
    if result["status"] in {"published", "unconfirmed"} and not args.text:
        save_state(args.topic, text, result["status"], now)
    return 0 if result["status"] in {"published", "tested"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
