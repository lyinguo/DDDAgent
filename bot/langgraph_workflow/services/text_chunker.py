"""
文本分块器

支持多种切割策略，通过 config.json 配置:

1. fixed     — 按固定字符数切（带 overlap）
2. delimiter — 按指定分隔符切
3. semantic  — 按语义相似度分段
4. marker    — 按正则匹配的标题/标记切

配置示例 (config.json):
  "text_chunker": {
    "strategy": "fixed",
    "strategies": {
      "fixed": { "chunk_size": 500, "chunk_overlap": 50 },
      "delimiter": { "delimiter": "\\n\\n", "max_chunk_size": 1000, "min_chunk_size": 100 },
      "semantic": { "max_chunk_size": 800, "min_chunk_size": 200, "similarity_threshold": 0.7 },
      "marker": { "markers": ["第.*章", "一、", "二、"], "max_chunk_size": 1500 }
    }
  }
"""

import re
import uuid
from typing import List, Optional, Dict, Any
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from common.log import logger
from config import conf


@dataclass
class Chunk:
    """单个文本块"""
    content: str
    chunk_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.chunk_id:
            self.chunk_id = str(uuid.uuid4())[:8]


class TextChunker(ABC):
    """文本分块器抽象基类"""

    @abstractmethod
    def chunk(self, text: str, **kwargs) -> List[Chunk]:
        """将文本切分为块"""
        ...


class FixedChunker(TextChunker):
    """
    固定大小切割
    按 chunk_size 字符数切分，相邻块之间保留 overlap 字符重叠
    """
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk(self, text: str, **kwargs) -> List[Chunk]:
        if not text:
            return []

        chunks = []
        start = 0
        seq = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            content = text[start:end]
            if content.strip():
                chunks.append(Chunk(
                    content=content.strip(),
                    metadata={"seq": seq, "start": start, "end": end},
                ))
                seq += 1
            start += self.chunk_size - self.chunk_overlap

        logger.debug(f"[FixedChunker] {len(chunks)} 块, size={self.chunk_size}, overlap={self.chunk_overlap}")
        return chunks


class DelimiterChunker(TextChunker):
    """
    分隔符切割
    按指定的分隔符切分，再将过长的块二次切分
    """
    def __init__(self, delimiter: str = "\n\n", max_chunk_size: int = 1000, min_chunk_size: int = 100):
        self.delimiter = delimiter
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size

    def chunk(self, text: str, **kwargs) -> List[Chunk]:
        if not text:
            return []

        # 按分隔符切分
        raw_segments = re.split(self.delimiter, text)
        chunks = []
        seq = 0

        for seg in raw_segments:
            seg = seg.strip()
            if not seg:
                continue

            if len(seg) <= self.max_chunk_size and len(seg) >= self.min_chunk_size:
                chunks.append(Chunk(content=seg, metadata={"seq": seq}))
                seq += 1
            elif len(seg) < self.min_chunk_size:
                # 太短：合并到上一个块
                if chunks:
                    chunks[-1].content += "\n" + seg
                else:
                    chunks.append(Chunk(content=seg, metadata={"seq": seq}))
                    seq += 1
            else:
                # 太长：递归用固定大小切
                sub_chunker = FixedChunker(
                    chunk_size=self.max_chunk_size,
                    chunk_overlap=50,
                )
                sub_chunks = sub_chunker.chunk(seg)
                for sc in sub_chunks:
                    sc.metadata["seq"] = seq
                    chunks.append(sc)
                    seq += 1

        logger.debug(f"[DelimiterChunker] {len(chunks)} 块, delimiter={self.delimiter}")
        return chunks


class SemanticChunker(TextChunker):
    """
    语义切割
    计算相邻句子的语义相似度，在相似度低的地方切分。
    需要 embedding_service 配合。
    """
    def __init__(
        self,
        max_chunk_size: int = 800,
        min_chunk_size: int = 200,
        similarity_threshold: float = 0.7,
    ):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.similarity_threshold = similarity_threshold

    def chunk(self, text: str, embed_func=None, **kwargs) -> List[Chunk]:
        """
        语义切割

        :param text: 要切分的文本
        :param embed_func: embedding 函数，接收字符串列表返回向量列表
                           若不传则回退到 fixed 切割
        :return: Chunk 列表
        """
        if not text:
            return []

        # 没有 embed_func 时回退到 fixed
        if embed_func is None:
            logger.warning("[SemanticChunker] 无 embed_func，回退到 fixed 切割")
            fallback = FixedChunker(
                chunk_size=self.max_chunk_size,
                chunk_overlap=50,
            )
            return fallback.chunk(text)

        # 按句子切分
        sentences = re.split(r"([。！？\n])", text)
        # 重组句子（将标点附回到前一个句子）
        grouped = []
        buf = ""
        for s in sentences:
            if re.match(r"^[。！？\n]$", s):
                buf += s
            else:
                if buf:
                    grouped.append(buf.strip())
                buf = s
        if buf:
            grouped.append(buf.strip())
        grouped = [g for g in grouped if g]

        if len(grouped) <= 1:
            return [Chunk(content=text, metadata={"seq": 0})]

        # 分批计算 embedding（避免一次传太多）
        batch_size = 20
        all_embeddings = []
        for i in range(0, len(grouped), batch_size):
            batch = grouped[i:i + batch_size]
            try:
                vecs = embed_func(batch)
                all_embeddings.extend(vecs)
            except Exception as e:
                logger.warning(f"[SemanticChunker] embedding 计算失败: {e}")
                # 回退到 fixed
                fallback = FixedChunker(chunk_size=self.max_chunk_size, chunk_overlap=50)
                return fallback.chunk(text)

        # 计算相邻句子相似度
        import math
        def cosine_sim(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(y * y for y in b))
            return dot / (na * nb + 1e-10)

        # 在相似度低于阈值的地方切分
        chunks = []
        current_chunk = []
        seq = 0

        for i in range(len(grouped)):
            current_chunk.append(grouped[i])
            current_text = "".join(current_chunk)

            # 检查是否该切
            should_split = False

            # 超过 max 强制切
            if len(current_text) >= self.max_chunk_size:
                should_split = True
            # 超过 min 且下一句相似度低则切
            elif len(current_text) >= self.min_chunk_size and i + 1 < len(grouped):
                sim = cosine_sim(all_embeddings[i], all_embeddings[i + 1])
                if sim < self.similarity_threshold:
                    should_split = True

            if should_split:
                chunks.append(Chunk(
                    content=current_text.strip(),
                    metadata={"seq": seq},
                ))
                seq += 1
                current_chunk = []

        # 最后一段
        if current_chunk:
            remaining = "".join(current_chunk).strip()
            if remaining:
                chunks.append(Chunk(content=remaining, metadata={"seq": seq}))

        logger.debug(f"[SemanticChunker] {len(chunks)} 块, threshold={self.similarity_threshold}")
        return chunks


