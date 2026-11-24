import asyncio
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


if __name__ == "__main__":
    unittest.main()
