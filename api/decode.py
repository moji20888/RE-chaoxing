# -*- coding: utf-8 -*-
import re
import json
from bs4 import BeautifulSoup
from api.logger import logger
from api.font_decoder import FontDecoder


def decode_course_list(_text):
    logger.trace("开始解码课程列表...")
    _soup = BeautifulSoup(_text, "lxml")
    _raw_courses = _soup.select("div.course")
    _course_list = list()
    for course in _raw_courses:
        if not course.select_one("a.not-open-tip") and not course.select_one("div.not-open-tip"):
            _course_detail = {}
            _course_detail["id"] = course.attrs["id"]
            _course_detail["info"] = course.attrs["info"]
            _course_detail["roleid"] = course.attrs["roleid"]

            clazz_input = course.select_one("input.clazzId")
            _course_detail["clazzId"] = clazz_input.get("value") if clazz_input else ""

            course_input = course.select_one("input.courseId")
            _course_detail["courseId"] = course_input.get("value") if course_input else ""

            a_tag = course.select_one("a")
            if a_tag and a_tag.has_attr("href"):
                match = re.search(r"cpi=(.*?)(&|$)", a_tag["href"])
                _course_detail["cpi"] = match.group(1) if match else ""
            else:
                _course_detail["cpi"] = ""

            title_span = course.select_one("span.course-name")
            _course_detail["title"] = title_span.get("title") if title_span else ""

            desc_p = course.select_one("p.margint10")
            _course_detail["desc"] = desc_p.get("title") if desc_p else ""

            teacher_p = course.select_one("p.color3")
            _course_detail["teacher"] = teacher_p.get("title") if teacher_p else ""
            _course_list.append(_course_detail)
    return _course_list


def decode_course_folder(_text):
    logger.trace("开始解码二级课程列表...")
    _soup = BeautifulSoup(_text, "lxml")
    _raw_courses = _soup.select("ul.file-list>li")
    _course_folder_list = list()
    for course in _raw_courses:
        fileid = course.attrs.get("fileid")
        if fileid:
            _course_folder_detail = {}
            _course_folder_detail["id"] = fileid
            rename_input = course.select_one("input.rename-input")
            _course_folder_detail["rename"] = rename_input.get("value") if rename_input else ""
            _course_folder_list.append(_course_folder_detail)
    return _course_folder_list


def decode_course_point(_text):
    logger.trace("开始解码章节列表...")
    _soup = BeautifulSoup(_text, "lxml")
    _course_point = {
        "hasLocked": False,
        "points": []
    }

    for _chapter_unit in _soup.find_all("div", class_="chapter_unit"):
        _point_list = []
        _raw_points = _chapter_unit.find_all("li")
        for _point in _raw_points:
            _point_div = _point.div
            if not _point_div or "id" not in _point_div.attrs:
                continue

            _point_detail = {}
            _point_detail["id"] = re.findall(r"^cur(\d{1,20})$", _point_div.attrs["id"])[0]

            title_link = _point_div.select_one("a.clicktitle")
            _point_detail["title"] = title_link.text.strip() if title_link else ""

            job_count_input = _point_div.select_one("input.knowledgeJobCount")
            _point_detail["jobCount"] = job_count_input.get("value") if job_count_input else 1

            # ============ v3.5 新增：解析每个章节的开放状态 ============
            # 章节状态挂在 div.catalog_task > div 上，常见的有：
            #   catalog_state icon_yiwanc catalog_tishi56   -> 已完成
            #   catalog_jindu catalog_tishi120              -> 有进度 / 暂无内容
            #   catalog_state icon-chuangguan catalog_tishi240
            #       -> 闯关模式未解锁（提示：需完成之前闯关任务点，该章节才能解锁）
            hover_span = _point_div.select_one("span.bntHoverTips")
            _tooltip = hover_span.get_text(strip=True) if hover_span else ""
            _state_div = _point_div.select_one("div.catalog_task > div")
            _state_class = " ".join(_state_div.get("class", [])) if _state_div else ""
            _progress_span = _state_div.select_one("span.catalog_points_yi") if _state_div else None

            _point_detail["tooltip"] = _tooltip
            _point_detail["progress"] = _progress_span.get_text(strip=True) if _progress_span else None
            _point_detail["finished"] = ("已完成" in _tooltip) or ("icon_yiwanc" in _state_class)
            # 闯关模式未解锁的章节：列表里点不动，任务点接口也会返回「章节未开放」
            _point_detail["locked"] = (
                ("icon-chuangguan" in _state_class)
                or ("闯关" in _tooltip)
                or ("需完成" in _tooltip)
            )

            if _point_detail["locked"]:
                _course_point["hasLocked"] = True
            if hover_span and '解锁' in hover_span.text:   # 兼容旧版判断
                _course_point["hasLocked"] = True
            # ==========================================================

            _point_list.append(_point_detail)
        _course_point["points"] += _point_list
    return _course_point


