"""
知识库入库节点

将文档分块 → 向量化 → 存入 Milvus
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.retriever import Retriever
from bot.langgraph_workflow.services.text_chunker import TextChunkerFactory


def knowledge_ingest(state: WorkflowState):
    """
    文档入库到向量知识库
    """
    file_content = state.get("dialog_files_content", "")
    # 从 chat_history 中取最后一条消息作为文件名提示
    filename = "上传文件"
    chat_history = state.get("chat_history", [])
    if chat_history and len(chat_history) > 0:
        last_msg = chat_history[-1].get("content", "")
        if last_msg:
            filename = last_msg[:50]

    if not file_content:
        return {"final_output": "文件内容为空，无法入库。"}

    try:
        # 1. 获取分块器
        chunker = TextChunkerFactory.create()

        # 2. 获取检索器（内含 embedding + vector store）
        retriever = Retriever()

        # 3. 分块 → 向量化 → 入库
        chunks_count = retriever.add_document(
            text=file_content,
            filename=filename,
            chunker=chunker,
        )

        logger.info(f"[KnowledgeIngest] 入库成功: {filename}, {chunks_count} 块")
        return {
            "final_output": f"文件已学习完成，共入库 {chunks_count} 条知识。"
        }

    except Exception as e:
        logger.exception(f"[KnowledgeIngest] 入库异常: {e}")
        return {"final_output": f"文件入库失败: {str(e)}"}