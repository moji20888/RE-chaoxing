# -*- coding: utf-8 -*-
import shutil
import sys
import time

from api.config import GlobalConst as gc


def sec2time(sec: int):
    h = int(sec / 3600)
    m = int(sec % 3600 / 60)
    s = int(sec % 60)
    if h != 0:
        return f'{h}:{m:02}:{s:02}'
    if sec != 0:
        return f'{m:02}:{s:02}'
    return '--:--'


# ============ v3.5：进度条输出优化 ============
# 旧版每秒重画 10 次、且任务名很长（中文占两列），整行超过 cmd 窗口宽度后会折行，
# 而 \r 只能回到折行后那一行的行首，于是 cmd 里越堆越长，看起来像一直刷屏。
# 现在：任务名按终端宽度截断、内容没变不重画、限制刷新频率、输出被重定向时只按百分比打行。
_last_text = ""
_last_width = 0
_last_percent = -1
_last_print_ts = 0.0
_min_interval = 0.3          # 最多每 0.3 秒重画一次


def _display_width(text: str) -> int:
    """粗略计算终端显示宽度（中日韩字符按 2 列算）"""
    width = 0
    for ch in text:
        width += 2 if ord(ch) > 0x2E7F else 1
    return width


def _clip(text: str, max_width: int) -> str:
    """按显示宽度截断，超出部分用省略号"""
    if _display_width(text) <= max_width:
        return text
    out = ""
    for ch in text:
        if _display_width(out + ch) + 1 > max_width:
            return out + ""
        out += ch
    return out


def update_progress(task_name: str, current: int, total: int):
    """显示当前任务的进度（视频/音频）"""
    global _last_text, _last_width, _last_percent, _last_print_ts

    total = max(1, int(total or 1))
    current = max(0, min(int(current), total))
    percent = min(int(current / total * 100), 100)

    try:
        columns = shutil.get_terminal_size((80, 25)).columns
    except Exception:
        columns = 80

    bar_length = 20
    head = "当前任务: "
    tail = f" {percent:3d}% {sec2time(current)}/{sec2time(total)}"
    name_width = max(10, columns - _display_width(head) - (bar_length + 2) - _display_width(tail) - 1)
    # 窗口特别窄的时候自动缩短进度条，保证整行不折行
    while bar_length > 6 and (_display_width(head) + name_width + bar_length + 2 + _display_width(tail)) > columns:
        bar_length -= 2
    filled = int(percent * bar_length // 100)
    progress_bar = "#" * filled + "-" * (bar_length - filled)
    name = _clip(str(task_name), name_width)
    line = f"{head}{name} |{progress_bar}|{tail}"

    # 输出被重定向（管道/写文件）时不要用 \r 刷屏：只在百分比变化时打印一行
    try:
        is_tty = sys.stdout.isatty()
    except Exception:
        is_tty = False
    if not is_tty:
        if percent != _last_percent:
            _last_percent = percent
            print(line, flush=True)
        return

    if line == _last_text:
        return
    now = time.time()
    if now - _last_print_ts < _min_interval:
        return
    _last_print_ts = now
    _last_percent = percent

    pad = " " * max(0, _last_width - _display_width(line))   # 抹掉上一次多出来的字符
    _last_text = line
    _last_width = _display_width(line)
    sys.stdout.write("\r" + line + pad)
    sys.stdout.flush()


def finish_progress():
    """任务结束时换行，避免后面的日志接在进度条后面"""
    global _last_text, _last_width, _last_percent
    try:
        if _last_text and sys.stdout.isatty():
            sys.stdout.write("\n")
            sys.stdout.flush()
    except Exception:
        pass
    _last_text = ""
    _last_width = 0
    _last_percent = -1
