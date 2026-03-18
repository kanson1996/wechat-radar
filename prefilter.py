"""
prefilter.py - 规则预过滤，极保守策略只挡明显广告/垃圾
"""
import re
import logging

logger = logging.getLogger(__name__)


TECH_WHITELIST = re.compile(
    r"(GPT|GLM|Claude|Gemini|Llama|Qwen|DeepSeek|Turbo|"
    r"大模型|LLM|AI|人工智能|开源|模型|算法|Token|Agent|RAG|"
    r"芯片|GPU|CUDA|Transformer|微调|Fine.?tune|Embedding)",
    re.IGNORECASE,
)


def should_skip(title: str, content: str) -> bool:
    """
    规则预过滤：返回 True 表示跳过该文章。
    极保守策略，只挡组合特征，避免误杀。
    """
    title = title or ""
    content = content or ""
    combined = title + content

    # 白名单：标题含 AI/技术关键词时跳过所有促销规则
    if TECH_WHITELIST.search(title):
        return False

    # 规则1: "限时" + 促销词
    if "限时" in combined and re.search(r"(折扣|优惠|特价|秒杀|抢购)", combined):
        logger.info(f"  Prefilter skip (限时促销): {title[:40]}")
        return True

    # 规则2: "免费领" + 引流词
    if "免费领" in combined and re.search(r"(课程|资料|电子书|模板|工具包)", combined):
        logger.info(f"  Prefilter skip (免费领引流): {title[:40]}")
        return True

    # 规则3: 正文极短 + 含多个外链（典型导流文）
    if len(content) < 100:
        url_count = len(re.findall(r"https?://", content))
        if url_count >= 3:
            logger.info(f"  Prefilter skip (短文多链接): {title[:40]}")
            return True

    return False
