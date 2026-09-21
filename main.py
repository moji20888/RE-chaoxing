# -*- coding: utf-8 -*-
import argparse
import configparser
import requests
import json
import os
import time
import random
import re
import sys
import base64
import signal
import threading
from urllib3 import disable_warnings, exceptions
from playwright.sync_api import sync_playwright

from api.logger import logger
from api.base import Chaoxing, Account
from api.exceptions import LoginError, FormatError, JSONDecodeError, MaxRollBackError
from api.cookies import use_cookies, read_credentials, write_credentials
from api.config import GlobalConst as gc
from api.live import Live
from api.live_process import LiveProcessor
from deepseek_api import DeepSeekAnswer

disable_warnings(exceptions.InsecureRequestWarning)


# ==================== 中断处理 ====================
_stop_requested = threading.Event()


def _install_sigint_handler():
    """安装 Ctrl+C 处理器：第一次优雅退出，第二次/超时强退"""
    def _handler(signum, frame):
        if _stop_requested.is_set():
            print("\n再次收到 Ctrl+C，强制退出")
            _cleanup_all_silent()
            os._exit(1)
        print("\n收到 Ctrl+C，正在停止当前任务并清理资源...")
        print("（如 5 秒内未退出，请再按一次 Ctrl+C 强制退出）")
        _stop_requested.set()

        def _force():
            time.sleep(5)
            print("清理超时，强制退出")
            _cleanup_all_silent()
            os._exit(1)
        threading.Thread(target=_force, daemon=True).start()

    try:
        signal.signal(signal.SIGINT, _handler)
    except Exception as e:
        logger.warning(f"安装 Ctrl+C 处理器失败: {e}")


def _cleanup_all_silent():
    """退出时尝试清理 Playwright，失败也无所谓（OS 会回收）"""
    global _pw_instance, _browser_instance
    try:
        if _browser_instance:
            _browser_instance.close()
    except Exception:
        pass
    try:
        if _pw_instance:
            _pw_instance.stop()
    except Exception:
        pass
    _pw_instance = None
    _browser_instance = None


# ==================== Playwright 浏览器自动安装 ====================
def ensure_playwright_browser():
    """检测并自动安装 Playwright 浏览器内核"""
    import subprocess
    try:
        if "PLAYWRIGHT_DOWNLOAD_HOST" not in os.environ:
            os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = "https://npmmirror.com/mirrors/playwright"

        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=30
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 or "not installed" in output.lower() or "doesn't exist" in output.lower():
            print("正在下载 Playwright 浏览器内核，首次运行可能需要几分钟...")
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True, timeout=600
            )
            print("浏览器内核安装完成！")
    except subprocess.TimeoutExpired:
        print("浏览器安装超时，请手动运行: playwright install chromium")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"浏览器安装失败，请手动运行: playwright install chromium\n错误: {e}")
        sys.exit(1)


# ==================== 凭证管理 ====================
class CredentialManager:
    CRED_FILE = "credentials.json"

    # ---------- 账号密码 ----------
    @staticmethod
    def save_account(username, password):
        data = read_credentials()
        data["username"] = base64.b64encode(username.encode()).decode()
        data["password"] = base64.b64encode(password.encode()).decode()
        write_credentials(data)

    @staticmethod
    def load_account():
        data = read_credentials()
        try:
            username = base64.b64decode(data["username"].encode()).decode()
            password = base64.b64decode(data["password"].encode()).decode()
            return username, password
        except Exception:
            return None, None

    @staticmethod
    def has_account():
        u, p = CredentialManager.load_account()
        return bool(u and p)

    @staticmethod
    def clear_account():
        data = read_credentials()
        data.pop("username", None)
        data.pop("password", None)
        write_credentials(data)

    # ---------- cookies ----------
    @staticmethod
    def has_cookies():
        data = read_credentials()
        cookies = data.get("cookies")
        return isinstance(cookies, dict) and bool(cookies)

    @staticmethod
    def clear_cookies():
        data = read_credentials()
        data.pop("cookies", None)
        write_credentials(data)

    # ---------- 全部清除 ----------
    @staticmethod
    def clear_all():
        if os.path.exists(CredentialManager.CRED_FILE):
            try:
                os.remove(CredentialManager.CRED_FILE)
            except Exception:
                pass
        if os.path.exists(gc.COOKIES_PATH):
            try:
                os.remove(gc.COOKIES_PATH)
            except Exception:
                pass


# ==================== API 密钥管理 ====================
API_KEY_FILE = "api_key.json"

SETTINGS_FILE = "settings.json"


