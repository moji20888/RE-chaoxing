# -*- coding: utf-8 -*-
import re
import base64
from io import BytesIO
from fontTools.ttLib import TTFont
from bs4 import BeautifulSoup
from api.logger import logger

class FontDecoder:
    def __init__(self, html_content):
        self.__font_map = {}
        self.__decode_init(html_content)

    def __decode_init(self, html_content):
        soup = BeautifulSoup(html_content, "lxml")
        style_tag = soup.find('style')
        
        # 检查style_tag是否存在且不为None
        if style_tag is None or not hasattr(style_tag, 'text'):
            logger.warning("未找到字体样式标签，跳过字体解码")
            return
            
        if style_tag.text is None:
            logger.warning("样式标签内容为空，跳过字体解码")
            return
        
        # 查找base64编码的字体
        match = re.search(r'base64,([\w\W]+?)\'', style_tag.text)
        if match is None:
            logger.warning("未找到base64编码的字体数据，跳过字体解码")
            return
            
        try:
            base64_string = match.group(1)
            font_data = base64.b64decode(base64_string)
            font = TTFont(BytesIO(font_data))
            cmap = font.getBestCmap()
            font.close()
            
            # 构建字体映射表
            self.__font_map = {}
            for code, name in cmap.items():
                # 根据实际情况调整映射规则
                # 这里使用一个简单的映射示例
                self.__font_map[chr(code)] = name
                
        except Exception as e:
            logger.error(f"字体解码失败: {e}")
            self.__font_map = {}

    def decode(self, text):
        """解码文本，如果没有字体映射，则返回原文本"""
        if not self.__font_map:
            return text
            
        result = ""
        for char in text:
            if char in self.__font_map:
                result += self.__font_map[char]
            else:
                result += char
        return result