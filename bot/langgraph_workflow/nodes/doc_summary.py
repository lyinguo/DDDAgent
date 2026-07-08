"""
文档总结节点

处理用户上传的文件，根据文件内容回答用户问题。
对应原毕昇工作流的"快速总结文档大模型"节点。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

SYSTEM_PROMPT = """你是一位拥有敏锐洞察力的文档智能分析专家。

核心任务：根据用户上传的文件内容精准回答用户提问。

规则：
1. 精准提取：从文件内容中定位与问题最相关的片段，严禁编造
2. 视角适配：高管侧重宏观结论，执行层侧重具体步骤
3. 严格边界：你的知识仅限于文件内容，文件中没有答案必须诚实告知
4. 禁止输出思考过程，直接输出最终结果"""


def doc_summary(state: WorkflowState):
    """
    文档总结
    """
    file_content = state.get("dialog_files_content", "")
    last_question = state.get("last_question", "")
    user_input = state.get("user_input", "")
    question = last_question or user_input

    if not file_content:
        return {"final_output": "抱歉，未检测到文件内容，请重新上传。"}

    try:
        service = LLMServiceFactory.create("default")
        user_name = state.get("user_name", "")
        user_title = state.get("user_title", "")

        user_prompt = (
            f"用户姓名：{user_name}\n"
            f"用户职位：{user_title}\n"
            f"用户当前问题：{question}\n"
            f"用户上传文件：{file_content[:15000]}\n"
            f"你的回答："
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        result = service.chat(messages)
        final_output = result.strip()
        logger.debug(f"[DocSummary] 文档总结成功，长度={len(final_output)}")
        return {"final_output": final_output}
    except Exception as e:
        logger.exception(f"[DocSummary] 文档总结异常: {e}")
        return {"final_output": "抱歉，文档总结过程中出现错误，请稍后重试。"}