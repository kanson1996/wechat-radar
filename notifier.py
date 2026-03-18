"""
notifier.py - 推送到飞书和 Gmail（支持开场白、category 标签、维度评分）
"""
import logging
import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 飞书推送
# ──────────────────────────────────────────────

def send_feishu(articles: list[dict], intro: str = "") -> bool:
    """发送飞书卡片消息（批量，一条消息汇总所有推荐文章）"""
    webhook = os.getenv("FEISHU_WEBHOOK", "")
    if not webhook:
        logger.warning("FEISHU_WEBHOOK not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send to Feishu")
        return True

    card = _build_feishu_card(articles, intro)
    try:
        resp = requests.post(webhook, json=card, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0 or result.get("StatusCode") == 0:
            logger.info(f"Feishu sent: {len(articles)} articles")
            return True
        else:
            logger.error(f"Feishu API error: {result}")
            return False
    except Exception as e:
        logger.error(f"Feishu send failed: {e}")
        return False


def _build_feishu_card(articles: list[dict], intro: str = "") -> dict:
    """构建飞书消息卡片（interactive card 格式）"""
    date_str = _today_str()
    elements = []

    # 标题
    elements.append({
        "tag": "markdown",
        "content": f"**📰 微信公众号日报 · {date_str}**\n共 {len(articles)} 篇推荐"
    })

    # 开场白
    if intro:
        elements.append({
            "tag": "markdown",
            "content": f"\n{intro}"
        })

    elements.append({"tag": "hr"})

    for i, art in enumerate(articles, 1):
        # 封面图暂不支持（飞书需要先上传获取 img_key，后续实现）

        # 文章内容块
        summary = art.get("summary", "")
        reason = art.get("reason", "")
        account = art.get("account_name", "")
        url = art.get("url", "")
        category = art.get("category", "")
        tags = art.get("tags", [])
        scores = art.get("scores", {})
        final_score = art.get("final_score", 0)

        content_lines = [
            f"**{i}. [{category}] {art['title']}**" if category else f"**{i}. {art['title']}**",
            f"来源：{account}" + (f"　|　综合分：{final_score:.1f}" if final_score else ""),
        ]
        if tags:
            content_lines.append(f"标签：{'、'.join(tags)}")
        if summary:
            content_lines.append(f"\n{summary}")
        if reason:
            content_lines.append(f"\n💡 {reason}")
        if scores:
            score_text = " | ".join(
                f"{k}:{v}" for k, v in scores.items()
            )
            content_lines.append(f"\n<font color='grey'>📊 {score_text}</font>")

        elements.append({
            "tag": "markdown",
            "content": "\n".join(content_lines)
        })

        if url:
            elements.append({
                "tag": "action",
                "actions": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "阅读原文"},
                    "url": url,
                    "type": "default",
                }]
            })

        if i < len(articles):
            elements.append({"tag": "hr"})

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"微信公众号日报 · {date_str}"},
                "template": "blue"
            },
            "elements": elements
        }
    }


# ──────────────────────────────────────────────
# Gmail 推送（SMTP）
# ──────────────────────────────────────────────

def send_gmail(articles: list[dict], intro: str = "") -> bool:
    """通过 Gmail SMTP 发送 HTML 日报"""
    gmail_user = os.getenv("GMAIL_USER", "")
    app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    gmail_to = os.getenv("GMAIL_TO", gmail_user)

    if not gmail_user or not app_password:
        logger.warning("GMAIL_USER or GMAIL_APP_PASSWORD not set, skipping")
        return False

    if not articles:
        logger.info("No articles to send via Gmail")
        return True

    date_str = _today_str()
    subject = f"微信公众号日报 · {date_str}（{len(articles)} 篇推荐）"
    html_body = _build_gmail_html(articles, date_str, intro)

    recipients = [addr.strip() for addr in gmail_to.split(",") if addr.strip()]

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipients)

    # HTML 部分
    msg_alt = MIMEMultipart("alternative")
    msg_alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(msg_alt)

    # 内嵌 banner 图片
    banner_path = Path(__file__).parent / "assets" / "banner.png"
    if banner_path.exists():
        with open(banner_path, "rb") as f:
            banner_img = MIMEImage(f.read(), _subtype="png")
            banner_img.add_header("Content-ID", "<banner>")
            banner_img.add_header("Content-Disposition", "inline", filename="banner.png")
            msg.attach(banner_img)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, app_password)
            server.sendmail(gmail_user, recipients, msg.as_string())
        logger.info(f"Gmail sent to {recipients}: {len(articles)} articles")
        return True
    except Exception as e:
        logger.error(f"Gmail send failed: {e}")
        return False


