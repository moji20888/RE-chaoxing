# -*- coding: utf-8 -*-
import configparser
import requests
from pathlib import Path
import json
import os
import shutil
import tempfile
import threading
import time
import random
from urllib3 import disable_warnings, exceptions

from api.logger import logger

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)


class CacheDAO:
    """
    线程安全的题目缓存管理器
    - 加锁避免并发写坏文件
    - 写临时文件后原子替换
    - 解析失败自动备份损坏文件并重建
    """
    DEFAULT_CACHE_FILE = "cache.json"

    def __init__(self, file: str = DEFAULT_CACHE_FILE):
        self.cache_file = Path(file)
        self._lock = threading.RLock()
        if not self.cache_file.is_file():
            self._write_cache({})

    def _read_cache(self) -> dict:
        try:
            with self._lock:
                if not self.cache_file.is_file():
                    return {}
                try:
                    with self.cache_file.open("r", encoding="utf8") as fp:
                        return json.load(fp)
                except json.JSONDecodeError as e:
                    logger.error(f"缓存文件 JSON 解析失败: {e}, 尝试恢复...")
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end + 1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件已损坏，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
                except UnicodeDecodeError as e:
                    logger.error(f"缓存文件编码读取失败: {e}, 采用恢复策略...")
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                return json.loads(text[start:end + 1])
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                        bak_path = self.cache_file.with_name(bak_name)
                        shutil.copy2(self.cache_file, bak_path)
                        logger.error(f"缓存文件编码错误，已备份为: {bak_path}，将使用空缓存继续运行")
                    except Exception as ex:
                        logger.error(f"备份损坏缓存失败: {ex}")
                    return {}
        except Exception as e:
            logger.error(f"读取缓存异常: {e}")
            return {}

    def _write_cache(self, data: dict) -> None:
        try:
            with self._lock:
                parent = self.cache_file.parent
                if not parent.exists():
                    parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(prefix=self.cache_file.name, dir=str(parent))
                try:
                    with os.fdopen(fd, "w", encoding="utf8") as fp:
                        json.dump(data, fp, ensure_ascii=False, indent=4)
                        fp.flush()
                        os.fsync(fp.fileno())
                    os.replace(tmp_path, str(self.cache_file))
                except Exception as e:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    logger.error(f"Failed to write cache atomically: {e}")
        except IOError as e:
            logger.error(f"Failed to write cache: {e}")

    def get_cache(self, question: str):
        data = self._read_cache()
        return data.get(question)

    def add_cache(self, question: str, answer: str) -> None:
        with self._lock:
            data = self._read_cache()
            data[question] = answer
            self._write_cache(data)

    # ---- 兼容旧接口名（原有代码调用 getCache/addCache 也能用） ----
    def getCache(self, question: str):
        return self.get_cache(question)

    def addCache(self, question: str, answer: str) -> None:
        return self.add_cache(question, answer)


class Tiku:
    CONFIG_PATH = "config.ini"  # 默认配置文件路径
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志

    def __init__(self) -> None:
        self._name = None
        self._api = None
        self._conf = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def api(self):
        return self._api

    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, value):
        self._token = value

    def init_tiku(self):
        # 仅用于题库初始化，应该在题库载入后作初始化调用，随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.config_set(self._get_conf())
        if not self.DISABLE:
            # 设置提交模式
            self.SUBMIT = True if self._conf['submit'] == 'true' else False
            # 调用自定义题库初始化
            self._init_tiku()

    def _init_tiku(self):
        # 仅用于题库初始化，例如配置token，交由自定义题库完成
        pass

    def config_set(self, config):
        self._conf = config

    def _get_conf(self):
        """
        从默认配置文件查询配置，如果未能查到，停用题库
        """
        try:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_PATH, encoding="utf8")
            return config['tiku']
        except KeyError or FileNotFoundError:
            logger.info("未找到tiku配置，已忽略题库功能")
            self.DISABLE = True
            return None

    def query(self, q_info: dict):
        if self.DISABLE:
            return None

        # 预处理，去除【单选题】这样与标题无关的字段
        q_info['title'] = q_info['title'][6:]  # 暂时直接用裁切解决

        # 先过缓存
        cache_dao = CacheDAO()
        answer = cache_dao.getCache(q_info['title'])
        if answer:
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            return answer.strip()
        else:
            answer = self._query(q_info)
            if answer:
                answer = answer.strip()
                cache_dao.addCache(q_info['title'], answer)
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")
                return answer
            logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None

    def _query(self, q_info: dict):
        """
        查询接口，交由自定义题库实现
        """
        pass

    def get_tiku_from_config(self):
        """
        从配置文件加载题库，这个配置可以是用户提供，可以是默认配置文件
        """
        if not self._conf:
            self.config_set(self._get_conf())
        if self.DISABLE:
            return self
        try:
            cls_name = self._conf['provider']
            if not cls_name:
                raise KeyError
        except KeyError:
            logger.error("未找到题库配置，已忽略题库功能")
            return self
        new_cls = globals()[cls_name]()
        new_cls.config_set(self._conf)
        return new_cls

    def jugement_select(self, answer: str) -> bool:
        """
        将获取到的答案与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        true_list = self._conf['true_list'].split(',')
        false_list = self._conf['false_list'].split(',')
        answer = answer.strip()
        if answer in true_list:
            return True
        elif answer in false_list:
            return False
        else:
            logger.error(f'无法判断答案 -> {answer} 对应的是正确还是错误，本次将会随机选择选项')
            return random.choice([True, False])

    def get_submit_params(self):
        """
        根据当前设置的提交模式，响应对应的答题提交API中的pyFlag值
        """
        if self.SUBMIT:
            return ""
        else:
            return "1"


class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0
        self._times = 100

    def _query(self, q_info: dict):
        res = requests.get(
            self.api,
            params={
                'question': q_info['title'],
                'token': self._token
            },
            timeout=15,
            verify=False
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足，将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    return self._query(q_info)
                logger.error(f'{self.name}查询失败:\n剩余查询数{res_json["data"].get("times", f"{self._times}(仅参考)")}:\n消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times", self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def load_token(self):
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            logger.error('TOKEN用完，请自行更换再重启脚本')
            raise Exception(f'{self.name} TOKEN 已用完，请更换')
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()