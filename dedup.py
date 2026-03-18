"""
dedup.py - 跨源去重，MVP 用标题 Jaccard 相似度
"""
import logging
import re

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> set[str]:
    """分词：英文单词 + 中文 bigram（二字组合），提高短文本相似度的区分度"""
    tokens = set()
    # 英文单词
    tokens.update(re.findall(r"[a-zA-Z]+", text.lower()))
    # 中文字符提取
    chars = re.findall(r"[\u4e00-\u9fff]", text)
    # 中文 bigram（相邻两字组合，如"阿里"、"里悟"、"悟空"）
    for i in range(len(chars) - 1):
        tokens.add(chars[i] + chars[i + 1])
    return tokens


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def deduplicate(articles: list[dict], threshold: float = 0.5) -> list[dict]:
    """
    跨源去重：标题 Jaccard 相似度 >= threshold 视为重复。
    同组保留正文最长的一篇。
    """
    if not articles:
        return []

    # 为每篇文章生成标题 token
    tokenized = [(art, _tokenize(art.get("title", ""))) for art in articles]

    # 分组：用 union-find 思路
    n = len(tokenized)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    # 正文前200字 token（用于事件级去重）
    content_tokens = [
        _tokenize((art.get("content", "") or art.get("text", ""))[:200])
        for art, _ in tokenized
    ]

    for i in range(n):
        for j in range(i + 1, n):
            title_sim = _jaccard(tokenized[i][1], tokenized[j][1])
            content_sim = _jaccard(content_tokens[i], content_tokens[j])
            # 标题高度相似 → 直接去重
            # 同事件去重：标题有微弱关联(>=0.15) 且 正文高度相似(>=0.6)
            if title_sim >= threshold or (title_sim >= 0.15 and content_sim >= 0.6):
                union(i, j)

    # 每组保留正文最长的
    groups: dict[int, list[int]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    result = []
    for indices in groups.values():
        if len(indices) == 1:
            result.append(articles[indices[0]])
        else:
            # 保留 content 最长的
            best = max(indices, key=lambda i: len(articles[i].get("content", "") or articles[i].get("text", "")))
            kept = articles[best]
            skipped_titles = [articles[i]["title"][:30] for i in indices if i != best]
            logger.info(f"  Dedup: kept '{kept['title'][:30]}', skipped {skipped_titles}")
            result.append(kept)

    removed = len(articles) - len(result)
    if removed:
        logger.info(f"Dedup: {len(articles)} -> {len(result)} articles ({removed} duplicates)")

    return result