def decode_course_card(_text: str):
    logger.trace("开始解码任务点列表...")
    _job_info = {}
    _job_list = []

    # 对于未开放章节检测
    if '章节未开放' in _text:
        _job_info['notOpen'] = True
        return [], _job_info

    _temp = re.findall(r"mArg=\{(.*?)\};", _text.replace(" ", ""))
    if _temp:
        _temp = _temp[0]
    else:
        return [], {}

    _cards = json.loads("{" + _temp + "}")

    if _cards:
        defaults = _cards.get("defaults", {})
        _job_info = {
            "ktoken": defaults.get("ktoken"),
            "mtEnc": defaults.get("mtEnc"),
            "reportTimeInterval": defaults.get("reportTimeInterval", 60),
            "defenc": defaults.get("defenc"),
            "cardid": defaults.get("cardid"),
            "cpi": defaults.get("cpi"),
            "qnenc": defaults.get("qnenc"),
            "knowledgeid": defaults.get("knowledgeid") or defaults.get("knowledgeId"),
            # 下面几个是阅读任务点上报 / 状态判断要用的（网页端 finishJob 用的就是这些）
            "courseid": defaults.get("courseid") or defaults.get("courseId"),
            "clazzId": defaults.get("clazzId"),
            "userid": defaults.get("userid"),
            "isFiled": defaults.get("isFiled"),
            "state": defaults.get("state"),
        }
        attachments = _cards.get("attachments", [])

        for _card_index, _card in enumerate(attachments):
            if _card.get("isPassed"):
                continue

            card_type = str(_card.get("type") or "").strip().lower()
            prop = _card.get("property") or {}
            _title = prop.get("title") or prop.get("name") or _card.get("title") or ""
            _otherinfo = _card.get("otherInfo") or ""
            if "&courseId=" in _otherinfo:
                _otherinfo = _otherinfo.split("&courseId=")[0]

            # 保险：个别章节的卡片没写 type，但 jobid 是 read-xxx，也按阅读处理
            if not card_type:
                _hint = str(_card.get("jobid") or "")
                if _hint.lower().startswith("read"):
                    card_type = "read"

            # ==================== 阅读任务 ====================
            # 和视频/作业一样：卡片里的 job 为真 = 这个任务点"还没完成"
            # （实测：阅读任务点刷完后，同一张卡片的 job 会从 true 变成 null，
            #   同时章节列表里的"待完成任务点"也会减 1）。
            # 两个老坑：
            #   1) 旧代码只在 "job 为空" 的分支里处理 read 卡片 —— 那正好是
            #      "已完成"的卡片，等于专门跳过没完成的阅读任务点；
            #   2) 旧代码看到 property.read == true 就当成"已阅读"跳过 ——
            #      其实未完成的阅读卡片同样是 read=true，这个判断是错的。
            if card_type == "read":
                if not _card.get("job"):
                    continue  # job 为空 = 服务端认为已完成，不用再刷
                _read_id = prop.get("id") or _card.get("readId") or _card.get("objectId")
                _card_jobid = _card.get("jobid")
                if not _card_jobid and _read_id:
                    _card_jobid = "read-%s" % _read_id
                _job = {
                    "title": _title or "阅读任务",
                    "type": "read",
                    "id": _read_id,
                    "readid": _read_id,
                    "jobid": _card_jobid or _read_id,
                    "jtoken": _card.get("jtoken"),
                    "mid": _card.get("mid"),
                    "otherinfo": _otherinfo,
                    "enc": _card.get("enc"),
                    "aid": _card.get("aid"),
                    "index": _card_index,
                    "microTopicId": _card.get("microTopicId"),
                    "property": prop,
                }
                _job_list.append(_job)
                continue

            # ==================== 问卷调查（vote） ====================
            if card_type == "vote":
                _job = {
                    "title": _title or "问卷调查",
                    "type": "vote",
                    "jobid": _card.get("jobid"),
                    "otherinfo": _otherinfo,
                    "mid": _card.get("mid"),
                    "enc": _card.get("enc"),
                    "aid": _card.get("aid"),
                    "index": _card_index,
                }
                _job_list.append(_job)
                continue

            # 既没有 job 字段、也不是上面几种的卡片：不是任务点，跳过
            if not _card.get("job") and card_type != "live":
                continue

            # ==================== 视频任务 ====================
            if card_type == "video":
                _job = {
                    "type": "video",
                    "jobid": _card.get("jobid"),
                    "name": prop.get("name") or _title,
                    "title": prop.get("name") or _title,
                    "otherinfo": _otherinfo,
                    "mid": _card.get("mid"),
                    "objectid": _card.get("objectId"),
                    "aid": _card.get("aid"),
                    "index": _card_index,
                }
                if not _job.get("mid"):
                    logger.warning("出现转码失败视频，已跳过...")
                    continue
                _job_list.append(_job)
                continue

            # ==================== document 任务 ====================
            if card_type == "document":
                _job = {
                    "type": "document",
                    "jobid": _card.get("jobid"),
                    "title": _title,
                    "otherinfo": _otherinfo,
                    "jtoken": _card.get("jtoken"),
                    "mid": _card.get("mid"),
                    "enc": _card.get("enc"),
                    "aid": _card.get("aid"),
                    "objectid": prop.get("objectid"),
                    "index": _card_index,
                }
                _job_list.append(_job)
                continue

            # ==================== workid 任务 ====================
            if card_type == "workid":
                _job = {
                    "type": "workid",
                    "jobid": _card.get("jobid"),
                    "title": _title,
                    "otherinfo": _otherinfo,
                    "mid": _card.get("mid"),
                    "enc": _card.get("enc"),
                    "aid": _card.get("aid"),
                    "index": _card_index,
                }
                _job_list.append(_job)
                continue

            # ==================== 直播任务 ====================
            if card_type == "live":
                _job = {
                    "type": "live",
                    "jobid": _card.get("jobid"),
                    "name": prop.get("title", prop.get("name", "未知直播")),
                    "title": _title or "未知直播",
                    "otherinfo": _otherinfo,
                    "mid": _card.get("mid", ""),
                    "enc": _card.get("enc", ""),
                    "aid": _card.get("aid", ""),
                    "objectid": _card.get("objectId", ""),
                    "property": prop,                 # Live 类需要用
                    "liveId": prop.get("liveId"),
                    "streamName": prop.get("streamName"),
                    "vdoid": prop.get("vdoid"),
                    "index": _card_index,
                }
                _job_list.append(_job)
                continue

            # ==================== 不认识的任务点：日志里说清楚，不要静默丢掉 ====================
            if card_type:
                logger.warning(f"遇到脚本暂不支持的任务点：类型={card_type}，标题={_title or '未知'}，"
                               f"jobid={_card.get('jobid')}；已跳过，若它一直不完成请手动完成一次")

    return _job_list, _job_info


