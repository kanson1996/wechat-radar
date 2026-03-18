"""
prefilter.py - 规则预过滤，极保守策略只挡明显广告/垃圾
"""
import re
import logging

logger = logging.getLogger(__name__)


_DEFAULT_TECH_WHITELIST = [
    r"GPT|GLM|Claude|Gemini|Llama|Qwen|DeepSeek|Turbo",
    r"大模型|LLM|AI|人工智能|开源|模型|算法|Token|Agent|RAG",
    r"芯片|GPU|CUDA|Transformer|微调|Fine.?tune|Embedding",
]

_DEFAULT_AD_RULES = [
    {"trigger": "限时", "keywords": r"折扣|优惠|特价|秒杀|抢购"},
    {"trigger": "免费领", "keywords": r"课程|资料|电子书|模板|工具包"},
]


def _build_whitelist(patterns: list[str]) -> re.Pattern:
    combined = "|".join(f"({p})" for p in patterns)
    return re.compile(combined, re.IGNORECASE)


def should_skip(title: str, content: str, config: dict = None) -> bool:
    """
    规则预过滤：返回 True 表示跳过该文章。
    极保守策略，只挡组合特征，避免误杀。
    """
    title = title or ""
    content = content or ""
    combined = title + content

    # 从 config 读取自定义规则，否则用默认
    pf_config = (config or {}).get("prefilter", {})
    whitelist_patterns = pf_config.get("tech_whitelist", _DEFAULT_TECH_WHITELIST)
    ad_rules = pf_config.get("ad_rules", _DEFAULT_AD_RULES)

    # 白名单：标题含技术关键词时跳过所有促销规则
    whitelist = _build_whitelist(whitelist_patterns)
    if whitelist.search(title):
        return False

    # 广告关键词组合规则
    for rule in ad_rules:
        trigger = rule.get("trigger", "")
        keywords = rule.get("keywords", "")
        if trigger and keywords and trigger in combined and re.search(keywords, combined):
            logger.info(f"  Prefilter skip ({trigger}+keywords): {title[:40]}")
            return True

    # 规则: 正文极短 + 含多个外链（典型导流文）
    if len(content) < 100:
        url_count = len(re.findall(r"https?://", content))
        if url_count >= 3:
            logger.info(f"  Prefilter skip (短文多链接): {title[:40]}")
            return True

    return False
