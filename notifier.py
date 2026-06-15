"""Webhook notifier — push evaluation results to Chinese IM platforms."""

import time
from datetime import datetime

import httpx


def build_markdown(results, run_time):
    """Build a markdown message body from evaluation results."""
    lines = [
        f"## GitHub 技术项目速递",
        f"扫描时间: {run_time.strftime('%Y-%m-%d %H:%M')}",
        f"本次发现 {len(results)} 个值得关注的项目",
        "",
    ]

    by_category = {}
    for r in results:
        cat = r.get("direction", r.get("category", "其他"))
        by_category.setdefault(cat, []).append(r)

    for cat, items in by_category.items():
        lines.append(f"### {cat}")
        lines.append("")
        for r in items:
            lang = r.get("language", "Unknown")
            desc = r.get("description", "") or "无描述"
            if len(desc) > 60:
                desc = desc[:57] + "..."

            lines.append(f"**[{r['full_name']}]({r['html_url']})**")
            lines.append(f"🔥{r['score']}星 | ⭐{r['stars']} | {lang}")
            lines.append(f"> {desc}")
            lines.append(f"> 推荐理由: {r.get('reason', '无')}")
            lines.append("")
        lines.append("")

    return "\n".join(lines)


def _retry_post(url, json_body, max_retries=3):
    """POST with retry on server errors."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = httpx.post(url, json=json_body, timeout=15)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPStatusError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"[notifier] 推送失败 (尝试 {attempt+1}/{max_retries})，{wait}s 后重试: {e}")
                time.sleep(wait)
    raise last_exc


def send_feishu(webhook_url, content):
    """Send markdown message to Feishu/Lark bot webhook."""
    body = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "GitHub 技术项目速递"},
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": content}
            ],
        },
    }
    resp = _retry_post(webhook_url, body)
    result = resp.json()
    if result.get("code") != 0:
        raise RuntimeError(f"Feishu webhook error: {result}")
    print("[notifier] 飞书消息推送成功")


def send_wecom(webhook_url, content):
    """Send markdown message to WeCom bot webhook."""
    body = {
        "msgtype": "markdown",
        "markdown": {"content": content},
    }
    resp = _retry_post(webhook_url, body)
    result = resp.json()
    if result.get("errcode") != 0:
        raise RuntimeError(f"WeCom webhook error: {result}")
    print("[notifier] 企业微信消息推送成功")


def send_dingtalk(webhook_url, content):
    """Send markdown message to DingTalk bot webhook."""
    body = {
        "msgtype": "markdown",
        "markdown": {
            "title": "GitHub 技术项目速递",
            "text": content,
        },
    }
    resp = _retry_post(webhook_url, body)
    result = resp.json()
    if result.get("errcode") != 0:
        raise RuntimeError(f"DingTalk webhook error: {result}")
    print("[notifier] 钉钉消息推送成功")


def send_generic(webhook_url, content):
    """Send plain text body to a generic webhook URL."""
    body = {"text": content, "timestamp": datetime.now().isoformat()}
    resp = _retry_post(webhook_url, body)
    print(f"[notifier] Generic webhook 推送成功 (status={resp.status_code})")


SENDERS = {
    "feishu": send_feishu,
    "wecom": send_wecom,
    "dingtalk": send_dingtalk,
    "generic": send_generic,
}


def run(results, config):
    """Push evaluation results to webhook.

    If webhook URL is placeholder, print results to console instead.
    """
    if not results:
        print("[notifier] 没有通过评估的项目，跳过推送")
        return

    webhook_url = config["webhook"]["url"]
    webhook_type = config["webhook"]["type"]
    run_time = datetime.now()

    content = build_markdown(results, run_time)

    if "your-hook-id" in webhook_url or not webhook_url.strip():
        print("\n" + "=" * 60)
        print("[notifier] 未配置 Webhook URL，以下是预览输出：")
        print("=" * 60)
        print(content)
        print("=" * 60)
        return

    sender = SENDERS.get(webhook_type, send_generic)
    sender(webhook_url, content)