def load_settings():
    default = {
        "enable_thinking": False,
        "locked_chapter_policy": "auto",   # 未开放(闯关)章节处理策略: auto / skip / rollback
        "locked_retry_times": 2,           # 未开放章节重试次数
        "locked_retry_interval": 5,        # 未开放章节重试间隔(秒)
        "locked_second_pass_rounds": 5,    # 收尾复查轮数（0 = 关闭）
        "max_rollback_times": 3,           # 同一章节最多回滚次数
        "unlock_check_times": 4,           # 访问章节后，等待/复查下一章解锁的次数
        "unlock_check_interval": 5,        # 每次复查的间隔（秒）
        "sweep_wait_seconds": 180,         # 收尾复查每轮之间等待的秒数（等服务端解锁/判定）
    }
    if not os.path.exists(SETTINGS_FILE):
        return default
    try:
        with open(SETTINGS_FILE, "r", encoding="utf8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        return {**default, **data}
    except Exception:
        return default


def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存设置失败: {e}")

        
def load_api_key():
    if not os.path.exists(API_KEY_FILE):
        return None
    try:
        with open(API_KEY_FILE, "r") as f:
            data = json.load(f)
            return data.get("api_key")
    except Exception:
        return None


def save_api_key(api_key):
    with open(API_KEY_FILE, "w") as f:
        json.dump({"api_key": api_key}, f)


def clear_api_key():
    if os.path.exists(API_KEY_FILE):
        os.remove(API_KEY_FILE)


# ==================== 回滚管理器 ====================
class RollBackManager:
    """记录每个章节的回滚次数；超过上限时抛 MaxRollBackError（v3.5：次数 = 允许回滚的次数）"""

    def __init__(self, max_times: int = 3):
        self._counts = {}
        self._max = max_times

    def times(self, point_id) -> int:
        """返回该章节已经回滚过几次"""
        return self._counts.get(point_id, 0)

    def add_times(self, point_id):
        current = self._counts.get(point_id, 0) + 1
        self._counts[point_id] = current
        if current > self._max:
            raise MaxRollBackError(f"point {point_id} reached max rollback {self._max}")


# ==================== Playwright 全局单例 ====================
_pw_instance = None
_browser_instance = None


def _get_playwright():
    global _pw_instance
    if _pw_instance is None:
        _pw_instance = sync_playwright().start()
    return _pw_instance


def _get_browser():
    global _browser_instance
    if _browser_instance is None or not _browser_instance.is_connected():
        _browser_instance = _get_playwright().chromium.launch(
            headless=True,
            args=['--autoplay-policy=no-user-gesture-required']
        )
    return _browser_instance


def new_browser_context():
    """创建一个新的浏览器上下文（共享同一个 browser）"""
    browser = _get_browser()
    return browser.new_context(
        viewport={'width': 1280, 'height': 800},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )


def close_browser_context(context):
    """关闭上下文，不关 browser"""
    try:
        if context:
            context.close()
    except Exception as e:
        logger.debug(f"关闭 browser context 失败: {e}")

# ==================== 课程选择 ====================
def get_course_selection(chaoxing):
    """获取用户选择的课程列表（输入顺序即刷课顺序）"""
    all_course = chaoxing.get_course_list()
    print("*" * 10 + "课程列表" + "*" * 10)
    for idx, course in enumerate(all_course, 1):
        print(f"[{idx}] ID: {course['courseId']} 班级: {course['clazzId']} 课程名: {course['title']}")
    print("*" * 28)
    print("提示: 输入课程ID，顺序即刷课顺序；直接回车则按默认顺序学习全部课程。")
    print("      多个ID支持这些分隔符：英文逗号 , / 中文逗号 ， / 顿号 、 / 分号 ; / 空格，例如：")
    print("      123456,234567   123456，234567   123456、234567   123456 234567")

    while True:
        try:
            course_input = input("请输入想要学习的课程ID（直接回车 = 全部课程）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消，改为学习全部课程。")
            return all_course

        if not course_input:
            return all_course

        # 英文逗号 / 中文逗号 / 顿号 / 分号 / 竖线 / 斜杠 / 空格(含Tab、换行) 都当分隔符
        course_ids = [cid for cid in re.split(r"[,，、;；|/\s]+", course_input) if cid]

        course_task = []
        seen_keys = set()
        not_found = []
        for cid in course_ids:
            matched = False
            for course in all_course:
                key = (course["courseId"], course["clazzId"])
                if course["courseId"] == cid:
                    matched = True
                    if key not in seen_keys:
                        course_task.append(course)
                        seen_keys.add(key)
            if not matched:
                not_found.append(cid)

        if course_task:
            if not_found:
                print(f"提示: 这些ID在课程列表里没找到，已经忽略 -> {'、'.join(not_found)}")
            return course_task

        print(f"没有匹配到任何课程（你输入的是: {'、'.join(course_ids)}）。请重新输入；")
        print("如果就是想学习全部课程，直接按回车即可。")


# ==================== 章节未开放（闯关模式）处理 ====================
# 超星部分课程开启了「闯关模式」：要先完成上一章的闯关任务点，下一章才会解锁，
# 这类章节的任务点接口会返回「章节未开放」提示页。
# 旧版(3.4)遇到这种情况只会一直回滚到上一章，最后报「回滚次数已达3次」并结束刷课。
# 3.5 的处理逻辑：
#   1. 章节列表里被标记成「闯关未解锁」的，说明确实还没解锁，直接进入下面的判断；
#   2. 没被标记的，先等几秒重试几次（有些章节刚解锁，服务端存在延迟）；
#   3. auto 策略：上一章还有没刷完的任务点 -> 回滚上一章去刷（很可能就是它卡住了解锁）；
#      上一章已经刷完了 -> 回滚也解锁不了，直接跳过这一章，继续往后刷；
#   4. 整门课刷完后（默认开启），重新读一次章节列表，把期间解锁的章节补刷一遍。
LOCKED_POLICY_AUTO = "auto"          # 自动判断（默认）
LOCKED_POLICY_SKIP = "skip"          # 直接跳过未开放章节
LOCKED_POLICY_ROLLBACK = "rollback"  # 尽量回滚上一章尝试解锁（旧版行为）

LOCKED_POLICY_NAME = {
    LOCKED_POLICY_AUTO: "自动判断（推荐）",
    LOCKED_POLICY_SKIP: "直接跳过未开放章节",
    LOCKED_POLICY_ROLLBACK: "回滚上一章尝试解锁",
}


def sleep_interruptible(seconds: float) -> bool:
    """可被 Ctrl+C 打断的等待；返回 True 表示收到了停止信号"""
    deadline = time.time() + max(0.0, seconds)
    while time.time() < deadline:
        if _stop_requested.is_set():
            return True
        time.sleep(0.2)
    return _stop_requested.is_set()


def decide_not_open_action(point, prev_jobs_count, policy, rollback_times, max_rollback,
                           can_rollback=True, can_submit=True):
    """
    章节未开放时决定下一步动作，返回 (action, reason)
      action = "rollback" 回滚到上一章去刷（尝试解锁本章）
      action = "skip"     跳过本章，继续下一章
    """
    reason = "闯关模式未解锁" if point.get("locked") else "服务端返回「章节未开放」"

    if policy == LOCKED_POLICY_SKIP:
        return "skip", reason + "（按设置直接跳过）"

    if not can_rollback:
        return "skip", reason + "（已经是第一章，无法回滚）"
    if not can_submit:
        return "skip", reason + "（题库未开启，无法自动刷上一章来解锁）"
    if rollback_times >= max_rollback:
        return "skip", reason + "（回滚已达 %d 次）" % max_rollback

    if policy == LOCKED_POLICY_ROLLBACK:
        return "rollback", reason + "（按设置回滚上一章尝试解锁）"

    # auto：只有上一章确实还有没刷完的任务点时，回滚才有意义
    if prev_jobs_count > 0:
        return "rollback", reason + "（上一章还有 %d 个任务点没刷完，回滚去刷）" % prev_jobs_count
    return "skip", reason + "（上一章任务点已刷完，回滚也无法解锁）"


def fetch_job_list(chaoxing, course, knowledgeid, title="", retry_times=0, retry_interval=5.0):
    """读取章节任务点；章节未开放时按需等待重试"""
    jobs, job_info = chaoxing.get_job_list(course["clazzId"], course["courseId"], course["cpi"], knowledgeid)
    attempt = 0
    while job_info.get("notOpen") and attempt < retry_times:
        attempt += 1
        logger.warning(f"章节《{title}》未开放，{retry_interval:g} 秒后重试（{attempt}/{retry_times}）...")
        if sleep_interruptible(retry_interval):
            break
        jobs, job_info = chaoxing.get_job_list(course["clazzId"], course["courseId"], course["cpi"], knowledgeid)
    return jobs, job_info


def process_jobs(chaoxing, course, jobs, job_info):
    """处理一个章节里的所有任务点（视频 / 文档 / 作业 / 阅读 / 问卷 / 直播）"""
    for job in jobs:
        if _stop_requested.is_set():
            logger.info("收到停止信号，中断刷课")
            return

        logger.info(f"正在处理任务: {job.get('jobid', 'unknown')} - {job.get('title', job.get('name', 'unknown'))}")

        if job["type"] == "video":
            chaoxing.study_video(course, job, job_info, _speed=1.0, _type="Video")
        elif job["type"] == "document":
            chaoxing.study_document(course, job)
        elif job["type"] == "workid":
            chaoxing.study_work(course, job, job_info)
        elif job["type"] == "read":
            chaoxing.study_read(course, job, job_info)
        elif job["type"] == "vote":
            chaoxing.study_vote(course, job, job_info)
        elif job["type"] == "live":
            logger.info(f"识别到直播任务: {job.get('name', '未知直播')}")
            try:
                defaults = {
                    "userid": chaoxing.get_uid(),
                    "clazzId": course.get("clazzId"),
                    "knowledgeid": job_info.get("knowledgeid"),
                }
                live = Live(attachment=job, defaults=defaults, course_id=course.get("courseId"))
                LiveProcessor.run_live(live, speed=1.0)
            except Exception as e:
                logger.error(f"处理直播任务时出错: {e}")


def refresh_course_states(chaoxing, course, points):
    """重新读取章节列表，刷新 points 里的章节状态（是否解锁/是否已完成）"""
    try:
        _fresh = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
    except Exception as e:
        logger.warning(f"重新读取章节列表失败: {e}")
        return False

    _state = {p["id"]: p for p in _fresh}
    _changed = False
    for p in points:
        f = _state.get(p["id"])
        if f is None:
            continue
        if bool(f.get("locked")) != bool(p.get("locked")):
            _changed = True
        p["locked"] = f.get("locked")
        p["finished"] = f.get("finished")
        p["tooltip"] = f.get("tooltip")
    return _changed


def visit_chapter(chaoxing, course, point, why=""):
    """模拟浏览器点开章节：超星会记录学习记录，空章节靠这个才会变成已完成"""
    try:
        ok = chaoxing.visit_chapter(course["courseId"], course["clazzId"], course["cpi"], point["id"])
    except Exception as e:
        logger.debug(f"访问章节《{point.get('title')}》失败: {e}")
        return False
    if ok:
        logger.info(f"已访问章节《{point['title']}》（{why}）")
    return ok


def wait_chapter_unlock(chaoxing, course, points, index, times, interval):
    """上一章刚处理完、本章却还显示未解锁时使用。

    超星解锁有延迟（刚点开上一章，服务端要过几秒才会把下一章放开），
    所以这里隔几秒重读一次章节状态，最多重读 times 次；
    如果章节列表一直说没解锁，就直接问任务点接口（真开放了才会返回任务点）。
    返回 True 表示本章确实已经解锁。
    """
    point = points[index]
    times = max(1, int(times))
    for attempt in range(1, times + 1):
        if sleep_interruptible(interval):
            return False
        logger.info(f"等待《{point['title']}》解锁，第 {attempt}/{times} 次重读章节状态...")
        refresh_course_states(chaoxing, course, points)
        if not points[index].get("locked"):
            logger.info(f"章节《{point['title']}》已经解锁，继续学习")
            return True

    # 章节列表可能还是旧的，直接问任务点接口：只有真开放了才拿得到任务点
    try:
        _jobs, _info = chaoxing.get_job_list(course["clazzId"], course["courseId"], course["cpi"], point["id"])
    except Exception as e:
        logger.debug(f"读取章节《{point['title']}》任务点失败: {e}")
        return False
    if not _info.get("notOpen"):
        points[index]["locked"] = False
        logger.info(f"章节《{point['title']}》已经解锁（任务点接口判断），继续学习")
        return True
    return False


def study_course(chaoxing, course, deepseek_tiku, settings):
    """刷一门课的所有章节：自动跳过未开放(闯关)章节，返回被跳过的章节列表"""
    policy = settings.get("locked_chapter_policy", LOCKED_POLICY_AUTO)
    retry_times = int(settings.get("locked_retry_times", 2))
    retry_interval = float(settings.get("locked_retry_interval", 5))
    second_pass_rounds = int(settings.get("locked_second_pass_rounds", 3))
    unlock_check_times = int(settings.get("unlock_check_times", 4))
    unlock_check_interval = float(settings.get("unlock_check_interval", 5))
    sweep_wait_seconds = float(settings.get("sweep_wait_seconds", 180))
    max_rollback = int(settings.get("max_rollback_times", 3))

    point_list = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])
    points = point_list["points"]
    logger.info(f"课程《{course['title']}》共 {len(points)} 个章节")

    RB = RollBackManager(max_rollback)
    skipped_points = []          # 因未开放被跳过的章节 [(章节, 原因)]
    _rechecked = set()           # 已经重新读过状态的章节，避免反复请求
    _last_handled = -2           # 上一次真正处理完（刷过或本来就没任务点）的章节下标
    __point_index = 0

    while __point_index < len(points):
        if _stop_requested.is_set():
            logger.info("收到停止信号，中断刷课")
            break

        point = points[__point_index]
        logger.info(f'当前章节: {point["title"]}')

        # 上一章刚处理完，本章却还显示未解锁：多半是超星解锁延迟，等一会儿并重读状态
        if point.get("locked") and __point_index - 1 == _last_handled and point["id"] not in _rechecked:
            _rechecked.add(point["id"])
            logger.info(f"章节列表显示《{point['title']}》未解锁，等待服务端更新章节状态...")
            wait_chapter_unlock(chaoxing, course, points, __point_index, unlock_check_times, unlock_check_interval)
            point = points[__point_index]

        # 章节列表里明确标记「闯关未解锁」的：直接跳过（不回滚，因为上一章的任务点已经刷完了）
        if point.get("locked") and policy != LOCKED_POLICY_ROLLBACK:
            if policy == LOCKED_POLICY_SKIP:
                reason = "闯关模式未解锁（按设置直接跳过）"
            else:
                reason = "闯关模式未解锁（上一章任务点已刷完，回滚也无法解锁）"
            logger.warning(f"跳过未开放章节《{point['title']}》（{reason}）")
            skipped_points.append((point, reason))
            __point_index += 1
            continue

        # 章节列表里没标记锁定的，先重试几次（部分章节刚解锁时服务端有延迟）
        _retry = 0 if point.get("locked") else retry_times
        jobs, job_info = fetch_job_list(chaoxing, course, point["id"], point["title"], _retry, retry_interval)

        if job_info.get("notOpen", False):
            # 先看看上一章还有多少任务点没刷完，auto 策略要靠它判断回滚有没有意义
            prev_jobs_count = 0
            if __point_index > 0:
                try:
                    _prev_point = points[__point_index - 1]
                    _prev_jobs, _prev_info = chaoxing.get_job_list(
                        course["clazzId"], course["courseId"], course["cpi"], _prev_point["id"])
                    prev_jobs_count = 0 if _prev_info.get("notOpen") else len(_prev_jobs)
                except Exception as e:
                    logger.debug(f"读取上一章任务点失败: {e}")

            action, reason = decide_not_open_action(
                point,
                prev_jobs_count,
                policy,
                RB.times(point["id"]),
                max_rollback,
                can_rollback=__point_index > 0,
                can_submit=bool(deepseek_tiku and not deepseek_tiku.DISABLE and deepseek_tiku.SUBMIT),
            )

            if action == "rollback":
                try:
                    RB.add_times(point["id"])
                except MaxRollBackError:
                    action, reason = "skip", reason + "（回滚次数已达上限）"

            if action == "rollback":
                logger.warning(f"章节《{point['title']}》未开放：{reason}，回滚到上一章")
                __point_index = max(0, __point_index - 1)
                continue

            logger.warning(f"跳过未开放章节《{point['title']}》（{reason}）")
            skipped_points.append((point, reason))
            __point_index += 1
            continue

        if not jobs:
            # 空章节（没有任务点，例如课程里的章标题页）：像浏览器那样点开一下，
            # 超星才会把它记成已完成；闯关模式下只有这样下一章才会解锁。
            if not point.get("finished"):
                visit_chapter(chaoxing, course, point, "本章没有任务点，用于解锁下一章")
                _last_handled = __point_index      # 状态可能刚变化，让下一章重新读一次
                __point_index += 1
                continue
            _last_handled = __point_index
            __point_index += 1
            continue

        process_jobs(chaoxing, course, jobs, job_info)
        # 任务点刷完后也点开一次章节页，让超星记录本章状态（闯关课程用来解锁下一章）
        visit_chapter(chaoxing, course, point, "任务点已处理完")
        _last_handled = __point_index
        __point_index += 1

    # ============ 收尾复查：解锁延迟 + 卡住的任务点 ============
    # 超星有两处慢：
    #   1) 刷完/点开上一章后，下一章要过一会儿才解锁；
    #   2) 视频到 100% 后，服务端有时要过一段时间才判定完成。
    # 所以整轮刷完后再复查几轮：解锁的章节补上，没完成的任务点再试一遍。
    for _round in range(1, max(1, second_pass_rounds) + 1):
        if _stop_requested.is_set():
            break
        logger.info(f"第 {_round}/{second_pass_rounds} 轮复查：重新读取章节状态...")
        try:
            _new_points = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
        except Exception as e:
            logger.warning(f"重新读取章节列表失败，结束复查: {e}")
            break

        _skipped_ids = {_p["id"] for _p, _r in skipped_points}
        # 已解锁、但还没完成的章节（包括刚解锁的和任务点卡住的）
        _todo = [p for p in _new_points if not p.get("locked") and not p.get("finished")]
        if not _todo:
            logger.info("没有需要复查的章节，收尾复查结束")
            break
        progressed = False

        for _p in _todo:
            if _stop_requested.is_set():
                break
            try:
                _jobs, _info = chaoxing.get_job_list(course["clazzId"], course["courseId"], course["cpi"], _p["id"])
            except Exception as e:
                logger.debug(f'复查章节《{_p["title"]}》失败: {e}')
                continue
            if _info.get("notOpen"):
                continue
            if _jobs:
                if _p["id"] in _skipped_ids:
                    logger.info(f'补刷章节: {_p["title"]}')
                else:
                    logger.info(f'复查章节《{_p["title"]}》：还有 {len(_jobs)} 个任务点没完成，再试一遍 ...')
                process_jobs(chaoxing, course, _jobs, _info)
            elif _p["id"] in _skipped_ids:
                logger.info(f'补刷章节: {_p["title"]}（这一章没有任务点）')
            visit_chapter(chaoxing, course, _p, "复查")
            skipped_points = [(p, r) for (p, r) in skipped_points if p["id"] != _p["id"]]
            progressed = True

        if not progressed and _round >= second_pass_rounds:
            break
        # 还有已解锁但没完成的章节：多半是服务端还没判定（例如视频卡在 100%）。
        # 隔一段时间自己复查，一旦解锁/判定生效就接着刷，不用手动重启脚本。
        _wait = max(5.0, sweep_wait_seconds)
        logger.info(f"第 {_round} 轮复查结束，{_wait:g} 秒后再复查一次"
                    f"（超星解锁/判定有时比较慢，等它一下即可；按 Ctrl+C 可随时停止）...")
        if sleep_interruptible(_wait):
            break

    # 统计已解锁、但任务点一直没被判定完成的章节（典型就是卡在 100% 的视频）
    _unfinished = []
    try:
        _final_points = chaoxing.get_course_point(course["courseId"], course["clazzId"], course["cpi"])["points"]
    except Exception as e:
        logger.debug(f"收尾读取章节列表失败: {e}")
        _final_points = []
    for _p in _final_points:
        if _p.get("locked") or _p.get("finished"):
            continue
        try:
            _jobs, _info = chaoxing.get_job_list(course["clazzId"], course["courseId"], course["cpi"], _p["id"])
        except Exception:
            continue
        if _info.get("notOpen"):
            continue
        for _j in _jobs:
            _unfinished.append((_p, _j))

    if skipped_points:
        logger.warning(f"课程《{course['title']}》共 {len(skipped_points)} 个章节未开放（闯关未解锁 / 老师还没发布），已自动跳过：")
        _show = skipped_points[:10]
        for _point, _reason in _show:
            logger.warning(f"    - {_point['title']}（{_reason}）")
        if len(skipped_points) > len(_show):
            logger.warning(f"    -  其余 {len(skipped_points) - len(_show)} 个未开放章节见日志文件 chaoxing.log")
        logger.warning("这些章节解锁后重新运行脚本即可继续刷，已经刷过的地方不会重复。")

    if _unfinished:
        logger.warning(f"注意：下面 {len(_unfinished)} 个任务点所在章节已经解锁，但任务点一直没被判定完成：")
        for _p, _j in _unfinished[:10]:
            _name = _j.get("name") or _j.get("title") or _j.get("jobid") or "未知任务"
            logger.warning(f"    - 《{_p['title']}》 -> {_name}（{_j.get('type')}）")
        if len(_unfinished) > 10:
            logger.warning(f"    -  其余 {len(_unfinished) - 10} 个见日志文件 chaoxing.log")
        logger.warning("  最常见的是视频：服务端记录了播放进度却不判完成，后面的章节就一直解锁不了。")
        logger.warning("  建议：在浏览器里把这些视频正常播放完（别拖进度条），或者过一段时间再运行一次脚本")
        logger.warning("  （超星有时会延迟几十分钟到几小时才判定完成，你的 1.5.2 就是后来自己过的）。")
    elif not skipped_points:
        logger.info(f"课程《{course['title']}》所有章节已处理完成")

    return skipped_points


