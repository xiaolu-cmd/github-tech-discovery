import asyncio
import base64
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import fetcher


class SearchFailureTests(unittest.TestCase):
    def run_search(self, responses):
        with patch.object(fetcher, "search_github", AsyncMock(side_effect=responses)):
            return asyncio.run(fetcher._async_run(
                [("AI", "ai"), ("Security", "security")],
                5, "stars", "desc", "test-token", None,
            ))

    def test_rejected_token_does_not_become_empty_success(self):
        request = httpx.Request("GET", "https://api.github.com/search/repositories")
        response = httpx.Response(401, request=request)
        error = httpx.HTTPStatusError("Unauthorized", request=request, response=response)
        with self.assertRaises(RuntimeError):
            self.run_search([error, error])

    def test_partial_search_failure_is_reported(self):
        with self.assertRaises(RuntimeError):
            self.run_search([[], httpx.ConnectError("Connection failed")])

    def test_successful_empty_search_remains_valid(self):
        self.assertEqual(self.run_search([[], []]), [])


class ChineseDiscoveryTests(unittest.TestCase):
    def test_chinese_keywords_are_searched_alongside_existing_queries(self):
        config = {"github": {"lookback_days": 7}, "categories": [
            {"name": "AI", "queries": ["llm"], "chinese_queries": ["智能体 OR 大模型"]}]}
        queries = fetcher.build_queries(config)
        self.assertEqual(len(queries), 2)
        self.assertTrue(any("智能体" in query for _, query in queries))

    def test_chinese_readme_is_used_when_default_is_english(self):
        chinese = "这是完整的中文安装使用说明，包括配置步骤和常见问题。" * 10
        requested = []
        def respond(request):
            requested.append(request.url.path)
            if request.url.path.endswith("/readme"):
                text = "English installation instructions. " * 20
                return httpx.Response(200, json={"content": base64.b64encode(text.encode()).decode()})
            if request.url.path.endswith("/contents"):
                return httpx.Response(200, json=[{"name": "README.zh-CN.md", "path": "README.zh-CN.md", "type": "file"}])
            return httpx.Response(200, json={"content": base64.b64encode(chinese.encode()).decode()})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                return await fetcher.fetch_readme("owner/repo", client, "test", fetcher.RateLimitTracker(True))
        self.assertEqual(asyncio.run(run()), chinese)
        self.assertIn("/repos/owner/repo/contents/README.zh-CN.md", requested)


if __name__ == "__main__":
    unittest.main()
