# -*- coding: utf-8 -*-
import requests
from api.logger import logger


class DeepSeekAnswer:
    """使用 DeepSeek V4 系列 API 进行答题的类，支持深度思考开关"""

    def __init__(self, api_key=None, config=None, enable_thinking=False):
        self.DISABLE = False
        self.SUBMIT = True

        self.api_key = api_key
        self.api_base = "https://api.deepseek.com/v1"
        self.model = "deepseek-v4-flash"       # 需要更强可改 v4-pro
        self.temperature = 1.0
        self.enable_thinking = enable_thinking

        # 思考模式会消耗额外 token，max_tokens 给大一点
        self.max_tokens = 4000 if enable_thinking else 1000

        logger.info(f"=== DeepSeek 答题器初始化 ===")
        logger.info(f"API密钥: {self.api_key[:8]}...")
        logger.info(f"模型: {self.model}")
        logger.info(f"深度思考: {'开启' if self.enable_thinking else '关闭'}")
        logger.info(f"=== 初始化完成 ===")

        if config and 'tiku' in config:
            submit_str = config.get('tiku', 'submit', fallback='true')
            self.SUBMIT = submit_str.lower() == 'true'

    def config_set(self, config):
        if config and 'tiku' in config:
            submit_str = config.get('tiku', 'submit', fallback='true')
            self.SUBMIT = submit_str.lower() == 'true'
        else:
            self.SUBMIT = True

    def get_tiku_from_config(self):
        return self

    def init_tiku(self):
        if not self.DISABLE:
            logger.info(f"DeepSeek 答题器已启用 | 提交模式: {'直接提交' if self.SUBMIT else '保存但不提交'}")

    def answer_question(self, question_data):
        if self.DISABLE:
            return None

        try:
            question_text = f"题目：{question_data.get('title', '')}\n"

            if 'options' in question_data and question_data['options']:
                options = question_data['options']
                question_text += "选项：\n"
                if isinstance(options, str):
                    question_text += options + "\n"
                else:
                    question_text += str(options) + "\n"

            question_type = question_data.get('type', '未知题型')
            question_text += f"\n题型：{question_type}"

            system_prompt = """你是一个专业的答题助手，负责根据题目内容和选项给出准确的答案。

回答规则如下：
1. 单选题：只返回一个选项字母，例如 A、B、C、D。
2. 多选题：返回所有正确选项的字母，按字母顺序排列，例如 AB、ACD，不要只返回单个字母。
3. 判断题：只返回"对"或"错"，不要返回其他内容。
4. 填空题：直接给出填空内容，不要加任何标点和解释。如果有多个空，用逗号分隔。
5. 简答题：直接给出核心答案，不超过50字，不要解释和标点。

输出要求：
- 只输出答案本身，不要输出任何解释、说明、前缀或后缀。
- 不要使用 Markdown 语法。
- 不要输出题号或题型。
- 不要输出"答案："这样的字样。

"""

            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question_text}
                ],
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                # 深度思考开关：显式传参，避免依赖服务端默认
                "thinking": {"type": "enabled" if self.enable_thinking else "disabled"},
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }

            api_url = f"{self.api_base}/chat/completions"
            response = requests.post(api_url, headers=headers, json=payload, timeout=120 if self.enable_thinking else 30)
            response.raise_for_status()

            result = response.json()

            # 缓存命中日志
            usage = result.get("usage", {})
            hit = usage.get("prompt_cache_hit_tokens", 0)
            miss = usage.get("prompt_cache_miss_tokens", 0)
            total = hit + miss
            if total > 0:
                rate = hit / total * 100
                logger.info(f"[缓存] 命中 {hit} / 总计 {total} tokens ({rate:.1f}%)")
            if self.enable_thinking:
                logger.debug(f"[用量] 思考 tokens: {usage.get('completion_tokens_details', {}).get('reasoning_tokens', '?')}")

            message = result['choices'][0]['message']
            answer = (message.get('content') or '').strip()

            # 思考模式偶尔 content 为空，从 reasoning_content 里兜底提取
            if not answer:
                reasoning = message.get('reasoning_content', '') or ''
                if reasoning:
                    # 取最后一行非空内容作为答案
                    lines = [l.strip() for l in reasoning.split('\n') if l.strip()]
                    if lines:
                        answer = lines[-1]
                        logger.warning(f"content 为空，从 reasoning_content 兜底: {answer}")

            return answer or None

        except Exception as e:
            logger.error(f"DeepSeek API调用失败: {e}")
            return None

    def get_submit_params(self):
        return "" if self.SUBMIT else "1"