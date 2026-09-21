# -*- coding: utf-8 -*-
import json
import time

from api.base import SessionManager
from api.config import GlobalConst as gc
from api.logger import logger


class Live:
    def __init__(self, attachment: dict, defaults: dict, course_id: str):
        self.attachment = attachment
        self.defaults = defaults
        self.course_id = course_id
        self.name = self.attachment.get("property", {}).get("title", "未知直播")
        self.headers = gc.HEADERS.copy()
        self.headers.update({
            "Referer": "https://mooc1.chaoxing.com/ananas/modules/live/index.html?v=2022-1214-1139"
        })

    def do_finish(self):
        """提交直播观看时长"""
        prop = self.attachment.get("property", {})
        stream_name = prop.get("streamName")
        vdoid = prop.get("vdoid")
        user_id = self.defaults.get("userid")

        if not all([stream_name, vdoid, user_id]):
            logger.error("缺少直播必要参数，无法提交时长")
            return False

        url = (f"https://zhibo.chaoxing.com/saveTimePc?streamName={stream_name}"
               f"&vdoid={vdoid}&userId={user_id}&isStart=0"
               f"&t={int(time.time() * 1000)}&courseId={self.course_id}")

        session = SessionManager.get_session()
        try:
            response = session.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            logger.debug(f"直播时长提交响应: {response.text}")
            return response.text.strip() == "@success"
        except Exception as e:
            logger.error(f"提交直播时长失败: {str(e)}")
            return False

    def get_status(self) -> dict | None:
        """获取直播状态（含总时长）"""
        prop = self.attachment.get("property", {})
        live_id = prop.get("liveId")
        user_id = self.defaults.get("userid")
        clazz_id = self.defaults.get("clazzId")
        knowledge_id = self.defaults.get("knowledgeid")

        if not all([live_id, user_id, clazz_id, knowledge_id]):
            logger.error("缺少直播状态查询必要参数")
            return None

        status_url = (f"https://mooc1.chaoxing.com/ananas/live/liveinfo"
                      f"?liveid={live_id}&userid={user_id}&clazzid={clazz_id}"
                      f"&knowledgeid={knowledge_id}&courseid={self.course_id}"
                      f"&jobid={prop.get('_jobid', '')}&ut=s")

        session = SessionManager.get_session()
        try:
            response = session.get(status_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"获取直播状态失败: {str(e)}")
            return None