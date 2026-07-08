"""
知识库检索服务

支持多种知识库接入方式:
  - bisheng: 通过毕昇平台知识库检索 API 获取相关文档片段

用法:
    service = KnowledgeServiceFactory.create()
    results = service.retrieve("请假流程是什么？")
"""

from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

import requests

from common.log import logger
from bot.langgraph_workflow.model_config import get_bisheng_rag_config, get_proxy


@dataclass
class KnowledgeResult:
    """单条检索结果"""
    content: str                          # 文档片段内容
    score: float = 0.0                    # 相关度分数
    title: str = ""                       # 来源文档标题
    chunk_id: str = ""                    # 片段ID


@dataclass
class KnowledgeResponse:
    """检索响应"""
    results: List[KnowledgeResult] = field(default_factory=list)
    total: int = 0
    error: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return len(self.results) == 0

    def to_context_text(self, max_chars: int = 15000) -> str:
        """
        将检索结果合并为上下文文本（供 LLM 使用）
        :param max_chars: 最大字符数
        :return: 合并后的文本
        """
        parts = []
        total_chars = 0
        for i, r in enumerate(self.results, 1):
            header = f"[{i}]"
            if r.title:
                header += f" ({r.title})"
            entry = f"{header}\n{r.content}\n"
            if total_chars + len(entry) > max_chars:
                break
            parts.append(entry)
            total_chars += len(entry)
        return "\n".join(parts)


class KnowledgeService(ABC):
    """知识库检索服务抽象基类"""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        max_chars: int = 15000,
    ) -> KnowledgeResponse:
        """
        检索知识库
        :param query: 检索问题
        :param top_k: 返回的最相关片段数
        :param max_chars: 返回内容最大字符数
        :return: 检索响应
        """
        ...


class BishengKnowledgeService(KnowledgeService):
    """
    毕昇平台知识库检索

    通过毕昇的 API 进行知识库向量检索 + 关键词检索。
    需要配置:
      - bisheng_rag_api_base: 检索API地址
      - bisheng_rag_api_key: API密钥（如有）
      - bisheng_rag_knowledge_ids: 要检索的知识库ID列表
    """

    def __init__(self):
        config = get_bisheng_rag_config()
        self.api_base = config.get("api_base", "")
        self.api_key = config.get("api_key", "")
        self.knowledge_ids = config.get("knowledge_base_ids", [])
        self.proxy = get_proxy()

        if not self.api_base:
            logger.warning(
                "[KnowledgeService] bisheng_rag_api_base 未配置，"
                "知识库检索将返回空结果"
            )

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        max_chars: int = 15000,
    ) -> KnowledgeResponse:
        if not self.api_base:
            return KnowledgeResponse(error="知识库检索 API 未配置")

        if not self.knowledge_ids:
            return KnowledgeResponse(error="未配置知识库 ID")

        try:
            payload = {
                "query": query,
                "knowledge_ids": self.knowledge_ids,
                "top_k": top_k,
                "max_chunk_size": max_chars,
            }

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            proxies = None
            if self.proxy:
                proxies = {"http": self.proxy, "https": self.proxy}

            logger.debug(
                f"[KnowledgeService] 检索知识库: query={query[:50]}, "
                f"knowledge_ids={self.knowledge_ids}"
            )

            response = requests.post(
                self.api_base,
                headers=headers,
                json=payload,
                proxies=proxies,
                timeout=30,
            )

            if response.status_code != 200:
                logger.error(
                    f"[KnowledgeService] API 请求失败: "
                    f"status={response.status_code}, body={response.text[:200]}"
                )
                return KnowledgeResponse(
                    error=f"检索 API 返回错误状态码: {response.status_code}"
                )

            data = response.json()
            results = self._parse_response(data)
            logger.info(
                f"[KnowledgeService] 检索完成: query={query[:30]}, "
                f"结果数={len(results)}"
            )
            return KnowledgeResponse(results=results, total=len(results))

        except requests.exceptions.Timeout:
            logger.error("[KnowledgeService] 检索请求超时")
            return KnowledgeResponse(error="知识库检索请求超时")
        except requests.exceptions.RequestException as e:
            logger.exception(f"[KnowledgeService] 检索请求异常: {e}")
            return KnowledgeResponse(error=f"知识库检索请求失败: {str(e)}")
        except Exception as e:
            logger.exception(f"[KnowledgeService] 检索未知错误: {e}")
            return KnowledgeResponse(error=f"知识库检索异常: {str(e)}")

    def _parse_response(self, data: dict) -> List[KnowledgeResult]:
        """
        解析毕昇检索 API 响应
        不同版本的 API 响应格式可能不同，尝试多种解析方式
        """
        results = []

        # 方式1: data.results 或 data.data.results
        raw_items = (
            data.get("results")
            or data.get("data", {}).get("results")
            or data.get("data", {}).get("chunks")
            or data.get("chunks")
            or []
        )

        if isinstance(raw_items, list):
            for item in raw_items:
                content = (
                    item.get("content")
                    or item.get("text")
                    or item.get("chunk")
                    or item.get("page_content")
                    or ""
                )
                if not content:
                    continue
                results.append(KnowledgeResult(
                    content=content,
                    score=item.get("score", 0) or item.get("similarity", 0),
                    title=item.get("title", "") or item.get("doc_name", ""),
                    chunk_id=item.get("chunk_id", "") or item.get("id", ""),
                ))

            results.sort(key=lambda r: r.score, reverse=True)

        return results


class KnowledgeServiceFactory:
    """知识库服务工厂"""

    _instance = None

    @classmethod
    def create(cls) -> KnowledgeService:
        """
        创建知识库检索服务实例
        当前默认返回 BishengKnowledgeService
        """
        if cls._instance is not None:
            return cls._instance

        service = BishengKnowledgeService()
        cls._instance = service
        logger.info(
            f"[KnowledgeServiceFactory] 创建知识库服务: "
            f"type={type(service).__name__}"
        )
        return service

    @classmethod
    def clear_cache(cls):
        cls._instance = None