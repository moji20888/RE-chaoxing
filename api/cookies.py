# -*- coding: utf-8 -*-
import os
import json
import pickle

from api.config import GlobalConst as gc
from api.logger import logger


CREDENTIALS_FILE = "credentials.json"


def read_credentials() -> dict:
    """读取 credentials.json（不存在或损坏时返回空 dict）"""
    if not os.path.exists(CREDENTIALS_FILE):
        return {}
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"读取凭证文件失败: {e}")
        return {}


def write_credentials(data: dict) -> None:
    """写入 credentials.json"""
    try:
        with open(CREDENTIALS_FILE, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"写入凭证文件失败: {e}")


def _migrate_legacy_cookies() -> dict:
    """兼容旧版本：把 cookies.txt 迁移到 credentials.json"""
    if not os.path.exists(gc.COOKIES_PATH):
        return {}
    try:
        with open(gc.COOKIES_PATH, "rb") as f:
            old = pickle.load(f)
        cookies_dict = dict(old) if old else {}
        if cookies_dict:
            data = read_credentials()
            data.setdefault("cookies", cookies_dict)
            write_credentials(data)
            logger.info("检测到旧版 cookies.txt，已自动迁移到 credentials.json")
        try:
            os.remove(gc.COOKIES_PATH)
        except Exception:
            pass
        return cookies_dict
    except Exception as e:
        logger.warning(f"旧版 cookies.txt 迁移失败: {e}")
        return {}


def save_cookies(_session) -> None:
    """保存 cookies 到 credentials.json（不覆盖账号密码）"""
    cookies_dict = _session.cookies.get_dict()
    if not cookies_dict:
        return
    data = read_credentials()
    data["cookies"] = cookies_dict
    write_credentials(data)


def use_cookies():
    """读取 cookies，返回 dict（可直接传给 session.cookies.update）"""
    data = read_credentials()
    cookies = data.get("cookies")
    if isinstance(cookies, dict) and cookies:
        return cookies
    # credentials.json 里没有 cookies 时才尝试迁移旧文件
    migrated = _migrate_legacy_cookies()
    return migrated or None