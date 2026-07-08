"""
LLM 服务封装

通过 LLMServiceFactory 根据 provider 类型创建对应的 LLM 服务实例。
当前支持的 provider:
  - openai-compatible: 兼容 OpenAI Chat Completions API 格式的任意服务

用法:
    service = LLMServiceFactory.create("light")
    reply = service.chat([{"role": "user", "content": "你好"}])
"""

from typing import List, Dict, Optional, Any, Union
from abc import ABC, abstractmethod

from openai import OpenAI

from common.log import logger
from bot.langgraph_workflow.model_config import get_model_config, get_proxy


class LLMService(ABC):
    """LLM 服务抽象基类"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_name = config.get("model", "gpt-4o-mini")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 4096)

    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        标准文本对话
        :param messages: 消息列表 [{"role": "user", "content": "..."}]
        :param temperature: 温度参数，覆盖配置中的值
        :param max_tokens: 最大输出 token 数
        :return: 回复文本
        """
        ...

    @abstractmethod
    def chat_with_images(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        多模态对话（支持图片输入）
        :param messages: 消息列表，content 可为文本或图文组合
        :param temperature: 温度参数
        :param max_tokens: 最大输出 token 数
        :return: 回复文本
        """
        ...


class OpenAICompatibleService(LLMService):
    """
    兼容 OpenAI Chat Completions API 格式的服务
    适用于 OpenAI、DeepSeek、通义千问、毕昇等
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        api_base = config.get("api_base", "https://api.openai.com/v1")
        api_key = config.get("api_key", "")
        proxy = get_proxy()

        client_kwargs = {
            "api_key": api_key,
            "base_url": api_base,
        }
        if proxy:
            client_kwargs["http_client"] = None  # openai 库自动使用 http_proxy 环境变量

        self.client = OpenAI(**client_kwargs)
        logger.debug(
            f"[LLMService] 初始化 OpenAICompatibleService: model={self.model_name}, "
            f"api_base={api_base}, temperature={self.temperature}"
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature if temperature is not None else self.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            )
            content = response.choices[0].message.content
            if content is None:
                logger.warning("[LLMService] API 返回 content 为 None")
                return ""
            return content
        except Exception as e:
            logger.exception(f"[LLMService] chat 调用失败: {e}")
            raise

    def chat_with_images(
        self,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        多模态对话。
        messages 格式示例:
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请描述这张图片"},
                    {"type": "image_url", "image_url": {"url": "https://..."}}
                ]
            }
        ]
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature if temperature is not None else self.temperature,
                max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            )
            content = response.choices[0].message.content
            if content is None:
                logger.warning("[LLMService] API 返回 content 为 None")
                return ""
            return content
        except Exception as e:
            logger.exception(f"[LLMService] chat_with_images 调用失败: {e}")
            raise


class LLMServiceFactory:
    """LLM 服务工厂，根据模型名称创建对应的服务实例"""

    _instances: Dict[str, LLMService] = {}

    @classmethod
    def create(cls, model_name: str = "default") -> LLMService:
        """
        创建或获取 LLM 服务实例（带缓存）
        :param model_name: 模型配置名称，对应 config.json 中 llm_models 的 name 字段
        :return: LLMService 实例
        """
        if model_name in cls._instances:
            return cls._instances[model_name]

        config = get_model_config(model_name)
        if config is None:
            logger.warning(
                f"[LLMServiceFactory] 未找到模型 '{model_name}'，使用 'default' 回退"
            )
            config = get_model_config("default")
            if config is None:
                raise ValueError(f"未找到名为 '{model_name}' 或 'default' 的模型配置")

        provider = config.get("provider", "openai-compatible")
        service = cls._create_by_provider(provider, config)
        cls._instances[model_name] = service
        logger.info(
            f"[LLMServiceFactory] 创建 LLM 服务: name={model_name}, "
            f"provider={provider}, model={service.model_name}"
        )
        return service

    @classmethod
    def _create_by_provider(cls, provider: str, config: Dict[str, Any]) -> LLMService:
        """根据 provider 类型创建对应的服务实例"""
        if provider == "openai-compatible":
            return OpenAICompatibleService(config)
        else:
            logger.warning(
                f"[LLMServiceFactory] 不支持的 provider '{provider}'，"
                f"回退到 openai-compatible"
            )
            return OpenAICompatibleService(config)

    @classmethod
    def clear_cache(cls):
        """清除缓存的服务实例（配置变更后调用）"""
        cls._instances.clear()
        logger.info("[LLMServiceFactory] 服务实例缓存已清除")