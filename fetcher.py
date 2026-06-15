"""GitHub API data fetcher — async parallel search and README fetch."""

import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone

import httpx
import yaml


def build_queries(config):
    """Build search queries per category with date window."""
    lookback = config["github"]["lookback_days"]
    since = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")

    queries = []
    for cat in config["categories"]:
        for q in cat["queries"]:
            full_q = f"{q} created:>{since}"
            queries.append((cat["name"], full_q))
    return queries


def _build_headers(token):
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "github-tech-discovery/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class RateLimitTracker:
    """Tracks GitHub API rate limit across concurrent requests.

    Tracks search and core limits separately since they have different quotas.
    Authenticated: 30 search/min + 5000 core/hr
    Unauthenticated: 10 search/min + 60 core/hr
    """

    def __init__(self, authenticated):
        self.search_remaining = 30 if authenticated else 10
        self.core_remaining = 5000 if authenticated else 55
        self.search_reset = time.time() + 60
        self.core_reset = time.time() + 3600
        self.lock = asyncio.Lock()

    async def update(self, response):
        """Update remaining count from response headers."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is None or reset is None:
            return
        remaining = int(remaining)
        reset_time = int(reset)
        async with self.lock:
            # Search API has lower limits; detect by URL path
            if int(remaining) <= 30 and reset_time - time.time() < 120:
                self.search_remaining = remaining
                self.search_reset = reset_time
            else:
                self.core_remaining = remaining
                self.core_reset = reset_time

    async def _acquire_core(self):
        while True:
            async with self.lock:
                if self.core_remaining > 2:
                    self.core_remaining -= 1
                    return
                wait = max(self.core_reset - time.time(), 1)
            print(f"[fetcher] 等待核心 API 限制 ({wait:.0f}s)...")
            await asyncio.sleep(min(wait, 60))

    async def _acquire_search(self):
        while True:
            async with self.lock:
                if self.search_remaining > 1:
                    self.search_remaining -= 1
                    return
                wait = max(self.search_reset - time.time(), 1)
            print(f"[fetcher] 等待搜索 API 限制 ({wait:.0f}s)...")
            await asyncio.sleep(min(wait, 60))

    async def acquire_for_search(self):
        await self._acquire_search()

    async def acquire_for_core(self):
        await self._acquire_core()


def _is_rate_limited(status_code):
    return status_code in (403, 429)


async def search_github(query, per_page, sort, order, client, token, rl_tracker):
    """Search GitHub repositories API (async)."""
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": sort, "order": order, "per_page": per_page}
    headers = _build_headers(token)

    await rl_tracker.acquire_for_search()
    resp = await client.get(url, params=params, headers=headers)
    await rl_tracker.update(resp)

    if _is_rate_limited(resp.status_code):
        await rl_tracker.acquire_for_search()
        resp = await client.get(url, params=params, headers=headers)
        await rl_tracker.update(resp)

    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


async def fetch_readme(full_name, client, token, rl_tracker):
    """Fetch a repo's README content (async)."""
    url = f"https://api.github.com/repos/{full_name}/readme"
    headers = _build_headers(token)

    await rl_tracker.acquire_for_core()
    resp = await client.get(url, headers=headers)
    await rl_tracker.update(resp)

    if resp.status_code == 404:
        return None
    if _is_rate_limited(resp.status_code):
        await rl_tracker.acquire_for_core()
        resp = await client.get(url, headers=headers)
        await rl_tracker.update(resp)

    resp.raise_for_status()
    data = resp.json()
    content = data.get("content", "")
    if content:
        return base64.b64decode(content).decode("utf-8", errors="replace")
    return None


def run(config):
    """Main fetcher: parallel search all categories, parallel fetch READMEs."""
    queries = build_queries(config)
    per_page = config["github"]["per_category_limit"]
    sort = config["github"].get("sort", "stars")
    order = config["github"].get("order", "desc")
    token = config["github"].get("token", "").strip() or None

    proxy = config.get("proxy", {})
    proxy_url = proxy.get("https") or proxy.get("http") or None

    if token:
        print("[fetcher] 已配置 GitHub Token")
    if proxy_url:
        print(f"[fetcher] 使用代理: {proxy_url}")

    return asyncio.run(_async_run(queries, per_page, sort, order, token, proxy_url))


async def _async_run(queries, per_page, sort, order, token, proxy_url):
    seen_ids = {}
    results = []
    rl_tracker = RateLimitTracker(authenticated=bool(token))

    limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
    # Use moderate concurrency to avoid hammering the API
    search_sem = asyncio.Semaphore(3)
    readme_sem = asyncio.Semaphore(3 if token else 1)

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, proxy=proxy_url, limits=limits
    ) as client:
        # Phase 1: Parallel search
        print(f"[fetcher] 并行搜索 {len(queries)} 个子查询...")
        tasks = [
            search_github(query, per_page, sort, order, client, token, rl_tracker)
            for _, query in queries
        ]
        all_repos_list = await asyncio.gather(*tasks, return_exceptions=True)

        for (cat_name, _), repos in zip(queries, all_repos_list):
            if isinstance(repos, Exception):
                print(f"[fetcher] 搜索失败 [{cat_name}]: {repos}")
                continue

            print(f"[fetcher]   [{cat_name}] => {len(repos)} 个仓库")
            for repo in repos:
                rid = repo["id"]
                if rid in seen_ids:
                    seen_ids[rid]["category"] += f", {cat_name}"
                    continue

                item = {
                    "id": rid,
                    "full_name": repo["full_name"],
                    "html_url": repo["html_url"],
                    "description": repo.get("description") or "",
                    "stars": repo["stargazers_count"],
                    "language": repo.get("language") or "Unknown",
                    "topics": repo.get("topics", []),
                    "created_at": repo["created_at"],
                    "category": cat_name,
                    "readme": None,
                }
                seen_ids[rid] = item
                results.append(item)

        print(f"[fetcher] 搜索完成，去重后 {len(results)} 个仓库")

        # Phase 2: Parallel README fetch (throttled by RateLimitTracker)
        if results:
            print(f"[fetcher] 并行获取 {len(results)} 个 README...")
            readme_tasks = [
                fetch_readme(item["full_name"], client, token, rl_tracker)
                for item in results
            ]
            readmes = await asyncio.gather(*readme_tasks, return_exceptions=True)

            for item, readme in zip(results, readmes):
                if isinstance(readme, Exception):
                    item["readme"] = None
                else:
                    item["readme"] = readme

            success_count = sum(1 for r in results if r["readme"] is not None)
            print(f"[fetcher] README 获取: {success_count}/{len(results)}")
        else:
            print("[fetcher] 无仓库需获取 README")

    print(f"[fetcher] 完成，共获取 {len(results)} 个新仓库（去重后）")
    return results
