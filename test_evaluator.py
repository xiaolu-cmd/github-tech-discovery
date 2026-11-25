import json
from types import SimpleNamespace
import unittest

import evaluator


class EvaluatorLanguageTests(unittest.TestCase):
    def evaluate(self, readme, language):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"score": 4, "direction": "实用工具", "reason": "有用", "content_language": language})))])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))
        return evaluator._evaluate_one({"full_name": "o/r", "readme": readme}, client, "test-model")

    def test_english_documentation_keeps_english_classification(self):
        self.assertEqual(self.evaluate("English usage instructions. " * 20, "en")["content_language"], "en")

    def test_chinese_classification_requires_readme_evidence(self):
        self.assertEqual(self.evaluate("English instructions.", "zh")["content_language"], "unknown")
        self.assertEqual(self.evaluate("这是项目中文安装使用说明。" * 20, "zh")["content_language"], "zh")


if __name__ == "__main__":
    unittest.main()
