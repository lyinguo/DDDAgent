"""
检索器

整合 Embedding 服务 + 向量数据库，提供完整的检索能力:
1. 用户问题向量化
2. 向量相似度搜索
3. 结果排序返回
"""

from typing import List, Optional
from dataclasses import dataclass, field

from common.log import logger
from bot.langgraph_workflow.services.vector_store import VectorStore, VectorRecord
from bot.langgraph_workflow.services.embedding_service import EmbeddingServiceFactory, EmbeddingService


@dataclass
class RetrievalResult:
    """单条检索结果"""
    content: str
    score: float
    filename: str = ""
    chunk_seq: int = 0
    id: str = ""


@dataclass
class RetrievalResponse:
    """检索响应"""
    results: List[RetrievalResult] = field(default_factory=list)
    total: int = 0
    error: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return len(self.results) == 0

    def to_context_text(self, max_chars: int = 15000) -> str:
        """合并为上下文文本"""
        parts = []
        total = 0
        for i, r in enumerate(self.results, 1):
            header = f"[{i}]"
            if r.filename:
                header += f" ({r.filename})"
            if r.score > 0:
                header += f" 相关度:{r.score:.2f}"
            entry = f"{header}\n{r.content}\n"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)
        return "\n".join(parts)


class Retriever:
    """
    检索器

    集成了向量搜索，直接返回最终结果
    """

    def __init__(self, embed_model_name: str = "default"):
        self.embed_model_name = embed_model_name
        self._vector_store = None
        self._embed_service = None

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            self._vector_store = VectorStore()
        return self._vector_store

    @property
    def embed_service(self) -> EmbeddingService:
        if self._embed_service is None:
            self._embed_service = EmbeddingServiceFactory.create(self.embed_model_name)
        return self._embed_service

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
    ) -> RetrievalResponse:
        """
        检索知识库

        :param query: 用户问题
        :param top_k: 返回结果数
        :return: 检索响应
        """
        if not query:
            return RetrievalResponse(error="查询为空")

        try:
            # 1. 问题向量化
            logger.debug(f"[Retriever] 查询向量化: {query[:50]}")
            query_vector = self.embed_service.embed_single(query)

            # 2. 向量搜索
            records = self.vector_store.search(
                query_vector=query_vector,
                top_k=top_k * 2,  # 多取一些用于后续排序
            )

            if not records:
                logger.info(f"[Retriever] 未检索到相关内容")
                return RetrievalResponse(results=[], total=0)

            # 3. 转换为标准格式
            results = []
            for r in records:
                results.append(RetrievalResult(
                    content=r.text,
                    score=r.score,
                    filename=r.metadata.get("filename", ""),
                    chunk_seq=r.metadata.get("chunk_seq", 0),
                    id=r.id,
                ))

            # 4. 按分数排序（已排序，确保顺序）
            results.sort(key=lambda x: x.score, reverse=True)
            results = results[:top_k]

            logger.info(
                f"[Retriever] 检索完成: query={query[:30]}, "
                f"结果数={len(results)}"
            )
            return RetrievalResponse(results=results, total=len(results))

        except Exception as e:
            logger.exception(f"[Retriever] 检索异常: {e}")
            return RetrievalResponse(error=f"检索失败: {str(e)}")

    def add_document(
        self,
        text: str,
        filename: str = "",
        chunker=None,
        embed_service: Optional[EmbeddingService] = None,
    ) -> int:
        """
        添加文档到知识库

        :param text: 文档文本
        :param filename: 文件名
        :param chunker: 文本分块器，None 则不切块
        :param embed_service: Embedding 服务，None 则用默认
        :return: 入库的块数
        """
        if embed_service is None:
            embed_service = self.embed_service

        # 切块
        if chunker:
            chunks = chunker.chunk(text)
            texts = [c.content for c in chunks]
            metadatas = [
                {**c.metadata, "filename": filename}
                for c in chunks
            ]
        else:
            texts = [text]
            metadatas = [{"filename": filename, "seq": 0}]

        if not texts:
            logger.warning(f"[Retriever] 文档无内容: {filename}")
            return 0

        # 向量化
        vectors = embed_service.embed(texts)

        # 入库
        ids = self.vector_store.insert(
            texts=texts,
            vectors=vectors,
            metadatas=metadatas,
        )

        logger.info(f"[Retriever] 文档入库成功: {filename}, {len(ids)} 块")
        return len(ids)