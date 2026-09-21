import time

from api.live import Live
from api.logger import logger


class LiveProcessor:
    @staticmethod
    def run_live(live: Live, speed: float = 1.0):
        """循环提交直播时长，直到达到总时长"""
        live_status = live.get_status()
        if not live_status:
            logger.error("直播状态获取失败，无法继续")
            return False

        try:
            duration = live_status.get("temp", {}).get("data", {}).get("duration", 0)
            if not duration:
                logger.warning("无法获取直播总时长，默认按30分钟处理")
                duration = 30 * 60
        except Exception as e:
            logger.error(f"解析直播时长失败: {str(e)}")
            return False

        adjusted_duration = duration / speed
        total_minutes = (int(adjusted_duration) + 59) // 60
        logger.info(f"开始刷取直播'{live.name}'，总时长{total_minutes}分钟（已根据倍速调整）")

        for i in range(total_minutes):
            logger.info(f"直播'{live.name}'已观看{i + 1}/{total_minutes}分钟")
            success = live.do_finish()
            if not success:
                logger.warning(f"第{i + 1}分钟时长提交失败，将重试")
                time.sleep(5)
                live.do_finish()
            time.sleep(59 / speed)

        logger.success(f"直播'{live.name}'时长刷取完成")
        return True