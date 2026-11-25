"""Select by documentation language, independently of programming language."""

import re


def has_chinese_docs(text):
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    return chinese >= 40 and chinese / max(chinese + latin, 1) >= 0.15


def classify_language(item, model_language):
    if has_chinese_docs(item.get("readme", "")):
        return "zh"
    return "en" if model_language == "en" else "unknown"


def select_recommendations(items, chinese_ratio=0.7):
    """Maximize count within five percentage points of the requested ratio.

    There is no fixed count cap. Unknown languages are excluded. When no
    feasible allocation exists, the caller should report the shortage.
    """
    if not 0 < chinese_ratio < 1:
        raise ValueError("chinese_ratio must be between 0 and 1")
    rank = lambda r: (r["score"], r["stars"])
    chinese = sorted((r for r in items if r.get("content_language") == "zh"), key=rank, reverse=True)
    english = sorted((r for r in items if r.get("content_language") == "en"), key=rank, reverse=True)
    for total in range(len(chinese) + len(english), 1, -1):
        zh_count = int(total * chinese_ratio + 0.5)
        en_count = total - zh_count
        if not (0 < zh_count <= len(chinese) and 0 < en_count <= len(english)):
            continue
        if abs(zh_count / total - chinese_ratio) <= 0.05000001:
            return sorted(chinese[:zh_count] + english[:en_count], key=rank, reverse=True)
    return []