# ==================== 核心刷课函数 ====================
def run_with_courses(course_task, username, password, api_key, enable_thinking=False, settings=None):
    if settings is None:
        settings = load_settings()

    browser_context = None
    
    try:
        account = Account(username, password)
        browser_context = new_browser_context()

        cookies_dict = use_cookies()
        if cookies_dict:
            cookies_list = [{'name': k, 'value': v, 'url': 'https://mooc1.chaoxing.com'} for k, v in cookies_dict.items()]
            browser_context.add_cookies(cookies_list)

        deepseek_tiku = DeepSeekAnswer(api_key=api_key, enable_thinking=enable_thinking)
        deepseek_tiku.init_tiku()
        chaoxing = Chaoxing(account=account, tiku=deepseek_tiku, browser_context=browser_context)

        _login_state = chaoxing.login(login_with_cookies=True)
        if not _login_state["status"]:
            raise LoginError(_login_state["msg"])

        for course in course_task:
            if _stop_requested.is_set():
                logger.info("收到停止信号，中断刷课")
                break

            logger.info(f"开始学习课程: {course['title']}")
            study_course(chaoxing, course, deepseek_tiku, settings)

        if _stop_requested.is_set():
            logger.info("刷课已被用户中断")
        else:
            logger.info("所有课程学习任务已完成")

    finally:
        close_browser_context(browser_context)

