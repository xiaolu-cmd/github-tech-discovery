from datetime import datetime
import unittest
from unittest.mock import patch

import notifier


class LanguageSummaryTests(unittest.TestCase):
    def test_actual_language_counts_are_visible(self):
        items = [{"full_name": str(i), "html_url": "https://github.com/o/r",
                  "score": 4, "stars": 20, "content_language": "zh" if i < 7 else "en"}
                 for i in range(10)]
        text = notifier.build_markdown(items, datetime(2026, 11, 25), {"zh": 7, "en": 12, "ratio": .7})
        self.assertIn("中文 7 个 / 英文 3 个", text)
        self.assertIn("文档：中文", text)
        self.assertIn("文档：英文", text)

    def test_shortage_still_sends_an_explanation(self):
        config = {"webhook": {"type": "feishu", "url": "https://example.invalid/hook"}}
        with patch.object(notifier, "send_feishu") as send:
            with patch.dict(notifier.SENDERS, {"feishu": send}):
                notifier.run([], config, {"zh": 0, "en": 10, "ratio": .7})
            send.assert_called_once()
            self.assertIn("不足", send.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
