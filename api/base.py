# -*- coding: utf-8 -*-
import re
import threading
import time
import random
from typing import Literal, Optional
from requests import RequestException
import requests
from api.cxsecret_font import font2map, decrypt
from bs4 import BeautifulSoup
import base64
from io import BytesIO
from hashlib import md5
from requests.adapters import HTTPAdapter
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from api.cipher import AESCipher
from api.logger import logger
from api.cookies import save_cookies, use_cookies
from api.process import update_progress, finish_progress
from api.config import GlobalConst as gc, pick_wait
from api.decode import (
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_folder,
    decode_questions_info,
)
from difflib import SequenceMatcher
from enum import Enum


# ==================== 答案处理辅助函数 ====================

def cut(answer):
    """将答案按常见分隔符切开"""
    cut_char = [
        "\n", ",", "，", "|", "\r", "\t", "#", "*", "-", "_", "+", "@",
        "~", "/", "\\", ".", "&", " ", "、",
    ]
    if answer is None:
        return None
    answer = str(answer)
    for char in cut_char:
        if char not in answer:
            continue
        res = [opt.strip() for opt in answer.split(char) if opt.strip()]
        if res:
            return res
    stripped = answer.strip()
    return [stripped] if stripped else None


def multi_cut(answer: str, origin_html_content: str = "", log=logger):
    """切成多段，切不开就告警并返回 None"""
    res = cut(answer)
    if res is None:
        log.warning(
            f"未能从答案中提取信息, 以下为相关信息：\n\t{answer}\n\n{origin_html_content}\n"
        )
        return None
    return res


def clean_res(res):
    """去掉答案前面的 A. / 1、 之类前缀，去掉尾部标点"""
    cleaned_res = []
    if isinstance(res, str):
        res = [res]
    for c in res:
        cleaned = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*|[.,!?;:，。！？；：]', '', c) if len(c) > 1 else c
        cleaned_res.append(cleaned.strip())
    return cleaned_res


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    char_map = str.maketrans({'⻛': '风', '⻔': '门', '⻋': '车', '⻢': '马'})
    normalized = text.translate(char_map)
    normalized = re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', normalized)
    normalized = re.sub(r'\s+', '', normalized)
    normalized = re.sub(r'[，。！？；：,.!?;:()（）\[\]【】"“”‘’\-_/\\|]', '', normalized)
    return normalized.lower()


def get_option_text(option: str) -> str:
    return re.sub(r'^[A-Za-z]\s*[.、:：)?）]?\s*', '', option).strip()


def best_option_by_similarity(target: str, options: list, threshold: float = 0.8) -> str:
    """相似度兜底：把答案文本匹配到选项"""
    if not target or not options:
        return ""
    target_norm = normalize_text(target)
    if not target_norm:
        return ""

    best_letter = ""
    best_score = 0.0
    for option in options:
        option_text = get_option_text(option)
        option_norm = normalize_text(option_text)
        if not option_norm:
            continue
        score = SequenceMatcher(None, target_norm, option_norm).ratio()
        if score > best_score:
            best_score = score
            best_letter = option[:1]

    if best_score >= threshold:
        logger.info(f"相似度兜底匹配成功: {best_letter} (score={best_score:.2f})")
        return best_letter
    return ""


def is_subsequence(a, o):
    iter_o = iter(o.lower())
    return all(c in iter_o for c in a.lower())


def random_answer(options: str, q_type: str) -> str:
    """随机答题（多选题按权重随机选几个，单选/判断直接随机）"""
    answer = ""
    if not options:
        return answer

    if q_type == "multiple":
        _op_list = multi_cut(options)
        if not _op_list:
            logger.error("选项为空, 未能正确提取题目选项信息!")
            return answer

        available_options = len(_op_list)
        if available_options <= 1:
            select_count = available_options
        else:
            max_possible = min(4, available_options)
            min_possible = min(2, available_options)
            weights_map = {
                2: [1.0],
                3: [0.3, 0.7],
                4: [0.1, 0.5, 0.4],
                5: [0.1, 0.4, 0.3, 0.2],
            }
            weights = weights_map.get(max_possible, [0.3, 0.4, 0.3])
            possible_counts = list(range(min_possible, max_possible + 1))
            weights = weights[:len(possible_counts)]
            weights_sum = sum(weights)
            if weights_sum > 0:
                weights = [w / weights_sum for w in weights]
            select_count = random.choices(possible_counts, weights=weights, k=1)[0]

        selected_options = random.sample(_op_list, select_count) if select_count > 0 else []
        for option in selected_options:
            answer += option[:1]
        answer = "".join(sorted(answer))
    elif q_type == "single":
        answer = random.choice(options.split("\n"))[:1]
    elif q_type == "judgement":
        answer = "true" if random.choice([True, False]) else "false"

    logger.info(f"随机选择 -> {answer}")
    return answer


# ==================== 基础类型 ====================

class StudyResult(Enum):
    SUCCESS = 0
    FORBIDDEN = 1
    ERROR = 2
    TIMEOUT = 3

    def is_success(self):
        return self == StudyResult.SUCCESS

    def is_failure(self):
        return self != StudyResult.SUCCESS


