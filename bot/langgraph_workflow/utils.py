"""
工作流工具函数
"""

from typing import List, Dict, Optional
from bot.langgraph_workflow.state import WorkflowState


def build_llm_messages(
    state: WorkflowState,
    system_prompt: str,
    user_prompt: str,
) -> List[Dict[str, str]]:
    """
    构建 LLM 调用消息列表，自动注入对话历史

    规则:
      1. system_prompt 作为系统消息
      2. chat_history 中除最后一条 user 消息外的内容作为上下文
      3. user_prompt 作为当前用户消息

    :param state: 工作流状态（含 chat_history）
    :param system_prompt: 系统提示词
    :param user_prompt: 当前用户提示词
    :return: 消息列表
    """
    chat_history = state.get("chat_history", [])
    messages = [{"role": "system", "content": system_prompt}]

    if chat_history:
        # 过滤掉 system 消息，排除最后一条 user 消息（当前提问）
        conversation = [
            m for m in chat_history
            if m.get("role") != "system"
        ]
        # 如果最后一条是 user 消息，去掉它（当前提问会单独传入）
        if conversation and conversation[-1].get("role") == "user":
            conversation = conversation[:-1]

        messages.extend(conversation)

    messages.append({"role": "user", "content": user_prompt})
    return messages


def build_simple_user_prompt(
    state: WorkflowState,
    question: str,
    extra_context: Optional[str] = None,
) -> str:
    """
    构建标准用户提示词（含用户信息 + 可选额外上下文）
    """
    user_name = state.get("user_name", "")
    user_title = state.get("user_title", "")
    current_time = state.get("current_time", "")

    parts = []
    if user_name:
        parts.append(f"用户姓名：{user_name}")
    if user_title:
        parts.append(f"用户职位：{user_title}")
    if current_time:
        parts.append(f"当前时间：{current_time}")
    if extra_context:
        parts.append(extra_context)
    parts.append(f"用户当前问题：{question}")
    parts.append("你的回答：")

    return "\n".join(parts)