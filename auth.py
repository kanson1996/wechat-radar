"""
auth.py - 微信公众号平台扫码登录 & token 管理
"""
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

logger = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).parent
TOKEN_FILE = _SCRIPT_DIR / "token.json"

BASE = "https://mp.weixin.qq.com"
HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://mp.weixin.qq.com/",
}


# ──────────────────────────────────────────────
# Token 存取
# ──────────────────────────────────────────────

def load_token() -> Optional[dict]:
    """读取 token.json，返回 dict 或 None（文件不存在 / 格式错误）"""
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Cannot load token.json: {e}")
        return None


def save_token(token: str, cookies: str, expiry_timestamp: int):
    data = {
        "token": token,
        "cookies": cookies,
        "expiry_timestamp": expiry_timestamp,
    }
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Token saved to {TOKEN_FILE}")


def is_token_valid(token_data: Optional[dict]) -> bool:
    """检查 token 是否存在且未过期（提前 10 分钟视为过期）"""
    if not token_data:
        return False
    expiry = token_data.get("expiry_timestamp", 0)
    return time.time() < expiry - 600


# ──────────────────────────────────────────────
# 登录流程
# ──────────────────────────────────────────────

def login() -> bool:
    """
    完整扫码登录流程：
    1. 获取 uuid
    2. 下载二维码并用 macOS Preview 打开
    3. 轮询扫码状态
    4. 获取 token
    5. 保存 token.json
    返回 True 表示登录成功
    """
    session = requests.Session()
    session.headers.update(HEADERS_BASE)

    # Step 1: 获取 uuid
    uuid = _start_login(session)
    if not uuid:
        logger.error("Failed to get login uuid")
        return False
    logger.info(f"Got uuid: {uuid}")

    # Step 2: 下载二维码
    qr_path = _download_qrcode(session, uuid)
    if not qr_path:
        logger.error("Failed to download QR code")
        return False

    # macOS: open 用系统默认查看器打开图片
    print(f"\n请扫描二维码登录微信公众号平台（二维码已保存至 {qr_path}）")
    try:
        subprocess.Popen(["open", str(qr_path)])
    except Exception:
        print(f"  → 无法自动打开，请手动打开: {qr_path}")

    # Step 3: 轮询扫码状态（最多 3 分钟）
    print("等待扫码...", end="", flush=True)
    scan_ok = _poll_scan(session, uuid, timeout=180)
    if not scan_ok:
        logger.error("Scan timeout or failed")
        return False
    print(" 扫码成功！")

    # Step 4: 获取 token
    token, cookies, expiry = _do_login(session, uuid)
    if not token:
        logger.error("Failed to get token after scan")
        return False

    # Step 5: 保存
    save_token(token, cookies, expiry)
    print(f"登录成功，token 已保存（有效期至 {datetime.fromtimestamp(expiry).strftime('%Y-%m-%d %H:%M')}）")
    return True


def _start_login(session: requests.Session) -> Optional[str]:
    """POST bizlogin?action=startlogin → 返回 uuid"""
    try:
        resp = session.post(
            f"{BASE}/cgi-bin/bizlogin",
            params={"action": "startlogin"},
            data={"userlang": "zh_CN", "redirect_url": "", "login_type": "3", "token": "", "lang": "zh_CN"},
            headers={**HEADERS_BASE, "Referer": "https://mp.weixin.qq.com/"},
            timeout=15,
        )
        data = resp.json()
        # uuid 可能在响应体里，也可能在 cookie 里
        return (data.get("uuid")
                or data.get("data", {}).get("uuid")
                or session.cookies.get("uuid"))
    except Exception as e:
        logger.error(f"startlogin error: {e}")
        return None


def _download_qrcode(session: requests.Session, uuid: str) -> Optional[Path]:
    """GET scanloginqrcode?action=getqrcode&uuid=xxx → PNG 保存到 /tmp/wechat_qr.png"""
    try:
        resp = session.get(
            f"{BASE}/cgi-bin/scanloginqrcode",
            params={"action": "getqrcode", "uuid": uuid, "rd": str(int(time.time() * 1000))},
            timeout=15,
        )
        if resp.status_code == 200 and resp.content:
            qr_path = Path(tempfile.gettempdir()) / "wechat_qr.png"
            qr_path.write_bytes(resp.content)
            return qr_path
    except Exception as e:
        logger.error(f"getqrcode error: {e}")
    return None


def _poll_scan(session: requests.Session, uuid: str, timeout: int = 180) -> bool:
    """轮询 scanloginqrcode?action=ask，status==1 表示已扫码确认"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = session.get(
                f"{BASE}/cgi-bin/scanloginqrcode",
                params={"action": "ask", "uuid": uuid, "rd": str(int(time.time() * 1000))},
                timeout=10,
            )
            data = resp.json()
            status = data.get("status") or data.get("data", {}).get("status")
            if status == 1:
                return True
            if status == 2:
                logger.warning("QR code expired, need to restart login")
                return False
        except Exception as e:
            logger.warning(f"poll scan error: {e}")
        print(".", end="", flush=True)
        time.sleep(2)
    return False


def _do_login(session: requests.Session, uuid: str) -> tuple[Optional[str], str, int]:
    """POST bizlogin?action=login → 提取 token + cookies"""
    try:
        resp = session.post(
            f"{BASE}/cgi-bin/bizlogin",
            params={"action": "login"},
            data={"userlang": "zh_CN", "redirect_url": "", "uuid": uuid, "login_type": "3", "token": "", "lang": "zh_CN"},
            timeout=15,
            allow_redirects=False,
        )
        data = resp.json()

        # redirect_url 里包含 token 参数
        redirect_url = data.get("redirect_url", "")
        token = None
        if redirect_url:
            parsed = urlparse(redirect_url)
            qs = parse_qs(parsed.query)
            token = (qs.get("token") or [""])[0]

        if not token:
            # 有时 token 直接在响应体里
            token = str(data.get("token", ""))

        # 拼 cookie 字符串
        cookie_str = "; ".join(f"{c.name}={c.value}" for c in session.cookies)

        # 从 slave_sid cookie 拿过期时间（fallback: 72小时后）
        expiry = _parse_cookie_expiry(session.cookies) or int(time.time() + 72 * 3600)

        return token or None, cookie_str, expiry
    except Exception as e:
        logger.error(f"do_login error: {e}")
        return None, "", 0


def _parse_cookie_expiry(cookies) -> Optional[int]:
    """从 cookie jar 里找 slave_sid 的 expires"""
    for c in cookies:
        if c.name == "slave_sid":
            if hasattr(c, "expires") and c.expires:
                return int(c.expires)
    return None