class SessionManager:
    _instance = None
    _login_lock = threading.Lock()
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # 防止单例每次实例化都重设 headers / cookies
        if SessionManager._initialized:
            return
        SessionManager._initialized = True
        self._session = requests.Session()
        self._session.mount("https://", HTTPAdapter(max_retries=3))
        self._session.mount("http://", HTTPAdapter(max_retries=3))
        self._session.headers.update(gc.HEADERS)
        cookies = use_cookies()
        if cookies:
            self._session.cookies.update(cookies)

    @classmethod
    def get_session(cls) -> requests.Session:
        return cls()._session

    @classmethod
    def update_cookies(cls):
        cookies = use_cookies()
        if cookies:
            cls()._session.cookies.update(cookies)

    @classmethod
    def relogin_if_needed(cls, chaoxing_instance) -> bool:
        """线程安全的按需重登"""
        with cls._login_lock:
            if chaoxing_instance._validate_cookie_session():
                return True
            logger.info("Cookie session invalid, attempting thread-safe relogin...")
            if (chaoxing_instance.account
                    and chaoxing_instance.account.username
                    and chaoxing_instance.account.password):
                login_result = chaoxing_instance.login(login_with_cookies=False)
                if login_result.get("status"):
                    cls.update_cookies()
                    logger.info("Thread-safe relogin succeeded")
                    return True
                else:
                    logger.warning(f"Thread-safe relogin failed: {login_result.get('msg')}")
            return False


class RateLimiter:
    def __init__(self, call_interval):
        self.last_call = time.time()
        self.lock = threading.Lock()
        self.call_interval = call_interval

    def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
        with self.lock:
            if random_time:
                time.sleep(random.uniform(random_min, random_max))
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.call_interval:
                time.sleep(self.call_interval - elapsed)
            self.last_call = time.time()


def get_timestamp():
    return str(int(time.time() * 1000))


def get_random_seconds():
    return random.randint(30, 90)


def init_session(isVideo: bool = False, isAudio: bool = False):
    _session = requests.session()
    _session.verify = False
    _session.mount('http://', HTTPAdapter(max_retries=3))
    _session.mount('https://', HTTPAdapter(max_retries=3))
    if isVideo:
        _session.headers = gc.VIDEO_HEADERS
    elif isAudio:
        _session.headers = gc.AUDIO_HEADERS
    else:
        _session.headers = gc.HEADERS
    cookies = use_cookies()
    if cookies:
        _session.cookies.update(cookies)
    return _session


class Account:
    username = None
    password = None
    last_login = None
    isSuccess = None

    def __init__(self, _username, _password):
        self.username = _username
        self.password = _password


