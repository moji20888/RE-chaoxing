"""
Captcha API for Chaoxing

本模块用于通过CX验证码，提供包括验证码获取、识别、验证等接口。
使用了开源的验证码识别库[DdddOcr](https://github.com/sml2h3/ddddocr)
"""

from random import randint
from typing import Optional

from loguru import logger
from requests import session

try:
    from ddddocr import DdddOcr
    HAS_DDDDOCR = True
except ImportError:
    DdddOcr = None
    HAS_DDDDOCR = False


def ocr_init() -> Optional[DdddOcr]:
    """初始化OCR对象"""
    if not HAS_DDDDOCR:
        logger.warning("未检测到 ddddocr 依赖，自动验证码识别将不可用。如遇403限制请在浏览器端手动完成验证。")
        return None
    try:
        return DdddOcr(show_ad=False)
    except Exception as e:
        logger.warning(f"ddddocr 初始化失败: {e}，自动验证码识别将不可用")
        return None


_MISSING = object()


class CxCaptcha:
    """超星验证码处理"""

    host = 'https://mooc1.chaoxing.com'
    api = {
        'get': '/processVerifyPng.ac',
        'submit': '/html/processVerify.ac'
    }

    def __init__(self, user_agent: str, cookies: str, ocr: Optional[DdddOcr] = _MISSING):
        self.user_agent = user_agent
        self.cookies = cookies
        self.s = session()
        self.s.headers.update({
            'User-Agent': self.user_agent,
            'Cookie': self.cookies,
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
        })
        self.s.verify = False
        self.ocr = ocr_init() if ocr is _MISSING else ocr

    def getCaptcha(self) -> Optional[bytes]:
        api = self.host + self.api['get']
        random_t = randint(0, 2147483647)
        res = self.s.get(api, params={'t': random_t}, timeout=15)
        if res.status_code == 200 and res.headers['Content-Type'] == 'image/png':
            return res.content
        return None

    def submitCaptcha(self, cap_token: str) -> bool:
        api = self.host + self.api['submit']
        params = {'ucode': cap_token, 'app': 0}
        res = self.s.get(api, params=params, timeout=15)
        return res.status_code == 302

    def recognition(self, img: bytes) -> str:
        if not self.ocr:
            logger.error("ddddocr 实例未成功初始化，无法执行验证码识别")
            raise RuntimeError("ddddocr is not available")
        return self.ocr.classification(img)

    def try_pass(self) -> bool:
        try:
            cap_img = self.getCaptcha()
            if not cap_img:
                return False
            cap_token = self.recognition(cap_img)
            return self.submitCaptcha(cap_token)
        except Exception as e:
            logger.error(f"验证码自动验证时发生异常: {e}")
            return False