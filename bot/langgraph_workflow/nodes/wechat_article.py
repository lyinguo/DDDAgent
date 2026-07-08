"""
微信文章总结节点

处理微信公众号文章链接，根据文章内容回答用户问题。
对应原毕昇工作流的"快速总结微信文章内容"节点。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory
from bot.langgraph_workflow.utils import build_llm_messages, build_simple_user_prompt

SYSTEM_PROMPT = """你是一位拥有敏锐洞察力的文档智能分析专家。

核心任务：根据微信公众号文章内容精准回答用户提问。

规则：
1. 精准提取：从文章内容中定位与问题最相关的片段，严禁编造
2. 视角适配：根据用户职位调整回答侧重点
3. 严格边界：你的知识仅限于文章内容，未提及的必须诚实告知
4. 直接输出最终结果，不要输出思考过程"""


def wechat_article(state: WorkflowState):
    """
    总结微信公众号文章
    """
    article_content = state.get("wechat_article_content", "")
    last_question = state.get("last_question", "")
    user_input = state.get("user_input", "")
    question = last_question or user_input

    if not article_content:
        return {"final_output": "抱歉，未获取到微信公众号文章内容。"}

    try:
        service = LLMServiceFactory.create("default")
        user_prompt = build_simple_user_prompt(state, question, f"微信文章内容：{article_content[:15000]}")
        messages = build_llm_messages(state, SYSTEM_PROMPT, user_prompt)
        result = service.chat(messages)
        final_output = result.strip()
        logger.debug(f"[WechatArticle] 文章总结成功，长度={len(final_output)}")
        return {"final_output": final_output}
    except Exception as e:
        logger.exception(f"[WechatArticle] 文章总结异常: {e}")
        return {"final_output": "抱歉，文章总结过程中出现错误，请稍后重试。"}