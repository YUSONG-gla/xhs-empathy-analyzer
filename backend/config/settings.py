from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """应用配置中心 - 通过环境变量 / .env 文件加载"""

    # LLM 提供商选择
    llm_provider: str = "deepseek"  # openai | gemini | deepseek

    # --- OpenAI 配置 ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    # --- Gemini 配置 ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # --- DeepSeek 配置 ---
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # --- 通用 LLM 配置 ---
    llm_timeout: int = 60          # 请求超时（秒）
    llm_max_retries: int = 2       # 最大重试次数

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
