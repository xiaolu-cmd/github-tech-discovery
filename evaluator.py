"""DeepSeek evaluator — parallel LLM assessment of repo relevance."""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import yaml
from openai import OpenAI, APIError, APITimeoutError

# Valid tech direction categories (used for output validation)
VALID_DIRECTIONS = ["AI / 人工智能", "网络安全", "实用工具"]

EVAL_PROMPT = """你是一个技术项目评估专家。用户在关注以下三个技术方向：
1. AI / 人工智能（LLM、Agent、RAG、机器学习、深度学习等）
2. 网络安全（漏洞扫描、渗透测试、安全工具、CVE、红队等）
3. 实用工具（CLI 工具、自动化、DevOps、效率工具、自托管等）

现在有一个 GitHub 仓库，请根据它的描述和 README 内容，评估它是否值得用户关注。

仓库信息：
- 名称: {full_name}
- 描述: {description}
- 语言: {language}
- Stars: {stars}
- Topics: {topics}

README 摘要（前 5000 字符）:
{readme_preview}

请严格按以下 JSON 格式返回评估结果（不要返回其他内容）：
{{"score": <1-5 整数>, "direction": "<AI / 人工智能 | 网络安全 | 实用工具>", "reason": "<一句话推荐理由，中文，不超过50字>"}}

评分标准：
- 5: 非常值得关注，在技术方向上具有创新性或高实用性
- 4: 值得关注，项目质量不错，与方向相关
- 3: 有一定关联，可以了解一下
- 2: 关联度较低
- 1: 不相关或不值得关注

如果仓库信息太少（无描述且无 README），直接返回 score=1, direction="实用工具"。"""


def _validate_direction(direction):
    """Normalize direction to a valid category name."""
    for valid in VALID_DIRECTIONS:
        if valid in direction:
            return valid
    return direction if direction in VALID_DIRECTIONS else "实用工具"


def _evaluate_one(item, client, model):
    """Evaluate a single repo, return (item_with_scores)."""
    readme = item.get("readme") or ""
    readme_preview = readme[:5000]

    prompt = EVAL_PROMPT.format(
        full_name=item["full_name"],
        description=item.get("description") or "无",
        language=item.get("language") or "Unknown",
        stars=item.get("stars", 0),
        topics=", ".join(item.get("topics", [])) or "无",
        readme_preview=readme_preview or "（无 README）",
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=200,
        timeout=45,
    )

    text = resp.choices[0].message.content.strip()

    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    try:
        result = json.loads(text)
        score = int(result.get("score", 1))
        direction = _validate_direction(result.get("direction", "实用工具"))
        reason = result.get("reason", "无") or "无"
    except (json.JSONDecodeError, ValueError, KeyError):
        match = re.search(r'"score"\s*:\s*(\d)', text)
        score = int(match.group(1)) if match else 1
        direction = "实用工具"
        reason = text[:80]

    item["score"] = min(max(score, 1), 5)
    item["direction"] = direction
    item["reason"] = reason
    return item


def run(results, config):
    """Evaluate all repos in parallel, return those meeting the score threshold.

    Returns list of dicts with added keys: score, direction, reason
    """
    api_key = config["deepseek"]["api_key"]
    base_url = config["deepseek"]["base_url"]
    model = config["deepseek"]["model"]
    min_score = config["webhook"]["min_score"]

    proxy = config.get("proxy", {})
    proxy_url = proxy.get("https") or proxy.get("http") or None

    transport = httpx.HTTPTransport(retries=1) if proxy_url else None
    http_client = httpx.Client(
        proxy=proxy_url, timeout=60, transport=transport
    ) if proxy_url else None

    try:
        client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

        passed = []
        total = len(results)
        workers = min(5, total) if total > 0 else 1

        print(f"[evaluator] 并行评估 {total} 个仓库 (并发数={workers})...")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_evaluate_one, item, client, model): item
                for item in results
            }

            done_count = 0
            for future in as_completed(futures):
                done_count += 1
                try:
                    item = future.result()
                except (httpx.HTTPError, APIError, APITimeoutError, OSError) as e:
                    item = futures[future]
                    item["score"] = 2
                    item["direction"] = "未知"
                    item["reason"] = f"评估网络错误: {str(e)[:40]}"

                name = item["full_name"]
                if item["score"] >= min_score:
                    passed.append(item)
                    print(f"[evaluator] ({done_count}/{total}) ⭐{item['score']} [{item['direction']}] {name}")
                    print(f"             {item['reason']}")
                else:
                    print(f"[evaluator] ({done_count}/{total}) ⭐{item['score']} (过滤) {name}")

        passed.sort(key=lambda x: (x["score"], x["stars"]), reverse=True)
        print(f"[evaluator] 完成，{len(passed)}/{total} 个仓库通过评估（≥{min_score}星）")
        return passed
    finally:
        if http_client:
            http_client.close()
