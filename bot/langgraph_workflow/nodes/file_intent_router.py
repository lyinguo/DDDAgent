"""
文件意图识别节点

用户上传文件时，判断用户意图：
  1. 总结 → 走 doc_summary 分支
  2. 入库 → 走 knowledge_ingest 分支

判断依据仅为当前用户消息 + 文件名，不需要对话历史。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

SYSTEM_PROMPT = """你是一个文件意图识别专家。

用户的文件已经上传，请根据用户的消息判断意图，只输出一个字：

- 如果用户要求"总结"、"概括"、"提炼"、"摘要"、"内容"、"看看"、"读一下"等 → 输出：总
- 如果用户要求"入库"、"学习"、"记住"、"添加到知识库"、"保存"、"存入"、"训练"等 → 输出：入
- 如果消息为空或不确定 → 输出：总（默认为总结）

仅输出一个字：总 或 入"""


def file_intent_router(state: WorkflowState):
    """
    判断用户上传文件的意图
    """
    user_input = state.get("user_input", "")
    file_content = state.get("dialog_files_content", "")
    filename = state.get("chat_history", [{}])[-1].get("content", "") if state.get("chat_history") else ""

    if not file_content:
        return {"file_intent": "summarize"}

    try:
        service = LLMServiceFactory.create("light")
        prompt = f"用户消息：{user_input}\n文件名：{filename}\n输出："
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        result = service.chat(messages, temperature=0.1)
        result = result.strip()

        if "入" in result:
            logger.debug(f"[FileIntent] 入库: user_input={user_input[:30]}")
            return {"file_intent": "ingest"}
        else:
            logger.debug(f"[FileIntent] 总结: user_input={user_input[:30]}")
            return {"file_intent": "summarize"}

    except Exception as e:
        logger.warning(f"[FileIntent] LLM 判断失败，默认总结: {e}")
        return {"file_intent": "summarize"}