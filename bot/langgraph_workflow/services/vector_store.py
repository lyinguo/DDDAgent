"""
向量数据库服务（Milvus 封装）

通过 config.json 中的 vector_store 配置连接参数:
  "vector_store": {
    "type": "milvus",
    "host": "localhost",
    "port": 19530,
    "collection_name": "knowledge_base",
    "dimension": 1536
  }
"""

from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
import uuid
import time

from common.log import logger
from config import conf


@dataclass
class VectorRecord:
    """向量库中的一条记录"""
    id: str
    vector: List[float]
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


class VectorStore:
    """向量数据库封装"""

    def __init__(self):
        config = conf().get("vector_store", {})
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 19530)
        self.collection_name = config.get("collection_name", "knowledge_base")
        self.dimension = config.get("dimension", 1536)
        self._connected = False
        self._collection = None

    def connect(self):
        """连接 Milvus"""
        if self._connected:
            return

        try:
            from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility

            connections.connect(
                alias="default",
                host=self.host,
                port=self.port,
            )

            # 检查 collection 是否存在
            if utility.has_collection(self.collection_name):
                self._collection = Collection(self.collection_name)
                self._collection.load()
                logger.info(f"[VectorStore] 连接 Milvus 成功, collection={self.collection_name}")
            else:
                self._create_collection()

            self._connected = True

        except Exception as e:
            logger.warning(f"[VectorStore] 连接 Milvus 失败: {e}")
            logger.warning("[VectorStore] 请确保 Milvus 已启动 (make up)")
            raise

    def _create_collection(self):
        """创建 Collection"""
        from pymilvus import Collection, CollectionSchema, FieldSchema, DataType, utility

        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dimension),
            FieldSchema(name="filename", dtype=DataType.VARCHAR, max_length=255),
            FieldSchema(name="chunk_seq", dtype=DataType.INT64),
        ]

        schema = CollectionSchema(fields, description="知识库向量集合")
        self._collection = Collection(
            name=self.collection_name,
            schema=schema,
        )

        # 创建索引
        index_params = {
            "metric_type": "IP",
            "index_type": "IVF_FLAT",
            "params": {"nlist": 128},
        }
        self._collection.create_index(
            field_name="embedding",
            index_params=index_params,
        )
        self._collection.load()

        logger.info(f"[VectorStore] 创建 collection 成功: {self.collection_name}")

    def insert(
        self,
        texts: List[str],
        vectors: List[List[float]],
        metadatas: Optional[List[Dict]] = None,
    ) -> List[str]:
        """
        插入向量数据

        :param texts: 文本列表
        :param vectors: 向量列表
        :param metadatas: 元数据列表
        :return: ID 列表
        """
        self.connect()

        if len(texts) != len(vectors):
            raise ValueError(f"texts({len(texts)}) 和 vectors({len(vectors)}) 数量不匹配")

        ids = []
        entities = []
        for i, (text, vec) in enumerate(zip(texts, vectors)):
            record_id = str(uuid.uuid4())
            ids.append(record_id)

            meta = metadatas[i] if metadatas and i < len(metadatas) else {}
            entities.append({
                "id": record_id,
                "text": text,
                "embedding": vec,
                "filename": meta.get("filename", ""),
                "chunk_seq": meta.get("seq", i),
            })

        self._collection.insert(entities)
        self._collection.flush()
        logger.info(f"[VectorStore] 插入 {len(entities)} 条记录")
        return ids

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
    ) -> List[VectorRecord]:
        """
        相似度搜索

        :param query_vector: 查询向量
        :param top_k: 返回条数
        :return: VectorRecord 列表
        """
        self.connect()

        search_params = {
            "metric_type": "IP",
            "params": {"nprobe": 16},
        }

        results = self._collection.search(
            data=[query_vector],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            output_fields=["id", "text", "filename", "chunk_seq"],
        )

        records = []
        for hits in results:
            for hit in hits:
                records.append(VectorRecord(
                    id=hit.id,
                    vector=[],
                    text=hit.entity.get("text") if hasattr(hit, 'entity') else "",
                    metadata={
                        "filename": hit.entity.get("filename") if hasattr(hit, 'entity') else "",
                        "chunk_seq": hit.entity.get("chunk_seq") if hasattr(hit, 'entity') else 0,
                    },
                    score=hit.score,
                ))

        logger.debug(f"[VectorStore] 搜索完成，返回 {len(records)} 条")
        return records

    def delete_by_filename(self, filename: str):
        """按文件名删除记录"""
        self.connect()
        expr = f'filename == "{filename}"'
        self._collection.delete(expr)
        logger.info(f"[VectorStore] 删除文件记录: {filename}")

    def get_stats(self) -> Dict:
        """获取统计信息"""
        self.connect()
        count = self._collection.num_entities
        return {
            "collection": self.collection_name,
            "total_entities": count,
            "dimension": self.dimension,
        }

    def close(self):
        """关闭连接"""
        if self._connected:
            from pymilvus import connections
            connections.disconnect("default")
            self._connected = False
            logger.info("[VectorStore] 连接已关闭")