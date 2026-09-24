# -*- coding: utf-8 -*-
import requests
from api.logger import logger


# ==================== 按题型精简的提示词（v3.5） ====================
# 题目是一题一题发过去的，所以只发当前题型的那一句规则即可，省 token 也更不容易跑偏。
SYSTEM_PROMPTS = {
    "single": "你是答题助手。这是单选题，只输出正确选项的字母（如 A），不要解释、不要标点。",
    "multiple": "你是答题助手。这是多选题，只输出所有正确选项的字母、按字母顺序连写（如 ABD），不要解释。",
    "judgement": "你是答题助手。这是判断题，只输出对或错，不要任何其他文字。",
    "completion": "你是答题助手。这是填空题，只输出填空内容；多个空用逗号分隔，不要标点、不要解释。",
    "short": "你是答题助手。这是简答题，直接给出核心答案（不超过50字），不要解释、不要标点。",
}
SYSTEM_PROMPT_DEFAULT = "你是答题助手。只输出答案本身，不要解释、不要 Markdown、不要答案是：这类前缀。"

# 题型别名：中文/英文写法都认
PROMPT_ALIAS = {
    "单选题": "single", "单选": "single", "single": "single", "radio": "single",
    "多选题": "multiple", "多选": "multiple", "multiple": "multiple", "checkbox": "multiple",
    "判断题": "judgement", "判断": "judgement", "judgement": "judgement",
    "judgment": "judgement", "truefalse": "judgement",
    "填空题": "completion", "填空": "completion", "completion": "completion", "fill": "completion",
    "简答题": "short", "简答": "short", "short": "short", "essay": "short", "问答": "short",
}


def pick_system_prompt(question_type) -> str:
    """按题型挑一段尽量短的提示词（认不出来就用通用的一句）"""
    _t = str(question_type or "").strip().lower()
    if _t in PROMPT_ALIAS:
        return SYSTEM_PROMPTS[PROMPT_ALIAS[_t]]
    for _key, _name in PROMPT_ALIAS.items():
        if _key in _t:
            return SYSTEM_PROMPTS[_name]
    return SYSTEM_PROMPT_DEFAULT


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

            # v3.5：只发当前题型的那一句规则，省 token
            system_prompt = pick_system_prompt(question_type)

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