class MarkerChunker(TextChunker):
    """
    标记切割
    按正则匹配的标题/标记位置切分。
    适用于规章、合同等结构化文档。
    """
    def __init__(
        self,
        markers: List[str] = None,
        max_chunk_size: int = 1500,
    ):
        self.markers = markers or ["第.*章", "第.*节", "一、", "二、", "\\d+\\.\\s+"]
        self.max_chunk_size = max_chunk_size

    def chunk(self, text: str, **kwargs) -> List[Chunk]:
        if not text:
            return []

        # 构建正则：任一标记匹配的位置
        pattern = "|".join(f"({m})" for m in self.markers)
        matches = list(re.finditer(pattern, text))

        if not matches:
            # 没有匹配到任何标记，回退到 fixed
            logger.info("[MarkerChunker] 未匹配到任何标记，回退到 fixed")
            fallback = FixedChunker(chunk_size=self.max_chunk_size, chunk_overlap=50)
            return fallback.chunk(text)

        chunks = []
        seq = 0

        for i, match in enumerate(matches):
            start = match.start()
            if i > 0:
                prev_start = matches[i - 1].start()
                segment = text[prev_start:start].strip()
            else:
                # 第一个标记之前的内容作为前言
                preamble = text[:start].strip()
                if preamble:
                    chunks.append(Chunk(content=preamble, metadata={"seq": seq, "type": "preamble"}))
                    seq += 1
                continue

            if segment:
                # 如果段太长，再按固定切
                if len(segment) > self.max_chunk_size:
                    sub_chunker = FixedChunker(chunk_size=self.max_chunk_size, chunk_overlap=50)
                    sub_chunks = sub_chunker.chunk(segment)
                    for sc in sub_chunks:
                        sc.metadata["seq"] = seq
                        chunks.append(sc)
                        seq += 1
                else:
                    chunks.append(Chunk(content=segment, metadata={"seq": seq}))
                    seq += 1

        # 最后一段
        last_segment = text[matches[-1].start():].strip()
        if last_segment:
            if len(last_segment) > self.max_chunk_size:
                sub_chunker = FixedChunker(chunk_size=self.max_chunk_size, chunk_overlap=50)
                sub_chunks = sub_chunker.chunk(last_segment)
                for sc in sub_chunks:
                    sc.metadata["seq"] = seq
                    chunks.append(sc)
                    seq += 1
            else:
                chunks.append(Chunk(content=last_segment, metadata={"seq": seq}))

        logger.debug(f"[MarkerChunker] {len(chunks)} 块, markers={self.markers}")
        return chunks


class TextChunkerFactory:
    """文本分块器工厂，根据配置创建对应的 Chunker"""

    @staticmethod
    def create() -> TextChunker:
        """
        从 config.json 读取 text_chunker 配置并创建对应实例

        config.json 示例:
          "text_chunker": {
            "strategy": "fixed",
            "strategies": {
              "fixed": { "chunk_size": 500, "chunk_overlap": 50 },
              ...
            }
          }
        """
        config = conf().get("text_chunker", {})
        strategy = config.get("strategy", "fixed")
        params = config.get("strategies", {}).get(strategy, {})

        logger.info(f"[TextChunkerFactory] 创建 chunker: strategy={strategy}, params={params}")

        if strategy == "fixed":
            return FixedChunker(
                chunk_size=params.get("chunk_size", 500),
                chunk_overlap=params.get("chunk_overlap", 50),
            )
        elif strategy == "delimiter":
            return DelimiterChunker(
                delimiter=params.get("delimiter", "\n\n"),
                max_chunk_size=params.get("max_chunk_size", 1000),
                min_chunk_size=params.get("min_chunk_size", 100),
            )
        elif strategy == "semantic":
            return SemanticChunker(
                max_chunk_size=params.get("max_chunk_size", 800),
                min_chunk_size=params.get("min_chunk_size", 200),
                similarity_threshold=params.get("similarity_threshold", 0.7),
            )
        elif strategy == "marker":
            return MarkerChunker(
                markers=params.get("markers", ["第.*章", "一、", "二、"]),
                max_chunk_size=params.get("max_chunk_size", 1500),
            )
        else:
            logger.warning(f"[TextChunkerFactory] 未知策略 '{strategy}'，使用 fixed")
            return FixedChunker()