# ==================== 界面：清屏 + 顶部固定信息 ====================
# 切换菜单时把屏幕就地替换，只保留启动器打印的那几行信息，
# 免得选项一层层往下堆、越看越乱。
_VT_READY = None


def _enable_vt():
    """让 Windows 控制台支持 ANSI 转义序列（清屏要用）"""
    global _VT_READY
    if _VT_READY is None:
        _VT_READY = False
        try:
            if os.name == "nt":
                import ctypes
                _k = ctypes.windll.kernel32
                _h = _k.GetStdHandle(-11)
                _mode = ctypes.c_uint32()
                if _k.GetConsoleMode(_h, ctypes.byref(_mode)):
                    _k.SetConsoleMode(_h, _mode.value | 0x0004)
                    _VT_READY = True
            else:
                _VT_READY = True
        except Exception:
            _VT_READY = False
    return _VT_READY


def clear_screen():
    """清屏；输出被重定向（不是终端）时不动，免得日志里全是转义符"""
    try:
        if not sys.stdout.isatty():
            return
    except Exception:
        return
    if _enable_vt():
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
    elif os.name == "nt":
        os.system("cls")


def get_banner_lines():
    """启动器(start.py)打印的那几行：切换菜单时始终保留在屏幕顶部"""
    _raw = os.environ.get("CX_LAUNCH_BANNER", "").strip()
    if _raw:
        return _raw.splitlines()
    _exe = sys.executable
    try:
        _rel = os.path.relpath(_exe, os.getcwd())
        _shown = _rel if not _rel.startswith("..") else _exe
    except Exception:
        _shown = _exe
    return [f"[INFO] 使用已有的虚拟环境: {_shown}",
            "[INFO] 依赖检查通过",
            "[INFO] 启动 main.py ...",
            "-" * 46]