class Chaoxing:
    def __init__(self, account: Account = None, tiku=None, browser_context=None):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.browser_context = browser_context
        # 已处理的测验任务，避免回滚导致重复答题
        self._processed_work_jobs = set()

        # 速率限制器
        self.rate_limiter = RateLimiter(0.5)
        self.video_log_limiter = RateLimiter(2)

    # ==================== 登录 ====================
    def login(self, login_with_cookies: bool = False):
        if login_with_cookies:
            logger.info("尝试使用 cookies 登录...")
            SessionManager.update_cookies()
            if not self._validate_cookie_session():
                logger.warning("Cookie 登录校验失败，尝试使用账号密码重新登录")
                if self.account and self.account.username and self.account.password:
                    return self.login(login_with_cookies=False)
                return {"status": False, "msg": "cookies 已失效，请更新 cookies 或提供账号密码"}
            logger.info("登录成功...")
            self._log_realname()
            return {"status": True, "msg": "登录成功"}

        _session = requests.session()
        _session.verify = False
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }

        logger.trace("正在尝试登录...")
        # v3.5：这里原来没有 timeout，网络一抖就会永久卡死
        #（Cookie 校验超时后转账号密码重登时最容易遇到，表现为一直没输出）
        resp = _session.post(_url, headers=gc.HEADERS, data=_data, timeout=(10, 20))
        try:
            _login_json = resp.json()
        except ValueError:
            logger.error(f"登录返回不是 JSON（HTTP {getattr(resp, 'status_code', '?')}），可能被拦截、限流或网络异常")
            return {"status": False, "msg": "登录失败：返回内容异常，请稍后重试"}
        if _login_json.get("status") is True:
            save_cookies(_session)
            SessionManager.update_cookies()
            logger.info("登录成功...")
            self._log_realname()
            return {"status": True, "msg": "登录成功"}
        _msg = _login_json.get("msg2") or _login_json.get("msg") or "登录失败（账号密码错误、需要验证码或网络异常）"
        return {"status": False, "msg": str(_msg)}

    def _log_realname(self):
        """登录成功后尝试打印真实姓名（失败静默）"""
        try:
            realname = self.get_name()
            if realname:
                logger.info(f"当前登录用户: {realname}")
        except Exception as e:
            logger.debug(f"获取当前登录用户名失败: {e}")

    def get_name(self) -> str:
        """获取当前登录用户真实姓名"""
        _session = SessionManager.get_session()
        try:
            resp = _session.get("https://passport2.chaoxing.com/mooc/accountManage", timeout=10)
            if resp.status_code == 200:
                match = re.search(r'id="messageName"\s+value="([^"]*)"', resp.text)
                if match:
                    return match.group(1).strip()
        except Exception as e:
            logger.debug(f"获取用户名失败: {e}")
        return ""

    def _validate_cookie_session(self) -> bool:
        """检测当前 session 的 cookies 是否仍然有效"""
        session = SessionManager.get_session()
        if not session.cookies.get("_uid") and not session.cookies.get("UID"):
            return False

        test_session = requests.Session()
        test_session.headers.update(gc.HEADERS)
        test_session.cookies.update(session.cookies.get_dict())

        try:
            resp = test_session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                timeout=8,
            )
        except RequestException as exc:
            logger.debug("Cookie validation request failed: {}", exc)
            return False

        if resp.status_code != 200:
            return False
        if "passport2.chaoxing.com" in resp.text or "login" in resp.text.lower():
            return False
        return True

    def get_fid(self):
        _session = SessionManager.get_session()
        return _session.cookies.get("fid", 1024)

    def get_uid(self):
        s = SessionManager.get_session()
        if "_uid" in s.cookies:
            return s.cookies["_uid"]
        if "UID" in s.cookies:
            return s.cookies["UID"]
        raise ValueError("无法获取 uid，请检查登录状态")

    # ==================== 课程 / 章节 / 任务点 ====================
    def get_course_list(self):
        _session = SessionManager.get_session()
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {
            "courseType": 1,
            "courseFolderId": 0,
            "query": "",
            "superstarClass": 0
        }
        logger.trace("正在读取所有的课程列表...")
        _headers = {
            "Host": "mooc2-ans.chaoxing.com",
            "sec-ch-ua-platform": "\"Windows\"",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
            "Accept": "text/html, */*; q=0.01",
            "sec-ch-ua": "\"Microsoft Edge\";v=\"129\", \"Not=A?Brand\";v=\"8\", \"Chromium\";v=\"129\"",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "sec-ch-ua-mobile": "?0",
            "Origin": "https://mooc2-ans.chaoxing.com",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5"
        }
        _resp = _session.post(_url, headers=_headers, data=_data, timeout=20)
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(_resp.text)
        if not course_list:
            logger.warning("课程列表为空：可能未登录成功、这个账号没有课程，或接口返回异常")

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = _session.get(_interaction_url, timeout=20)
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0
            }
            _resp = _session.post(_url, data=_data, timeout=20)
            course_list += decode_course_list(_resp.text)
        return course_list

    def get_course_point(self, _courseid, _clazzid, _cpi):
        _session = SessionManager.get_session()
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url, timeout=20)
        logger.info("课程章节读取成功...")
        return decode_course_point(_resp.text)

    def get_job_list(self, _clazzid, _courseid, _cpi, _knowledgeid):
        _session = SessionManager.get_session()
        job_list = []
        job_info = {}
        for _possible_num in ["0", "1", "2"]:
            _url = f"https://mooc1.chaoxing.com/mooc-ans/knowledge/cards?clazzid={_clazzid}&courseid={_courseid}&knowledgeid={_knowledgeid}&num={_possible_num}&ut=s&cpi={_cpi}&v=20160407-3&mooc2=1"
            logger.trace("开始读取章节所有任务点...")
            _resp = _session.get(_url, timeout=20)
            _job_list, _job_info = decode_course_card(_resp.text)
            if _job_info.get('notOpen', False):
                logger.info("该章节未开放")
                return [], _job_info
            job_list += _job_list
            job_info.update(_job_info)
        logger.info("章节任务点读取成功...")
        return job_list, job_info

    def visit_chapter(self, _courseid, _clazzid, _cpi, _knowledgeid) -> bool:
        """像浏览器那样点开一个章节（studentstudyAjax）。

        超星的章节完成状态是按章节访问记录算的：像课程里的章标题页这种
        没有任何任务点的空章节，只有被真正打开过才会变成已完成。
        闯关模式下，上一章没完成下一章就一直显示章节未开放，
        所以刷这种课程时必须补上这一步（人工操作就是点一下章节）。
        """
        _session = SessionManager.get_session()
        _url = "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax"
        _params = {
            "courseId": _courseid,
            "clazzid": _clazzid,
            "chapterId": _knowledgeid,
            "cpi": _cpi,
            "verificationcode": "",
            "mooc2": 1,
            "toComputer": "",
            "microTopicId": "0",
            "editorPreview": "0",
            "isPreviewVideo": "false",
            "videoWidth": "0",
            "videoHeight": "0",
            "targetVideoJobId": "",
            "cardIndex": "0",
        }
        _headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html, */*; q=0.01",
            "Referer": f"https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId={_knowledgeid}"
                       f"&courseId={_courseid}&clazzid={_clazzid}&cpi={_cpi}&mooc2=1&hidetype=0",
        }
        try:
            _resp = _session.get(_url, params=_params, headers=_headers, timeout=30)
        except Exception as e:
            logger.debug(f"访问章节 {_knowledgeid} 请求失败: {e}")
            return False
        if "章节未开放" in _resp.text or "校验失败" in _resp.text:
            return False
        return True

    # ==================== 视频 ====================
    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]"
            .encode()).hexdigest()

    def _refresh_video_status(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]) -> Optional[dict]:
        self.rate_limiter.limit_rate(random_time=True, random_max=0.2)
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info_url = f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?k={self.get_fid()}&flag=normal"
        try:
            resp = session.get(info_url, timeout=8, headers=headers)
        except RequestException as exc:
            logger.trace("刷新视频状态失败: {}", exc)
            return None
        if resp.status_code != 200:
            logger.trace(f"刷新视频状态返回状态码 {resp.status_code}")
            return None
        try:
            data = resp.json()
        except ValueError:
            logger.trace("刷新视频状态返回非 JSON 数据")
            return None

        logger.trace(f"刷新视频状态返回数据: {data}")
        if data.get("status") == "success":
            return data
        logger.trace(f"刷新视频状态返回非 success 状态: {data}")
        return None

    def _video_marked_passed(self, _course, _job, _job_info) -> bool:
        """视频已播到 100%、上报却一直不返回 isPassed 时，用章节任务点接口复核一次。

        get_job_list 只会返回还没完成的任务点，所以如果这个 jobid 已经不在里面，
        就说明服务端其实已经判定完成（只是没在返回里告诉我们），可以当成完成处理。
        """
        _knowledgeid = (_job_info or {}).get("knowledgeid")
        if not _knowledgeid:
            return False
        try:
            _jobs, _info = self.get_job_list(_course["clazzId"], _course["courseId"], _course["cpi"], _knowledgeid)
        except Exception as e:
            logger.debug(f"复核任务完成状态失败: {e}")
            return False
        if not _info or _info.get("notOpen"):
            return False
        for _j in _jobs:
            if str(_j.get("jobid")) == str(_job.get("jobid")):
                return False          # 还在未完成列表里
        return True                   # 已经不在未完成列表里 -> 服务端已判定完成

    def _recover_after_forbidden(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"]):
        SessionManager.update_cookies()
        refreshed = self._refresh_video_status(session, job, _type)
        if refreshed:
            return refreshed
        # cookies 更新后仍失败 → 尝试线程安全重登
        if SessionManager.relogin_if_needed(self):
            return self._refresh_video_status(session, job, _type)
        return None

    def video_progress_log(
        self,
        _session,
        _course,
        _job,
        _job_info,
        _dtoken,
        _duration,
        _playingTime,
        _type: str = "Video",
        headers: Optional[dict] = None,
        _isdrag: int = 3,
    ) -> tuple[bool, int]:
        if headers is None:
            headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS

        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        params = {
            "clazzId": _course["clazzId"],
            "playingTime": int(_playingTime),
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": _job["objectid"],
            "otherInfo": _job["otherinfo"],
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": self.get_uid(),
            "isdrag": str(_isdrag),
            "view": "pc",
            "enc": self.get_enc(_course["clazzId"], _job["jobid"], _job["objectid"], _playingTime, _duration, self.get_uid()),
            "dtype": _type,
        }
        if _job.get("videoFaceCaptureEnc"):
            params["videoFaceCaptureEnc"] = _job["videoFaceCaptureEnc"]
        if _job.get("attDuration"):
            params["attDuration"] = _job["attDuration"]
        if _job.get("attDurationEnc"):
            params["attDurationEnc"] = _job["attDurationEnc"]

        rt = _job.get("rt")
        if not rt:
            rt_search = re.search(r"-rt_([1d])", _job.get("otherinfo", ""))
            if rt_search:
                rt = "0.9" if rt_search.group(1) == "d" else "1"

        rt_values = []
        if rt:
            rt_values.append(str(rt))
            rt_values.append("1" if rt == "0.9" else "0.9")
        else:
            rt_values = ["0.9", "1"]

        url = f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/{_course['cpi']}/{_dtoken}"

        def perform_request(current_rt):
            params["rt"] = current_rt
            params["_t"] = get_timestamp()
            debug_params = params.copy()
            if "enc" in debug_params:
                debug_params["enc"] = "<hidden>"
            logger.trace(f"上报URL: {url}?{debug_params}")
            try:
                res = _session.get(url, params=params, headers=headers, timeout=10)
            except Exception as e:
                logger.warning(f"上报请求异常: {e}")
                return None

            # 验证码拦截
            if res.status_code == 403 or '验证码' in res.text or 'validate' in res.text.lower():
                logger.warning("检测到验证码拦截，正在尝试自动通过验证码...")
                try:
                    from api.captcha import CxCaptcha, ocr_init
                    cookies_str = "; ".join([f"{k}={v}" for k, v in _session.cookies.items()])
                    ua = headers.get("User-Agent", gc.HEADERS.get("User-Agent"))
                    ocr_inst = getattr(self, "_ocr", None)
                    if ocr_inst is None:
                        ocr_inst = ocr_init()
                        if ocr_inst:
                            self._ocr = ocr_inst
                    solver = CxCaptcha(user_agent=ua, cookies=cookies_str, ocr=ocr_inst)
                    solved = False
                    for attempt in range(3):
                        logger.info(f"第 {attempt + 1} 次尝试通关验证码...")
                        if solver.try_pass():
                            logger.success("验证码通关成功！")
                            solved = True
                            break
                        logger.warning("验证码验证失败，正在重试...")
                        time.sleep(2)
                    if solved:
                        _session.cookies.update(solver.s.cookies)
                        res = _session.get(url, params=params, headers=headers, timeout=10)
                    else:
                        logger.error("多次验证码通关失败，可能需要手动干预。")
                except Exception as e:
                    logger.error(f"验证码通关逻辑异常: {e}")
            return res

        resp = None
        for current_rt in rt_values:
            resp = perform_request(current_rt)
            if resp is None:
                return False, 500
            if resp.status_code == 200:
                try:
                    return resp.json().get("isPassed", False), 200
                except Exception:
                    return False, resp.status_code
            if resp.status_code == 403:
                logger.trace(f"使用 rt={current_rt} 上报返回403，尝试下一个 rt")
                continue
            logger.warning(f"上报返回非200状态码: {resp.status_code}")
            return False, resp.status_code
        return False, 403

    def study_video(self, _course, _job, _job_info, _speed: float = 1.0, _type: Literal["Video", "Audio"] = "Video") -> StudyResult:
        _session = SessionManager.get_session()
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS

        _info_url = f"https://mooc1.chaoxing.com/ananas/status/{_job['objectid']}?k={self.get_fid()}&flag=normal"
        try:
            _video_info = _session.get(_info_url, headers=headers, timeout=15).json()
        except Exception as e:
            logger.error(f"获取视频信息失败: {e}")
            return StudyResult.ERROR

        if _video_info.get("status") != "success":
            logger.error(f"视频信息返回异常: {_video_info}")
            return StudyResult.ERROR

        _dtoken = _video_info["dtoken"]
        duration = int(_video_info["duration"])
        play_time = int(_job.get("playTime", 0)) // 1000
        last_log_time = 0
        last_iter = time.time()
        wait_time = random.randint(30, 90)
        forbidden_retry = 0
        max_forbidden_retry = 2

        logger.info(f"开始任务: {_job['name']}, 总时长: {duration}s, 已进行: {play_time}s")

        # v3.6（可选，默认关闭）：先按原版做法试一次已看完上报
        # （playingTime=总时长 + isdrag=4）。有些视频服务端记录了进度却迟迟不判完成，
        # 这一下可能直接判过；但它等于一步上报 100%，比模拟正常播放突兀，风控角度更激进。
        if getattr(self, "video_fast_finish", False):
            try:
                _fast_passed, _fast_state = self.video_progress_log(
                    _session, _course, _job, _job_info, _dtoken, duration, duration, _type, headers, _isdrag=4)
            except TypeError:
                _fast_passed, _fast_state = False, 0
            if _fast_passed:
                logger.info(f"任务瞬间完成(isdrag=4): {_job['name']}")
                finish_progress()
                return StudyResult.SUCCESS
            logger.debug(f"已看完上报未被判定完成(state={_fast_state})，继续按正常流程播放")

        passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration, play_time, _type, headers=headers)
        if passed:
            logger.info(f"任务瞬间完成: {_job['name']}")
            return StudyResult.SUCCESS

        # v3.6：进度已经 100%、但服务端一直不返回 isPassed 时，不许无限循环。
        # 次数可配置（默认 3 次），间隔用随机 3~8 秒  避免每 5 秒一次这种机械节奏。
        end_retry = 0
        max_end_retry = max(0, int(getattr(self, "video_end_retry_times", 3)))

        while not passed:
            if play_time - last_log_time >= wait_time or play_time >= duration:
                passed, state = self.video_progress_log(
                    _session, _course, _job, _job_info, _dtoken, duration,
                    int(play_time), _type, headers=headers
                )

                if state == 403:
                    if forbidden_retry >= max_forbidden_retry:
                        logger.warning("403重试失败, 跳过当前任务")
                        finish_progress()
                        return StudyResult.FORBIDDEN
                    forbidden_retry += 1
                    logger.warning("出现403报错, 正在尝试刷新会话状态 (第{}次)", forbidden_retry)
                    time.sleep(random.uniform(2, 4))
                    refreshed_meta = self._recover_after_forbidden(_session, _job, _type)
                    if refreshed_meta:
                        _dtoken = refreshed_meta.get("dtoken", _dtoken)
                        duration = int(refreshed_meta.get("duration", duration))
                        # 若服务端返回了最新播放进度，从该进度续播
                        refreshed_play_time = refreshed_meta.get("playTime")
                        if refreshed_play_time is not None:
                            try:
                                play_time = int(refreshed_play_time)
                            except (TypeError, ValueError):
                                pass
                        logger.trace("刷新后 token: {}, duration: {}, 继续进度: {}s",
                                     _dtoken, duration, play_time)
                    continue

                elif not passed and state != 200:
                    logger.error(f"上报失败，状态码: {state}")
                    finish_progress()
                    return StudyResult.ERROR

                # ===== v3.5：已播到 100% 但服务端还没判定完成 =====
                if not passed and play_time >= duration:
                    end_retry += 1
                    if end_retry > max_end_retry:
                        logger.warning(f"任务《{_job['name']}》已播到 100%，但服务端始终没有判定完成，先跳过这个任务")
                        if self._video_marked_passed(_course, _job, _job_info):
                            logger.success(f"复查发现任务《{_job['name']}》其实已经完成，继续下一个")
                            finish_progress()
                            return StudyResult.SUCCESS
                        logger.warning("  提示：如果网页上也显示这个视频没学完，建议在浏览器里正常播放完一遍（不要拖动进度条），再重跑脚本")
                        finish_progress()
                        return StudyResult.TIMEOUT
                    _wait_s = pick_wait(getattr(self, "video_end_retry_interval", "3-8"), 5.0)
                    logger.warning(f"任务《{_job['name']}》已到 100%，等待服务端确认"
                                   f"（第 {end_retry}/{max_end_retry} 次，{_wait_s:.0f} 秒后再上报）...")
                    time.sleep(_wait_s)

                wait_time = random.randint(30, 90)
                last_log_time = play_time

            dt = (time.time() - last_iter) * _speed
            last_iter = time.time()
            play_time = min(duration, play_time + dt)

            update_progress(_job['name'], int(play_time), duration)
            time.sleep(gc.THRESHOLD)

        finish_progress()
        logger.info(f"任务完成: {_job['name']}")
        return StudyResult.SUCCESS

    # ==================== 文档 ====================
    def study_document(self, _course, _job) -> StudyResult:
        _session = SessionManager.get_session()
        _url = (f"https://mooc1.chaoxing.com/ananas/job/document"
                f"?jobid={_job['jobid']}"
                f"&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}"
                f"&courseid={_course['courseId']}"
                f"&clazzid={_course['clazzId']}"
                f"&jtoken={_job['jtoken']}"
                f"&_dc={get_timestamp()}")
        _resp = _session.get(_url, timeout=20)
        if _resp.status_code != 200:
            return StudyResult.ERROR
        return StudyResult.SUCCESS

    # ==================== Playwright 抓题 ====================
    def _fetch_questions_with_playwright(self, url):
        """使用浏览器打开工作页面，提取题目和表单字段（支持字体解密）"""
        page = self.browser_context.new_page()
        try:
            page.goto(url, wait_until='networkidle', timeout=30000)
            page.wait_for_selector('.TiMu, .questionLi, .singleQuesId', timeout=10000)

            html = page.content()
            font_map = {}
            soup = BeautifulSoup(html, "lxml")
            style_tag = soup.find('style')
            if style_tag and style_tag.text:
                match = re.search(r'base64,([\w\W]+?)\'', style_tag.text)
                if match:
                    base64_string = match.group(1)
                    try:
                        font_data = base64.b64decode(base64_string)
                        font_map = font2map(BytesIO(font_data))
                    except Exception as e:
                        logger.warning(f"字体解码失败: {e}")

            result = page.evaluate("""
                () => {
                    let containers = document.querySelectorAll('.TiMu');
                    if (containers.length === 0) {
                        containers = document.querySelectorAll('.questionLi, .singleQuesId');
                    }
                    const questions = [];
                    const seen = new Set();
                    containers.forEach(q => {
                        const firstLi = q.querySelector('li');
                        if (!firstLi) return;
                        const realQid = firstLi.getAttribute('qid');
                        if (!realQid || seen.has(realQid)) return;
                        seen.add(realQid);

                        let title = '';
                        const t1 = q.querySelector('.fontLabel');
                        const t2 = q.querySelector('h3.mark_name');
                        const t3 = q.querySelector('.Zy_TItle .clearfix');
                        if (t1) title = t1.innerText;
                        else if (t2) title = t2.innerText;
                        else if (t3) title = t3.innerText;
                        title = title.replace(/\\n/g, '').trim();
                        if (!title) return;

                        const opts = [];
                        q.querySelectorAll('li, .answerBg').forEach(el => {
                            let t = el.innerText.replace(/\\n/g, '').trim();
                            const pDiv = el.querySelector('.answer_p');
                            if (pDiv) t = pDiv.innerText.trim();
                            if(t) opts.push(t);
                        });
                        while(opts.length < 5) opts.push('');

                        let ans = '';
                        const ansEl = q.querySelector('.myAnswerBx .answerCon, .myAnswer .answerCon');
                        if (ansEl) ans = ansEl.innerText.replace('我的答案：', '').replace(/正确答案[:：]/, '').trim();

                        const timuDiv = q.closest('.TiMu') || q;
                        const qTypeCode = timuDiv.getAttribute('data') || '0';

                        questions.push({
                            id: realQid,
                            title: title,
                            options: opts.join('\\n'),
                            typeCode: qTypeCode,
                            answer: ans
                        });
                    });

                    const formData = {};
                    const form = document.querySelector('form');
                    if (form) {
                        form.querySelectorAll('input').forEach(input => {
                            const name = input.name;
                            if (name && !name.startsWith('answer')) {
                                formData[name] = input.value;
                            }
                        });
                    }

                    return { questions, formData };
                }
            """)

            questions_list = result['questions']
            form_fields = result['formData']

            for q in questions_list:
                if 'title' in q and q['title']:
                    q['title'] = decrypt(font_map, q['title'])
                if 'options' in q and q['options']:
                    lines = q['options'].split('\n')
                    dec_lines = []
                    for line in lines:
                        if line.strip():
                            dec_lines.append(decrypt(font_map, line))
                        else:
                            dec_lines.append('')
                    q['options'] = '\n'.join(dec_lines)

            form_data = {
                **form_fields,
                'questions': [],
                'answerwqbid': '',
            }

            for q in questions_list:
                q_type = 'single'
                if q['typeCode'] == '1':
                    q_type = 'multiple'
                elif q['typeCode'] == '2':
                    q_type = 'completion'
                elif q['typeCode'] == '3':
                    q_type = 'judgement'

                form_data['questions'].append({
                    'id': q['id'],
                    'title': q['title'],
                    'options': q['options'],
                    'type': q_type,
                    'answerField': {
                        f'answer{q["id"]}': q['answer'],
                        f'answertype{q["id"]}': q['typeCode']
                    }
                })

            if form_data['questions']:
                form_data['answerwqbid'] = ",".join([q['id'] for q in form_data['questions']]) + ","

            return form_data

        except PlaywrightTimeoutError:
            logger.error("浏览器等待题目超时")
            raise
        except Exception as e:
            logger.error(f"浏览器抓取题目失败: {e}")
            raise
        finally:
            page.close()

    def _extract_option_letter(self, option_text):
        """从选项文本中提取选项字母"""
        match = re.search(r'^[A-Z]', option_text.strip())
        if match:
            return match.group()
        match = re.search(r'[A-Z]', option_text)
        if match:
            return match.group()
        return None

    # ==================== 测验 ====================
    def study_work(self, _course, _job, _job_info) -> None:
        """使用 DeepSeek API 进行答题（题目获取用 Playwright）"""
        job_id = _job['jobid']

        if not self.tiku or self.tiku.DISABLE or not hasattr(self.tiku, 'answer_question'):
            logger.warning("题库功能未启用或不可用，跳过答题")
            return None

        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"
        params = {
            "api": "1",
            "workId": _job['jobid'].replace("work-", ""),
            "jobid": _job['jobid'],
            "originJobId": _job['jobid'],
            "needRedirect": "true",
            "skipHeader": "true",
            "knowledgeid": str(_job_info['knowledgeid']),
            'ktoken': _job_info['ktoken'],
            "cpi": _job_info['cpi'],
            "ut": "s",
            "clazzId": _course['clazzId'],
            "type": "",
            "enc": _job['enc'],
            "mooc2": "1",
            "courseid": _course['courseId']
        }
        full_url = _url + '?' + '&'.join([f"{k}={v}" for k, v in params.items()])

        try:
            questions = self._fetch_questions_with_playwright(full_url)
            logger.trace(f"Extracted questions count: {len(questions['questions'])}")
            for idx, q in enumerate(questions['questions']):
                logger.trace(f"Q{idx}: id={q['id']}, title={q['title'][:30]}...")
        except Exception as e:
            logger.error(f"浏览器抓取题目失败，跳过该任务: {e}")
            return

        for q in questions['questions']:
            if q['type'] == 'completion':
                raise RuntimeError("检测到填空题，当前脚本不支持填空题，终止执行")

            logger.info(f"题目: {q['title']}")

            question_data = {
                'title': q['title'],
                'type': q['type']
            }
            if 'options' in q and q['options']:
                question_data['options'] = q['options']

            options_list = []
            if q.get('options'):
                options_list = [opt.strip() for opt in q['options'].split('\n') if opt.strip()]
                logger.info(f"选项: {options_list}")

            max_retries = 3
            answer = None
            for attempt in range(max_retries):
                raw_answer = self.tiku.answer_question(question_data)
                if not raw_answer:
                    logger.warning(f"DeepSeek 返回空答案 (尝试 {attempt+1}/{max_retries})")
                    if attempt == max_retries - 1:
                        raise RuntimeError(f"连续 {max_retries} 次无法获取答案，终止脚本")
                    continue

                logger.info(f"DeepSeek 答案 (尝试 {attempt+1}): {raw_answer}")

                if q['type'] == 'multiple':
                    processed = ""
                    for ch in raw_answer:
                        ch_upper = ch.upper()
                        for opt in options_list:
                            opt_letter = self._extract_option_letter(opt)
                            if opt_letter and ch_upper == opt_letter:
                                processed += ch_upper
                                break
                    if processed:
                        answer = "".join(sorted(processed))
                        break
                    else:
                        logger.warning(f"多选题答案无法匹配选项: {raw_answer}")

                elif q['type'] == 'single':
                    matched = False
                    for opt in options_list:
                        opt_letter = self._extract_option_letter(opt)
                        if opt_letter and raw_answer.upper() == opt_letter:
                            answer = opt_letter
                            matched = True
                            break
                    if matched:
                        break
                    else:
                        logger.warning(f"单选题答案无法匹配选项: {raw_answer}")

                elif q['type'] == 'judgement':
                    clean = raw_answer.strip().lower()
                    if clean in ['对', '正确', 'true', '是']:
                        answer = "true"
                        break
                    elif clean in ['错', '错误', 'false', '否']:
                        answer = "false"
                        break
                    else:
                        logger.warning(f"判断题答案无法识别: {raw_answer}")

                else:
                    answer = raw_answer
                    break

                if attempt == max_retries - 1:
                    raise RuntimeError(f"连续 {max_retries} 次无法获取有效 {q['type']} 答案，终止脚本")

            if answer is None:
                raise RuntimeError("答案获取失败，终止脚本")
            logger.info(f"选择的答案: {answer}")

            q['answerField'][f'answer{q["id"]}'] = answer

        submit_data = {}
        for key, value in questions.items():
            if key != 'questions':
                submit_data[key] = value

        pyflag = self.tiku.get_submit_params()
        logger.trace(f"pyFlag = {pyflag!r}")
        submit_data['pyFlag'] = pyflag

        for q in questions['questions']:
            for field_name, field_value in q['answerField'].items():
                submit_data[field_name] = field_value

        logger.trace(f"Submit data keys: {list(submit_data.keys())}")

        _session = SessionManager.get_session()
        res = _session.post(
            'https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew',
            data=submit_data,
            headers={
                "Host": "mooc1.chaoxing.com",
                "sec-ch-ua-platform": "\"Windows\"",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (WindowsNT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "sec-ch-ua": "\"Microsoft Edge\";v=\"129\", \"Not=A?Brand\";v=\"8\", \"Chromium\";v=\"129\"",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": "https://mooc1.chaoxing.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5"
            },
            timeout=30
        )
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get('status'):
                logger.info(f'提交答题成功 -> {res_json["msg"]}')
                self._processed_work_jobs.add(job_id)
            else:
                logger.error(f'提交答题失败 -> {res_json.get("msg", "未知错误")}')
                raise RuntimeError(f"提交失败: {res_json.get('msg', '未知错误')}")
        else:
            logger.error(f"提交答题失败，状态码：{res.status_code}")
            logger.error(f"响应内容：{res.text}")
            raise RuntimeError(f"提交失败，HTTP {res.status_code}")

    # ==================== 阅读 ====================
    def study_read(self, _course, _job, _job_info) -> StudyResult:
        """阅读任务"""
        _session = SessionManager.get_session()
        jobid = str(_job.get('jobid', '')).replace("read-", "")
        knowledgeid = _job_info.get("knowledgeid")

        _url = "https://mooc1.chaoxing.com/ananas/job/readv2"
        params = {
            'jobid': jobid,
            'knowledgeid': knowledgeid,
            'jtoken': _job.get('jtoken', ''),
            'courseid': _course['courseId'],
            'clazzid': _course['clazzId'],
            '_dc': get_timestamp()
        }

        logger.info(f"正在完成阅读任务: {_job.get('title')} | jobid={jobid}")
        try:
            _resp = _session.get(_url, params=params, timeout=10)
            if _resp.status_code == 200:
                try:
                    msg = _resp.json().get('msg', '完成')
                    logger.info(f"阅读任务学习 -> {msg}")
                except Exception:
                    logger.info("阅读任务学习 -> 完成")
                return StudyResult.SUCCESS
            logger.error(f"阅读任务失败: {_resp.status_code}")
            return StudyResult.ERROR
        except Exception as e:
            logger.error(f"阅读任务异常: {e}")
            return StudyResult.ERROR

    # 兼容旧调用名
    def strdy_read(self, _course, _job, _job_info):
        return self.study_read(_course, _job, _job_info)

    # ==================== 问卷 ====================
    def study_vote(self, _course, _job, _job_info) -> None:
        """问卷调查任务"""
        _session = SessionManager.get_session()
        logger.info(f"正在完成问卷调查任务: {_job.get('title', '问卷')}")

        try:
            _url = "https://mooc1.chaoxing.com/ananas/job/vote"
            params = {
                'jobid': _job['jobid'],
                'knowledgeid': _job_info.get("knowledgeid"),
                'courseid': _course['courseId'],
                'clazzid': _course['clazzId'],
                '_dc': get_timestamp()
            }
            _resp = _session.get(_url, params=params, timeout=10)
            if _resp.status_code == 200:
                pass
            else:
                logger.warning(f"问卷返回: {_resp.status_code}")
        except Exception as e:
            logger.error(f"问卷任务异常: {e}")

    # ==================== 直播（保留旧方法，不删以免引用出错） ====================
    def study_live(self, _course, _job, _job_info):
        """旧版直播接口，已被 Live + LiveProcessor 替代。保留仅为兼容。"""
        logger.warning("study_live 已被弃用，请使用 api.live.Live + api.live_process.LiveProcessor")
        return StudyResult.ERROR