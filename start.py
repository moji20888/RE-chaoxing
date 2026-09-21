# -*- coding: utf-8 -*-
"""启动器（v3.5）

把原来 start.bat 里那堆逻辑搬到 Python 里，好处：
  * .bat 只留几行，不怕换行/编码/代码页变化导致的闪退；
  * 中文提示由 Python 输出，任何 Windows 语言都能正常显示；
  * 出错时能给出明确原因，并且窗口不会一闪而过（start.bat 里有 pause）。
做的事情：找/建虚拟环境 -> 装依赖 -> 检查 Playwright 内核 -> 运行 main.py
"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

VENV_NAMES = ("venv", ".venv", "env", ".env")
REQUIRED_IMPORTS = "requests, bs4, lxml, loguru, playwright, ddddocr, fontTools, pyaes, urllib3"
DEVNULL = subprocess.DEVNULL


def say(msg=""):
    print(msg, flush=True)


# 这几行会在 main.py 里一直保留在屏幕顶部（切菜单时清屏也不丢）
BANNER = []


def banner(msg):
    BANNER.append(msg)
    say(msg)


def find_venv_python():
    """按 venv/.venv/env/.env 顺序找虚拟环境里的 python.exe"""
    for name in VENV_NAMES:
        p = os.path.join(BASE, name, "Scripts", "python.exe")
        if os.path.exists(p):
            return p
    return None


def find_system_python():
    """找一个可用的系统 Python，返回命令列表（如 ['py', '-3']）"""
    for cmd in (["py", "-3"], ["python"], ["python3"]):
        try:
            r = subprocess.run(cmd + ["-V"], stdout=DEVNULL, stderr=DEVNULL, timeout=30)
        except Exception:
            continue
        if r.returncode == 0:
            return cmd
    return None


def run(py, args, **kw):
    """执行命令，出错返回 None 并打印原因"""
    try:
        return subprocess.run([py] + args, **kw)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        say(f"[ERROR] 执行失败: {py} {' '.join(args)} -> {e}")
        return None


def create_venv():
    cmd = find_system_python()
    if not cmd:
        say("[ERROR] 没有找到可用的 Python。")
        say("        请先安装 Python 3.9 或更高版本，安装时记得勾选 Add python.exe to PATH，")
        say("        然后重新运行 start.bat。")
        return None
    venv_dir = os.path.join(BASE, ".venv")
    say(f"[INFO] 正在用系统 Python 创建虚拟环境: {' '.join(cmd)}")
    r = run(cmd[0], cmd[1:] + ["-m", "venv", venv_dir], timeout=600)
    py = os.path.join(venv_dir, "Scripts", "python.exe")
    if os.path.exists(py):
        if r is not None and r.returncode != 0:
            say("[WARN] venv 命令返回了非 0（常见于 ensurepip 出错），继续尝试使用已建好的虚拟环境 ...")
        say(f"[INFO] 虚拟环境已创建: {venv_dir}")
        return py
    say("[ERROR] 创建虚拟环境失败。可以手动执行下面这条命令，然后重新运行 start.bat：")
    say(f"        {' '.join(cmd)} -m venv .venv")
    return None


def ensure_pip(py):
    r = run(py, ["-m", "pip", "--version"], stdout=DEVNULL, stderr=DEVNULL)
    if r is not None and r.returncode == 0:
        return True
    say("[INFO] 虚拟环境里没有 pip，正在初始化 ...")
    r = run(py, ["-m", "ensurepip", "--upgrade"])
    return r is not None and r.returncode == 0


def check_deps(py):
    r = run(py, ["-c", f"import {REQUIRED_IMPORTS}"], stdout=DEVNULL, stderr=DEVNULL)
    return r is not None and r.returncode == 0


def install_deps(py):
    if not os.path.exists(os.path.join(BASE, "requirements.txt")):
        say("[ERROR] 没找到 requirements.txt，请把它一起复制过来。")
        return False
    say("[INFO] 正在安装依赖（第一次大约 100~200MB，请耐心等待）...")
    ok = run(py, ["-m", "pip", "install", "--no-cache-dir", "-r", "requirements.txt"])
    if ok is None or ok.returncode != 0:
        say("[INFO] 默认源安装失败，改用清华镜像重试 ...")
        ok = run(py, ["-m", "pip", "install", "--no-cache-dir", "-r", "requirements.txt",
                      "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"])
    return ok is not None and ok.returncode == 0


def ensure_playwright(py):
    check = ("from playwright.sync_api import sync_playwright; "
             "p=sync_playwright().start(); b=p.chromium.launch(); b.close(); p.stop()")
    r = run(py, ["-c", check], stdout=DEVNULL, stderr=DEVNULL)
    if r is not None and r.returncode == 0:
        return True
    say("[INFO] Playwright 浏览器内核缺失，正在下载（约 150MB，请耐心等待）...")
    env = os.environ.copy()
    env.setdefault("PLAYWRIGHT_DOWNLOAD_HOST", "https://npmmirror.com/mirrors/playwright")
    r = run(py, ["-m", "playwright", "install", "chromium"], env=env)
    return r is not None and r.returncode == 0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    say(f"[INFO] 工作目录: {BASE}")

    # 必要文件检查
    must = [("main.py", "请把 start.bat / start.py 和 main.py 放在同一个文件夹里"),
            (os.path.join("api", "base.py"), "请把 api、resource 两个文件夹一起复制过来"),
            (os.path.join("resource", "font_map_table.json"), "请把 resource 文件夹一起复制过来")]
    missing = False
    for rel, hint in must:
        if not os.path.exists(os.path.join(BASE, rel)):
            say(f"[ERROR] 找不到 {rel}：{hint}")
            missing = True
    if missing:
        return 1

    py = find_venv_python()
    if py:
        banner(f"[INFO] 使用已有的虚拟环境: {os.path.relpath(py, BASE)}")
        if not check_deps(py):
            ensure_pip(py)
            install_deps(py)
    else:
        py = create_venv()
        if not py:
            return 1
        ensure_pip(py)
        install_deps(py)

    if not check_deps(py):
        say("[ERROR] 依赖仍然不完整，请手动执行下面这条命令后重试：")
        say(f'        "{py}" -m pip install --no-cache-dir -r requirements.txt')
        return 1
    banner("[INFO] 依赖检查通过")

    ensure_playwright(py)

    banner("[INFO] 启动 main.py ...")
    banner("-" * 46)
    env = os.environ.copy()
    env["CX_LAUNCH_BANNER"] = "\n".join(BANNER)
    r = run(py, ["main.py"], env=env)
    if r is None:
        return 1
    return r.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("")
        say("[INFO] 已被用户中断")
        sys.exit(0)