def draw_screen_header():
    for _line in get_banner_lines():
        print(_line)


def draw_screen(msg="", title="", lines=(), prompt="请选择: ", do_clear=True):
    """清屏 -> 顶部固定信息 -> （可选）上一步结果 -> 标题和选项 -> 读取输入"""
    if do_clear:
        clear_screen()
        draw_screen_header()
    if msg:
        print(msg)
    if title:
        print(f"\n--- {title} ---")
    for _l in lines:
        print(_l)
    if prompt is None:
        return ""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None


# ==================== API密钥菜单 ====================
def manage_api_key(current_key):
    msg = ""
    while True:
        if _stop_requested.is_set():
            return current_key
        choice = draw_screen(msg=msg, title="DeepSeek API密钥管理",
                             lines=[f"当前API密钥: {'已设置' if current_key else '未设置'}",
                                    "1. 设置/修改API密钥",
                                    "2. 删除API密钥",
                                    "3. 返回主菜单"])
        msg = ""
        if choice is None:
            return current_key
        if choice == '1':
            new_key = input("请输入新的DeepSeek API密钥: ").strip()
            if new_key:
                save_api_key(new_key)
                current_key = new_key
                msg = "API密钥已保存。"
            else:
                msg = "没有输入内容，未修改。"
        elif choice == '2':
            if not current_key:
                msg = "当前没有设置API密钥。"
            elif input("确定删除？(y/n): ").lower() == 'y':
                clear_api_key()
                current_key = None
                msg = "API密钥已删除。"
        elif choice == '3':
            return current_key
        else:
            msg = "无效输入"


