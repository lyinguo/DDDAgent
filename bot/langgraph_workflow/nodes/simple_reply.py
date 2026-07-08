"""
简单回复节点

当用户问题无需检索知识库时，直接由LLM生成回复。
对应原毕昇工作流的"简单回复大模型"节点。

使用default模型，输出格式遵循原工作流的模板规则。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

SYSTEM_PROMPT = """你是一位智能、高效且富有亲和力的企业级办公助手。

核心定位：你不是搜索框，而是有温度的合作伙伴。
语言风格：专业中带着热情，简洁直观。

输出规范：
1. 标题唯一性：全文只能有一个 # 一级标题，置于首行
2. 根据内容类型选择排版：
   - 结构化信息：使用 * 列表符呈现层级
   - 创作类内容：使用自然段落，代码用代码块包裹
3. 不要输出任何思考过程，直接输出最终结果"""


def simple_reply(state: WorkflowState):
    """
    生成简单回复（无需检索知识库）
    """
    last_question = state.get("last_question", "")
    user_input = state.get("user_input", "")
    question = last_question or user_input

    if not question:
        return {"final_output": ""}

    try:
        service = LLMServiceFactory.create("default")
        user_name = state.get("user_name", "")
        current_time = state.get("current_time", "")

        user_prompt = f"用户姓名：{user_name}\n当前时间：{current_time}\n用户当前问题：{question}\n你的回答："

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        result = service.chat(messages)
        final_output = result.strip()
        logger.debug(f"[SimpleReply] 回复生成成功，长度={len(final_output)}")
        return {"final_output": final_output}
    except Exception as e:
        logger.exception(f"[SimpleReply] 生成回复异常: {e}")
        return {"final_output": "抱歉，我现在有点忙，请稍后再试。"}