def decode_questions_info(html_content) -> dict:
    def replace_rtn(text):
        return text.replace('\r', '').replace('\t', '').replace('\n', '')

    soup = BeautifulSoup(html_content, "lxml")
    form_data = {}
    form_tag = soup.find("form")

    if not form_tag:
        logger.warning("未找到表单标签，无法解析题目信息")
        return {"questions": []}

    fd = None
    try:
        fd = FontDecoder(html_content)
    except Exception as e:
        logger.warning(f"字体解码器初始化失败: {e}")

        class DummyDecoder:
            def decode(self, text):
                return text
        fd = DummyDecoder()

    for input_tag in form_tag.find_all("input"):
        if 'name' not in input_tag.attrs or 'answer' in input_tag.attrs["name"]:
            continue
        form_data.update({
            input_tag.attrs["name"]: input_tag.attrs.get("value", '')
        })

    form_data['questions'] = []

    question_divs = form_tag.find_all("div", class_="singleQuesId")
    if not question_divs:
        question_divs = form_tag.find_all("div", class_=re.compile(r".*[Qq]ues.*"))

    for div_tag in question_divs:
        title_div = div_tag.find("div", class_="Zy_TItle")
        if not title_div:
            continue

        q_title = replace_rtn(fd.decode(title_div.text))

        q_options = ''
        option_ul = div_tag.find("ul")
        if option_ul:
            for li_tag in option_ul.find_all("li"):
                q_options += replace_rtn(fd.decode(li_tag.text)) + '\n'
            q_options = q_options[:-1] if q_options else ''
        else:
            q_options = ''

        q_type_code = '0'
        q_type = 'single'

        timu_div = div_tag.find('div', class_='TiMu')
        if timu_div and 'data' in timu_div.attrs:
            q_type_code = timu_div.attrs['data']

        if q_type_code == '0':
            q_type = 'single'
        elif q_type_code == '1':
            q_type = 'multiple'
        elif q_type_code == '2':
            q_type = 'completion'
        elif q_type_code == '3':
            q_type = 'judgement'
        else:
            logger.info("未知题型代码 -> " + q_type_code)
            q_type = 'unknown'

        question_id = div_tag.attrs.get("data", "")
        if not question_id and 'id' in div_tag.attrs:
            question_id = div_tag.attrs["id"]

        if not question_id:
            continue

        form_data["questions"].append({
            'id': question_id,
            'title': q_title,
            'options': q_options,
            'type': q_type,
            'answerField': {
                'answer' + question_id: '',
                'answertype' + question_id: q_type_code
            }
        })

    if form_data['questions']:
        form_data['answerwqbid'] = ",".join([q['id'] for q in form_data['questions']]) + ","
    else:
        form_data['answerwqbid'] = ""

    return form_data