# ==================== 未开放章节处理设置 ====================
def manage_locked_settings(settings):
    msg = ""
    while True:
        if _stop_requested.is_set():
            return settings
        choice = draw_screen(msg=msg, title="未开放(闯关)章节处理设置",
                             lines=["说明: 闯关模式下，上一章的闯关任务点没刷完，下一章会显示「章节未开放」。",
                                    f"1. 处理策略: {LOCKED_POLICY_NAME.get(settings.get('locked_chapter_policy'), '自动判断')}",
                                    f"2. 未开放重试: {settings.get('locked_retry_times', 2)} 次，间隔 {settings.get('locked_retry_interval', 5)} 秒",
                                    f"3. 收尾复查: {settings.get('locked_second_pass_rounds', 3)} 轮，每轮间隔 {settings.get('sweep_wait_seconds', 180)} 秒（轮数 0 = 关闭）",
                                    f"4. 同一章节最多回滚次数: {settings.get('max_rollback_times', 3)}",
                                    f"5. 解锁等待: 最多等 {settings.get('unlock_check_times', 4)} 次  {settings.get('unlock_check_interval', 5)} 秒",
                                    "6. 返回主菜单"])
        msg = ""
        if choice is None:
            return settings

        if choice == '1':
            print("  1) 自动判断（推荐）  2) 直接跳过  3) 回滚上一章尝试解锁")
            sub = input("请选择策略: ").strip()
            mapping = {'1': LOCKED_POLICY_AUTO, '2': LOCKED_POLICY_SKIP, '3': LOCKED_POLICY_ROLLBACK}
            if sub in mapping:
                settings['locked_chapter_policy'] = mapping[sub]
                save_settings(settings)
                msg = f"已保存: {LOCKED_POLICY_NAME[mapping[sub]]}"
            else:
                msg = "无效输入"
        elif choice == '2':
            try:
                times = input(f"重试次数（当前 {settings.get('locked_retry_times', 2)}）: ").strip()
                interval = input(f"重试间隔秒数（当前 {settings.get('locked_retry_interval', 5)}）: ").strip()
                if times:
                    settings['locked_retry_times'] = max(0, int(times))
                if interval:
                    settings['locked_retry_interval'] = max(1, int(interval))
                save_settings(settings)
                msg = "已保存。"
            except ValueError:
                msg = "输入的不是数字，已取消。"
        elif choice == '3':
            try:
                num = input(f"收尾复查轮数（0 = 关闭，当前 {settings.get('locked_second_pass_rounds', 3)}）: ").strip()
                gap = input(f"每轮之间等待秒数（当前 {settings.get('sweep_wait_seconds', 180)}）: ").strip()
                if num:
                    settings['locked_second_pass_rounds'] = max(0, int(num))
                if gap:
                    settings['sweep_wait_seconds'] = max(0, int(gap))
                save_settings(settings)
                msg = "已保存。"
            except ValueError:
                msg = "输入的不是数字，已取消。"
        elif choice == '4':
            try:
                num = input(f"最多回滚次数（当前 {settings.get('max_rollback_times', 3)}）: ").strip()
                if num:
                    settings['max_rollback_times'] = max(1, int(num))
                    save_settings(settings)
                    msg = "已保存。"
            except ValueError:
                msg = "输入的不是数字，已取消。"
        elif choice == '5':
            try:
                times = input(f"等待次数（当前 {settings.get('unlock_check_times', 4)}）: ").strip()
                interval = input(f"每次间隔秒数（当前 {settings.get('unlock_check_interval', 5)}）: ").strip()
                if times:
                    settings['unlock_check_times'] = max(1, int(times))
                if interval:
                    settings['unlock_check_interval'] = max(1, int(interval))
                save_settings(settings)
                msg = "已保存。"
            except ValueError:
                msg = "输入的不是数字，已取消。"
        elif choice == '6':
            return settings
        else:
            msg = "无效输入"


