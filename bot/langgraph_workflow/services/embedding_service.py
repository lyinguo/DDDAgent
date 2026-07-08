"""
Embedding 向量化服务

与 LLM 服务相同的 provider 模式，兼容 OpenAI Embedding API 格式。
通过 config.json 中的 embed_models 配置。

配置示例:
  "embed_models": [
    {
      "name": "default",
      "provider": "openai-compatible",
      "api_base": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "model": "text-embedding-ada-002",
      "dimensions": 1536
    }
  ]
"""

from typing import List, Optional, Dict, Any
from abc import ABC, abstractmethod

import requests

from common.log import logger
from bot.langgraph_workflow.model_config import get_proxy


class EmbeddingService(ABC):
    """向量化服务抽象基类"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model_name = config.get("model", "text-embedding-ada-002")
        self.dimensions = config.get("dimensions", 1536)

    @abstractmethod
    def embed(self, texts: List[str]) -> List[List[float]]:
        """
        将文本列表转为向量

        :param texts: 文本列表
        :return: 向量列表，每个向量是 float 列表
        """
        ...

    def embed_single(self, text: str) -> List[float]:
        """单条文本向量化"""
        return self.embed([text])[0]


class OpenAICompatibleEmbedding(EmbeddingService):
    """
    兼容 OpenAI Embedding API 格式
    POST {api_base}/embeddings
    {"input": [...], "model": "..."}
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.api_base = config.get("api_base", "https://api.openai.com/v1").rstrip("/")
        self.api_key = config.get("api_key", "")
        self.proxy = get_proxy()

        # 确保 api_base 以 /v1 结尾
        if not self.api_base.endswith("/v1"):
            self.api_base += "/v1"

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "input": texts,
            "model": self.model_name,
        }

        proxies = None
        if self.proxy:
            proxies = {"http": self.proxy, "https": self.proxy}

        try:
            response = requests.post(
                f"{self.api_base}/embeddings",
                headers=headers,
                json=payload,
                proxies=proxies,
                timeout=60,
            )

            if response.status_code != 200:
                logger.error(
                    f"[Embedding] API 错误: {response.status_code}, {response.text[:200]}"
                )
                raise Exception(f"Embedding API 返回 {response.status_code}")

            data = response.json()
            # 按输入顺序排序
            sorted_data = sorted(data["data"], key=lambda x: x["index"])
            vectors = [item["embedding"] for item in sorted_data]

            logger.debug(f"[Embedding] {len(texts)} 条文本向量化完成, dim={len(vectors[0]) if vectors else 0}")
            return vectors

        except Exception as e:
            logger.exception(f"[Embedding] 向量化失败: {e}")
            raise


class EmbeddingServiceFactory:
    """向量化服务工厂"""

    _instances: Dict[str, EmbeddingService] = {}

    @classmethod
    def create(cls, model_name: str = "default") -> EmbeddingService:
        """
        创建或获取 Embedding 服务实例

        :param model_name: embed_models 中的 name 字段
        """
        if model_name in cls._instances:
            return cls._instances[model_name]

        config = cls._get_config(model_name)
        if config is None:
            raise ValueError(f"未找到名为 '{model_name}' 的 Embedding 模型配置")

        provider = config.get("provider", "openai-compatible")

        if provider == "openai-compatible":
            service = OpenAICompatibleEmbedding(config)
        else:
            logger.warning(f"[EmbeddingFactory] 未知 provider '{provider}'，使用 openai-compatible")
            service = OpenAICompatibleEmbedding(config)

        cls._instances[model_name] = service
        logger.info(
            f"[EmbeddingFactory] 创建 Embedding 服务: name={model_name}, "
            f"model={service.model_name}, dim={service.dimensions}"
        )
        return service

    @classmethod
    def _get_config(cls, model_name: str) -> Optional[Dict[str, Any]]:
        """从 config.json 读取 embed_models 配置"""
        from config import conf
        models = conf().get("embed_models", [])
        for cfg in models:
            if cfg.get("name") == model_name:
                return cfg
        # 回退到第一个
        if models:
            logger.warning(f"[EmbeddingFactory] 未找到 '{model_name}'，使用第一个模型")
            return models[0]
        return None

    @classmethod
    def clear_cache(cls):
        cls._instances.clear()