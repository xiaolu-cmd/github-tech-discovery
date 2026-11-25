import unittest

import language_policy


def repo(number, language, score=4):
    return {"id": number, "full_name": str(number), "content_language": language,
            "score": score, "stars": number}


class LanguagePolicyTests(unittest.TestCase):
    def test_selects_seven_chinese_and_three_english(self):
        items = [repo(i, "zh") for i in range(12)]
        items += [repo(i + 20, "en", 5) for i in range(12)]
        result = language_policy.select_recommendations(items)
        self.assertGreater(len(result), 10)
        self.assertAlmostEqual(sum(r["content_language"] == "zh" for r in result) / len(result), .7, delta=.05)
        self.assertEqual({r["id"] for r in result if r["content_language"] == "zh"}, set(range(12)))

    def test_scales_down_instead_of_filling_with_english(self):
        items = [repo(i, "zh") for i in range(5)]
        items += [repo(i + 20, "en") for i in range(15)]
        result = language_policy.select_recommendations(items)
        self.assertEqual(len(result), 7)
        self.assertEqual(sum(r["content_language"] == "en" for r in result), 2)

    def test_english_only_shortage_does_not_expand_english_quota(self):
        result = language_policy.select_recommendations([repo(i, "en") for i in range(12)])
        self.assertEqual(result, [])

    def test_twenty_candidates_are_not_capped_at_ten(self):
        result = language_policy.select_recommendations(
            [repo(i, "zh") for i in range(14)] + [repo(i + 20, "en") for i in range(6)])
        self.assertEqual(len(result), 20)

    def test_english_shortage_reduces_chinese_count_too(self):
        result = language_policy.select_recommendations(
            [repo(i, "zh") for i in range(30)] + [repo(100, "en")])
        self.assertEqual(len(result), 4)

    def test_unknown_language_is_not_counted_as_chinese_or_english(self):
        self.assertEqual(language_policy.select_recommendations([repo(1, "unknown")]), [])

    def test_chinese_documentation_requires_substantive_evidence(self):
        self.assertTrue(language_policy.has_chinese_docs("这是项目的中文使用说明，包含安装配置以及使用方法。" * 5))
        self.assertFalse(language_policy.has_chinese_docs("中文 " + "English installation and usage. " * 100))
        self.assertFalse(language_policy.has_chinese_docs("A useful command line tool."))

    def test_model_cannot_label_english_only_document_as_chinese(self):
        self.assertEqual(language_policy.classify_language({"readme": "English documentation."}, "zh"), "unknown")
        self.assertEqual(language_policy.classify_language({"readme": "English documentation."}, "en"), "en")

    def test_bilingual_documentation_counts_as_chinese_available(self):
        self.assertEqual(language_policy.classify_language({"readme": "安装配置使用说明" * 20}, "en"), "zh")


if __name__ == "__main__":
    unittest.main()
