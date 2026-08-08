import os
import logging
from typing import List
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 自动定位项目根目录 .env：ai/llm_client.py → ai/ → backend/ → 项目根/
current_file = os.path.abspath(__file__)
ai_folder = os.path.dirname(current_file)
backend_folder = os.path.dirname(ai_folder)
root_folder = os.path.dirname(backend_folder)
env_path = os.path.join(root_folder, ".env")
load_dotenv(env_path)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekClient:
    def __init__(self):
        if not DEEPSEEK_API_KEY or len(DEEPSEEK_API_KEY.strip()) == 0:
            raise RuntimeError("请在项目根目录.env文件中正确配置 DEEPSEEK_API_KEY=sk-xxx")
        self.client = OpenAI(
            api_key=DEEPSEEK_API_KEY.strip(),
            base_url=DEEPSEEK_BASE_URL,
            timeout=30.0,
            max_retries=0,
        )
        self.chat_model = "deepseek-chat"

    def chat(self, prompt: str, temperature: float = 0.1) -> str:
        messages: List[ChatCompletionMessageParam] = [
            {"role": "user", "content": prompt}
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.chat_model,
                messages=messages,
                temperature=temperature
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error("DeepSeek LLM 调用失败: %s", e)
            raise


llm_client = DeepSeekClient()