# ==================== 账号管理菜单 ====================
def manage_account(saved_user, saved_pass):
    msg = ""
    while True:
        if _stop_requested.is_set():
            return saved_user, saved_pass
        has_acc = CredentialManager.has_account()
        has_ck = CredentialManager.has_cookies()
        choice = draw_screen(msg=msg, title="账号管理",
                             lines=[f"账号密码: {'已保存' if has_acc else '未保存'}",
                                    f"Cookies : {'已保存' if has_ck else '未保存'}",
                                    "1. 删除账号密码",
                                    "2. 删除 Cookies（下次需重新登录）",
                                    "3. 全部删除",
                                    "4. 返回主菜单"])
        msg = ""
        if choice is None:
            return saved_user, saved_pass
        if choice == '1':
            if not has_acc:
                msg = "没有已保存的账号密码。"
            elif input("确认删除账号密码？(y/n): ").lower() == 'y':
                CredentialManager.clear_account()
                saved_user = saved_pass = None
                msg = "账号密码已删除。"
        elif choice == '2':
            if not has_ck:
                msg = "没有已保存的 Cookies。"
            elif input("确认删除 Cookies？（下次需重新登录）(y/n): ").lower() == 'y':
                CredentialManager.clear_cookies()
                msg = "Cookies 已删除。"
        elif choice == '3':
            if input("确认全部删除？(y/n): ").lower() == 'y':
                CredentialManager.clear_all()
                saved_user = saved_pass = None
                msg = "已全部删除。"
        elif choice == '4':
            return saved_user, saved_pass
        else:
            msg = "无效输入"


