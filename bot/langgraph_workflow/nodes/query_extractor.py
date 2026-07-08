"""
提取用户最后问题节点

从 user_input 中提取用户的最新问题（去口语化）。
对应原毕昇工作流的多个"提取session对话的最后一个问题"节点。

使用轻量级LLM（light模型）进行提取。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

SYSTEM_PROMPT = """你是一个严谨的指令提取引擎。
你的唯一使命是从对话历史数据中，精准剥离出用户当前意图的最后一条原始指令。

规则:
1. 忽略所有 system 和 assistant 角色的内容
2. 只提取最后一条 user 消息
3. 原样输出，不改写、不总结、不加前缀后缀
4. 仅输出纯文本字符串

示例:
输入: [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好"}, {"role": "user", "content": "请假流程"}]
输出: 请假流程"""


def query_extractor(state: WorkflowState):
    """
    提取用户最后问题
    优先使用LLM提取，失败时回退到直接取 user_input
    """
    user_input = state.get("user_input", "")
    chat_history = state.get("chat_history", [])

    if not user_input and not chat_history:
        return {"last_question": ""}

    # 尝试用LLM提取
    try:
        service = LLMServiceFactory.create("light")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"对话历史: {chat_history}" if chat_history else f"用户输入: {user_input}"},
        ]
        result = service.chat(messages, temperature=0.1)
        result = result.strip()
        if result:
            logger.debug(f"[QueryExtractor] LLM提取结果: {result[:50]}")
            return {"last_question": result}
    except Exception as e:
        logger.warning(f"[QueryExtractor] LLM提取失败，回退到原始输入: {e}")

    # 回退：直接用 user_input
    logger.debug(f"[QueryExtractor] 使用原始输入: {user_input[:50]}")
    return {"last_question": user_input}