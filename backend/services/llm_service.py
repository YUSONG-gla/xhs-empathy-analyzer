"""
LLM 调用服务 - 支持 OpenAI 和 Gemini 双提供商

通过抽象基类统一接口，使用配置字段切换提供商。
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

import openai
import httpx
from google import genai
from google.genai import types

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class BaseLLMService(ABC):
    """LLM 服务抽象基类"""

    @abstractmethod
    async def call(self, system_prompt: str, user_prompt: str) -> str:
        """
        调用 LLM 并返回原始文本响应
        
        Args:
            system_prompt: 系统提示词
            user_prompt: 用户消息
        
        Returns:
            LLM 返回的原始文本
        """
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回当前使用的模型名称"""
        pass


# ==================== OpenAI 实现 ====================

class OpenAIService(BaseLLMService):
    """OpenAI LLM 服务（支持 GPT-4o 等系列）"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=httpx.Timeout(settings.llm_timeout),
        )

    async def call(self, system_prompt: str, user_prompt: str) -> str:
        logger.info(f"[OpenAI] 调用模型: {self.model_name}")
        
        response = await self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,  # 低温度保证评分一致性
            max_tokens=2048,
        )
        
        content = response.choices[0].message.content or ""
        logger.info(f"[OpenAI] 响应长度: {len(content)} 字符")
        return content

    @property
    def model_name(self) -> str:
        return self._settings.openai_model


# ==================== DeepSeek 实现 ====================

class DeepSeekService(BaseLLMService):
    """DeepSeek LLM 服务（API 完全兼容 OpenAI 格式，复用 openai SDK）"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = openai.AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=httpx.Timeout(settings.llm_timeout),
        )

    async def call(self, system_prompt: str, user_prompt: str) -> str:
        logger.info(f"[DeepSeek] 调用模型: {self.model_name}")

        response = await self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2048,
        )

        content = response.choices[0].message.content or ""
        logger.info(f"[DeepSeek] 响应长度: {len(content)} 字符")
        return content

    @property
    def model_name(self) -> str:
        return self._settings.deepseek_model


# ==================== Gemini 实现 ====================

class GeminiService(BaseLLMService):
    """Google Gemini LLM 服务（使用 google-genai 新版 SDK，支持免费 API）"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    async def call(self, system_prompt: str, user_prompt: str) -> str:
        logger.info(f"[Gemini] 调用模型: {self.model_name}")

        response = await self._client.aio.models.generate_content(
            model=self.model_name,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=2048,
            ),
            contents=user_prompt,
        )

        content = response.text or ""
        logger.info(f"[Gemini] 响应长度: {len(content)} 字符")
        return content

    @property
    def model_name(self) -> str:
        return self._settings.gemini_model


# ==================== 工厂函数 ====================

def create_llm_service(provider: Optional[str] = None) -> BaseLLMService:
    """
    根据配置创建对应的 LLM 服务实例
    
    Args:
        provider: 提供商名称 (openai/gemini)，为空则从环境变量读取
    
    Returns:
        BaseLLMService 实例
    
    Raises:
        ValueError: 不支持的 provider
    """
    settings = get_settings()
    provider = provider or settings.llm_provider

    if provider == "openai":
        return OpenAIService(settings)
    elif provider == "gemini":
        return GeminiService(settings)
    elif provider == "deepseek":
        return DeepSeekService(settings)
    else:
        raise ValueError(
            f"不支持的 LLM 提供商: '{provider}'。可选值: 'openai', 'gemini', 'deepseek'"
        )