# ==================== 主程序 ====================
def main():
    saved_user, saved_pass = CredentialManager.load_account()
    api_key = load_api_key()
    settings = load_settings()

    need_clear = True          # 首次进入 / 从子菜单回来时，清屏重画
    notice = ""
    while True:
        if _stop_requested.is_set():
            print("\n已退出。")
            break

        _lines = ["", "=" * 40, "超星学习通自动刷课脚本 v3.5",
                  "提示: 退出请按 Ctrl+C（不要点窗口 X 按钮）", "=" * 40,
                  "1. 开始刷课"]
        if saved_user:
            _lines.append(f"   (使用保存账号: {saved_user})")
        _lines += ["2. 管理API密钥",
                   "3. 管理保存账号",
                   f"4. 深度思考模式: {'[开启]' if settings['enable_thinking'] else '[关闭]'}",
                   f"5. 未开放(闯关)章节处理: [{LOCKED_POLICY_NAME.get(settings.get('locked_chapter_policy'), '自动判断')}]",
                   "6. 退出"]
        choice = draw_screen(msg=notice, lines=_lines, do_clear=need_clear)
        notice = ""
        need_clear = False
        if choice is None:
            print("\n已退出。")
            break

        if choice == '1':
            if not api_key:
                print("请先设置API密钥")
                api_key = manage_api_key(None)
                if not api_key:
                    continue

            if saved_user:
                username, password = saved_user, saved_pass
            else:
                username = input("手机号: ").strip()
                password = input("密码: ").strip()
                if not username or not password:
                    continue
                if input("是否保存账号？(y/n): ").lower() == 'y':
                    CredentialManager.save_account(username, password)
                    saved_user, saved_pass = username, password

            # 获取课程选择
            temp_account = Account(username, password)
            temp_context = new_browser_context()
            try:
                temp_chaoxing = Chaoxing(account=temp_account, tiku=None, browser_context=temp_context)
                _login_state = temp_chaoxing.login()
                if not _login_state.get("status"):
                    # v3.5：以前这里不检查登录结果，登录失败会静默走到课程列表为空，看起来像卡住
                    _msg = _login_state.get("msg", "未知原因")
                    logger.error(f"登录失败: {_msg}")
                    print(f"\n[登录失败] {_msg}")
                    print("常见原因：账号或密码不对、超星要求输入验证码、网络被限流。")
                    print("建议：先在浏览器打开 https://i.chaoxing.com 用同一个账号登录一次，")
                    print("      确认能看到课程后再回脚本重试（登录状态有效时脚本会自动复用）。")
                    try:
                        input("按回车返回主菜单...")
                    except (EOFError, KeyboardInterrupt):
                        break
                    notice = f"[登录失败] {_msg}"
                    need_clear = True
                    continue
                course_task = get_course_selection(temp_chaoxing)
            finally:
                close_browser_context(temp_context)

            if not course_task:
                logger.warning("没有读取到任何课程，请检查登录状态或账号下的课程")
                print("\n[提示] 没有读取到任何课程，可能原因：")
                print("  1) 登录状态失效（cookie 没生效）")
                print("  2) 这个账号目前没有正在进行的课程")
                print("  3) 超星接口临时异常 / 被限流，可以稍后重试")
                try:
                    input("按回车返回主菜单...")
                except (EOFError, KeyboardInterrupt):
                    break
                notice = "[提示] 没有读取到任何课程（登录状态 / 课程 / 接口问题，详见日志）"
                need_clear = True
                continue

            if _stop_requested.is_set():
                break

            # 开始刷课
            run_with_courses(course_task, username, password, api_key,
                             enable_thinking=settings['enable_thinking'], settings=settings)

            if _stop_requested.is_set():
                break
            try:
                input("刷课完成，按回车返回主菜单...")
            except (EOFError, KeyboardInterrupt):
                break

        elif choice == '2':
            api_key = manage_api_key(api_key)
            need_clear = True
        elif choice == '3':
            saved_user, saved_pass = manage_account(saved_user, saved_pass)
            need_clear = True
        elif choice == '4':
            settings['enable_thinking'] = not settings['enable_thinking']
            save_settings(settings)
            state = "开启" if settings['enable_thinking'] else "关闭"
            notice = f"深度思考模式已{state}。"
            if settings['enable_thinking']:
                notice += "\n提示: 开启后每道题回答会慢一些，但正确率更高，也会消耗更多 token。"
            need_clear = True
        elif choice == '5':
            settings = manage_locked_settings(settings)
            need_clear = True
        elif choice == '6':
            print("退出脚本")
            break
        else:
            notice = "无效输入"
            need_clear = True


def main_with_net_guard():
    """包一层网络异常处理，避免超星连不上时刷屏"""
    try:
        main()
    except requests.exceptions.ConnectionError as e:
        logger.error("=" * 50)
        logger.error("网络连接失败：无法连接到超星服务器")
        logger.error("请检查：")
        logger.error("  1. 本机网络是否正常")
        logger.error("  2. 是否需要挂代理/梯子")
        logger.error("  3. 超星服务器是否临时故障")
        logger.error("=" * 50)
        logger.debug(f"详细错误: {e}")
        sys.exit(1)
    except requests.exceptions.Timeout as e:
        logger.error("=" * 50)
        logger.error("请求超时：超星服务器无响应或网络太慢")
        logger.error("建议稍后重试，或检查本地网络")
        logger.error("=" * 50)
        logger.debug(f"详细错误: {e}")
        sys.exit(1)
    except requests.exceptions.SSLError as e:
        logger.error("=" * 50)
        logger.error("SSL 证书校验失败，可能是代理拦截了 HTTPS 流量")
        logger.error("如果你在用代理软件，请关闭它或切换为直连后重试")
        logger.error("=" * 50)
        logger.debug(f"详细错误: {e}")
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        logger.error("=" * 50)
        logger.error(f"网络请求失败：{type(e).__name__}")
        logger.error("请检查网络连接后重试")
        logger.error("=" * 50)
        logger.debug(f"详细错误: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已退出。")
        sys.exit(0)


if __name__ == '__main__':
    _install_sigint_handler()
    ensure_playwright_browser()
    main_with_net_guard()