"""GitHub 技术项目自动发现 — 主入口.

Usage:
    python main.py          # 启动定时调度模式
    python main.py --once   # 手动运行一次
"""

import argparse
import json
import os
import sys
import io
import time
import traceback
from datetime import datetime, timezone

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR

import fetcher
import evaluator
import notifier


SEEN_FILE = "seen.json"
CONFIG_FILE = "config.yaml"
MAX_SEEN_ENTRIES = 50000
REEVALUATE_AFTER_DAYS = 30

# Windows terminal UTF-8 encoding compatibility
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def load_seen():
    """Load seen repo tracking data. Returns dict {repo_id: {seen_at, score}}."""
    if not os.path.exists(SEEN_FILE):
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(seen):
    """Save seen repo tracking data, cap at MAX_SEEN_ENTRIES."""
    if len(seen) > MAX_SEEN_ENTRIES:
        # Keep most recent entries
        sorted_items = sorted(
            seen.items(),
            key=lambda x: x[1].get("seen_at", ""),
            reverse=True,
        )
        seen = dict(sorted_items[:MAX_SEEN_ENTRIES])
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)


def should_skip(repo_id, seen_cache):
    """Check if a repo should be skipped based on seen history.

    Skips repos seen within REEVALUATE_AFTER_DAYS.
    Repos seen longer ago will be re-evaluated (they may have improved).
    """
    if repo_id not in seen_cache:
        return False
    entry = seen_cache[repo_id]
    seen_at = entry.get("seen_at", "")
    if not seen_at:
        return True
    try:
        seen_time = datetime.fromisoformat(seen_at)
        age_days = (datetime.now(timezone.utc) - seen_time).days
        return age_days < REEVALUATE_AFTER_DAYS
    except (ValueError, TypeError):
        return True


def resolve_env_vars(obj):
    """Recursively resolve ${VAR} patterns in config values from environment."""
    if isinstance(obj, str):
        if obj.startswith("${") and obj.endswith("}"):
            var_name = obj[2:-1]
            return os.environ.get(var_name, "")
        return obj
    if isinstance(obj, dict):
        return {k: resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_env_vars(v) for v in obj]
    return obj


def check_config():
    """Validate config has required fields filled in."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config = resolve_env_vars(config)

    api_key = config["deepseek"]["api_key"]
    webhook_url = config["webhook"]["url"]

    issues = []
    if not api_key:
        issues.append("DeepSeek API Key 未配置 (设置 DEEPSEEK_API_KEY 环境变量)")
    if not webhook_url:
        issues.append("Webhook URL 未配置（将使用控制台输出）")

    return config, issues


def run_pipeline():
    """Execute one full pipeline run.

    Returns True on success, False on failure (for scheduler error tracking).
    """
    print(f"\n{'='*60}")
    print(f"[main] GitHub 技术项目发现 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    config, issues = check_config()
    for issue in issues:
        print(f"[main] [WARN] {issue}")

    seen_cache = load_seen()
    print(f"[main] 已追踪 {len(seen_cache)} 个历史仓库 (>{REEVALUATE_AFTER_DAYS}天前可重新评估)")

    # Step 1: Fetch
    try:
        raw_results = fetcher.run(config)
    except Exception as e:
        print(f"[main] 数据获取失败: {e}")
        traceback.print_exc()
        return False

    if not raw_results:
        print("[main] 本次未获取到新仓库")
        return True

    # Step 2: Deduplicate — skip recently-seen repos, re-evaluate old ones
    new_results = [r for r in raw_results if not should_skip(r["id"], seen_cache)]
    reevaluating = [
        r for r in raw_results
        if r["id"] in seen_cache and not should_skip(r["id"], seen_cache)
    ]
    skipped = len(raw_results) - len(new_results)

    if skipped:
        print(f"[main] 跳过 {skipped} 个近期已处理仓库")
    if reevaluating:
        print(f"[main] 重新评估 {len(reevaluating)} 个超过 {REEVALUATE_AFTER_DAYS} 天的旧仓库")

    if not new_results:
        print("[main] 所有仓库近期均已处理过")
        return True

    # Step 3: Evaluate with DeepSeek
    try:
        passed = evaluator.run(new_results, config)
    except Exception as e:
        print(f"[main] 评估失败: {e}")
        traceback.print_exc()
        # Still mark fetched repos so we don't retry them immediately
        now_str = datetime.now(timezone.utc).isoformat()
        for r in raw_results:
            seen_cache[str(r["id"])] = {"seen_at": now_str, "score": 0}
        save_seen(seen_cache)
        return False

    # Step 4: Notify
    notifier.run(passed, config)

    # Step 5: Update seen cache with all evaluated repos
    now_str = datetime.now(timezone.utc).isoformat()
    for r in new_results:
        seen_cache[str(r["id"])] = {
            "seen_at": now_str,
            "score": r.get("score", 0),
        }
    # Also mark skipped repos as still-seen (update timestamp)
    for r in raw_results:
        rid = str(r["id"])
        if rid not in seen_cache:
            seen_cache[rid] = {"seen_at": now_str, "score": r.get("score", 0)}

    save_seen(seen_cache)
    print(f"[main] seen.json 已更新，当前追踪 {len(seen_cache)} 个仓库")
    print(f"[main] 本轮完成")
    return True


def job_error_listener(event):
    """Handle scheduler job errors to prevent silent failures."""
    print(f"[scheduler] 任务执行异常: {event.exception}")
    if event.traceback:
        print(event.traceback)


def main():
    parser = argparse.ArgumentParser(description="GitHub 技术项目自动发现")
    parser.add_argument(
        "--once", action="store_true", help="手动运行一次后退出"
    )
    args = parser.parse_args()

    if args.once:
        success = run_pipeline()
        sys.exit(0 if success else 1)

    # Scheduled mode
    config, issues = check_config()
    interval = config["scheduler"]["interval_hours"]
    run_immediately = config["scheduler"]["run_immediately"]

    print(f"[main] 启动定时模式，每 {interval} 小时执行一次")
    for issue in issues:
        print(f"[main] [WARN] {issue}")

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_pipeline,
        "interval",
        hours=interval,
        next_run_time=datetime.now() if run_immediately else None,
        id="github_discovery",
        misfire_grace_time=900,  # 15 minutes grace
    )
    scheduler.add_listener(job_error_listener, EVENT_JOB_ERROR)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n[main] 已停止")


if __name__ == "__main__":
    main()
