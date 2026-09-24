# -*- coding: utf-8 -*-
class GlobalConst:
    AESKey = "u2oh6Vu^HWe4_AES"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Sec-Ch-Ua": '"Chromium";v="118", "Google Chrome";v="118", "Not=A?Brand";v="99"'
    }
    COOKIES_PATH = "cookies.txt"
    VIDEO_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/video/index.html?v=2023-1110-1610",
        "Host": "mooc1.chaoxing.com"
    }
    AUDIO_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        "Referer": "https://mooc1.chaoxing.com/ananas/modules/audio/index_new.html?v=2023-0428-1705",
        "Host": "mooc1.chaoxing.com"
    }
    THRESHOLD = 0.1

import random
import re


def parse_wait_spec(spec, default_fixed: float = 5.0):
    """把等待间隔设置解析成 (最小秒, 最大秒)。

    支持三种写法：
      * 数字       5         固定 5 秒
      * 字符串范围 "3-8"     在 3~8 秒之间随机（也认 "3~8" / "3 至 8"）
      * 列表/元组  [3, 8]    同上
    """
    try:
        if isinstance(spec, (list, tuple)) and len(spec) >= 2:
            low, high = float(spec[0]), float(spec[1])
        elif isinstance(spec, str) and re.search(r"[-~至,，\s]", spec.strip()):
            _nums = [float(x) for x in re.split(r"[-~至,，\s]+", spec.strip()) if x]
            if len(_nums) < 2:
                low = high = _nums[0]
            else:
                low, high = _nums[0], _nums[1]
        else:
            low = high = float(spec)
        if low > high:
            low, high = high, low
        return max(0.0, low), max(0.0, high)
    except Exception:
        return float(default_fixed), float(default_fixed)


def pick_wait(spec, default_fixed: float = 5.0) -> float:
    """按设置返回一个等待秒数：固定值就直接返回，范围就随机取（随机数带小数，更不规律）"""
    low, high = parse_wait_spec(spec, default_fixed)
    if high <= low:
        return low
    return random.uniform(low, high)


def wait_spec_text(spec, default_fixed: float = 5.0) -> str:
    """给菜单/日志显示用：固定值显示成 5，范围显示成 3~8（随机）"""
    low, high = parse_wait_spec(spec, default_fixed)
    if high <= low:
        return ("%g" % low)
    return "%g~%g（随机）" % (low, high)