def _build_gmail_html(articles: list[dict], date_str: str, intro: str = "") -> str:
    # 卷首语
    intro_html = ""
    if intro:
        intro_html = f"""
        <div style="margin:32px 0;padding:20px 24px;border-left:3px solid #333;background:#fafafa;">
            <p style="margin:0;color:#333;font-size:15px;line-height:1.9;">{intro}</p>
        </div>
        """

    # 本期概要（按 category 分组列标题）
    from collections import OrderedDict
    toc_groups: dict[str, list] = OrderedDict()
    for art in articles:
        cat = art.get("category", "其他")
        toc_groups.setdefault(cat, []).append(art["title"])

    toc_items = ""
    for cat, titles in toc_groups.items():
        toc_items += f"<li style='margin-bottom:4px;color:#666;font-size:13px;'><strong>{cat}</strong>：{'、'.join(t[:20] for t in titles)}</li>"

    toc_html = f"""
    <div style="margin:24px 0 32px;padding:16px 20px;background:#f8f9fa;border-radius:6px;">
        <p style="margin:0 0 8px;font-size:14px;font-weight:bold;color:#333;">本期概要</p>
        <ul style="margin:0;padding-left:20px;list-style:disc;">{toc_items}</ul>
    </div>
    """

    # 按 category 分区展示文章
    cat_groups: dict[str, list] = OrderedDict()
    for art in articles:
        cat = art.get("category", "其他")
        cat_groups.setdefault(cat, []).append(art)

    items_html = ""
    article_idx = 0
    for cat, cat_articles in cat_groups.items():
        items_html += f"""
        <h2 style="margin:36px 0 16px;padding-bottom:8px;border-bottom:1px solid #ddd;
                    font-size:18px;color:#333;font-weight:bold;">{cat}</h2>
        """
        for art in cat_articles:
            article_idx += 1
            summary = art.get("summary", "")
            reason = art.get("reason", "")
            url = art.get("url", "#")
            account = art.get("account_name", "")
            tags = art.get("tags", [])
            scores = art.get("scores", {})
            final_score = art.get("final_score", 0)

            tags_html = " ".join(
                f'<span style="background:#f1f3f4;color:#555;padding:2px 6px;border-radius:3px;font-size:11px;">#{t}</span>'
                for t in tags
            ) if tags else ""

            scores_html = ""
            if scores:
                score_items = " · ".join(f"{k}:{v}" for k, v in scores.items())
                scores_html = f'<p style="margin:4px 0 0;color:#999;font-size:11px;">📊 {score_items}</p>'

            cover = art.get("cover", "")
            thumb_cell = f'<td style="width:60px;vertical-align:top;padding-left:12px;"><img src="{cover}" style="width:60px;height:60px;object-fit:cover;border-radius:6px;display:block;" /></td>' if cover and cover.startswith("http") else '<td style="width:60px;"></td>'

            items_html += f"""
            <div style="margin-bottom:32px;">
                <table style="width:100%;border-collapse:collapse;"><tr>
                    <td style="vertical-align:top;">
                        <h3 style="margin:0 0 6px;font-size:17px;line-height:1.5;">
                            <a href="{url}" style="color:#1a73e8;text-decoration:none;">{art['title']}</a>
                        </h3>
                    </td>
                    {thumb_cell}
                </tr></table>
                <p style="margin:0 0 8px;color:#999;font-size:12px;">
                    来源：{account}{'　|　综合分：%.1f' % final_score if final_score else ''}
                </p>
                {f'<p style="margin:0 0 8px;">{tags_html}</p>' if tags_html else ''}
                {"<p style='margin:0 0 8px;color:#333;font-size:14px;line-height:1.8;'>" + summary + "</p>" if summary else ""}
                {"<p style='margin:0 0 4px;color:#555;font-size:13px;line-height:1.8;'>💡 " + reason + "</p>" if reason else ""}
                {scores_html}
            </div>
            """

    return f"""
    <html><body style="font-family:-apple-system,'Helvetica Neue',sans-serif;max-width:620px;margin:0 auto;padding:40px 20px;color:#333;">
        <div style="text-align:center;margin-bottom:24px;">
            <img src="cid:banner" style="max-width:100%;border-radius:8px;" alt="AI 日报" />
        </div>
        <p style="text-align:center;color:#999;font-size:14px;margin-bottom:8px;">{date_str}　·　共 {len(articles)} 篇推荐</p>
        {intro_html}
        {toc_html}
        {items_html}
        <hr style="border:none;border-top:1px solid #eee;margin:40px 0 16px;" />
        <p style="text-align:center;color:#bbb;font-size:12px;">由 wechat-digest 自动生成</p>
    </body></html>
    """


def _get_hero_image(articles: list[dict]) -> str:
    """取第一篇有封面图的文章作为整期 newsletter 的头图"""
    for art in articles:
        cover = art.get("cover", "")
        if cover and cover.startswith("http"):
            return f'<img src="{cover}" style="max-width:100%;border-radius:8px;margin-bottom:24px;display:block;" />'
    return ""


def _today_str() -> str:
    from datetime import datetime, timezone, timedelta
    cst = timezone(timedelta(hours=8))
    return datetime.now(cst).strftime("%Y-%m-%d")
