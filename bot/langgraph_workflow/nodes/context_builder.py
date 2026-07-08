"""
提取上下文最后两条消息

从 chat_history 中提取最后两条消息（User+Assistant），
用于判断用户意图时的上下文参考。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

# 提取上下文消息的系统提示词（使用简单规则而非LLM调用）
SYSTEM_PROMPT = """你是一个上下文提取器。从对话历史中提取最后两条消息。
仅输出最后一条User消息和前一条Assistant消息，保持原样不修改。"""


def context_builder(state: WorkflowState):
    """
    提取上下文最后两条消息
    使用LLM提取更准确，但也可以直接取chat_history最后两条
    """
    chat_history = state.get("chat_history", [])

    if not chat_history:
        logger.debug("[ContextBuilder] chat_history 为空")
        return {"context_messages": []}

    # 直接取最后两条
    user_msgs = [msg for msg in chat_history if msg.get("role") == "user"]
    assistant_msgs = [msg for msg in chat_history if msg.get("role") == "assistant"]

    if user_msgs and assistant_msgs:
        context_msgs = [
            {"role": "assistant", "content": assistant_msgs[-1]["content"]},
            {"role": "user", "content": user_msgs[-1]["content"]},
        ]
    elif user_msgs:
        context_msgs = [{"role": "user", "content": user_msgs[-1]["content"]}]
    else:
        context_msgs = chat_history[-2:] if len(chat_history) >= 2 else chat_history

    logger.debug(f"[ContextBuilder] 提取上下文: {len(context_msgs)} 条消息")
    return {"context_messages": context_msgs}