"""
每日新闻处理节点

处理每日行业新闻内容，包括:
1. 主动查询：用户发送"今日新闻"等指令时触发
2. 定时推送：系统定时推送行业新闻

对应原毕昇工作流的"每日新闻内容总结以及格式调整"和"每日定时新闻处理"节点。
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory
from bot.langgraph_workflow.utils import build_llm_messages

SYSTEM_PROMPT = """你是一个高时效性的新闻聚合助手。将新闻转化为具有视觉冲击力的纯文本块简报。

格式规范：
1. 标题: 全文仅包含一个一级标题 `# 今日行业新闻`
2. 每条新闻占两行：
   - 标题行: `**序号. [时间] [标题](链接)**`
   - 摘要行: 开头缩进，末尾加 `<br>`
3. 保留原标题，不改写
4. 时间格式: MM-DD HH:mm
5. 链接嵌入在标题文本中

注意：不要使用 Markdown 列表语法（如 1. 或 - 开头）"""


def news_handler(state: WorkflowState):
    """
    处理每日新闻
    """
    news_content = (
        state.get("push_daily_news_content", "")
        or state.get("daily_news_content", "")
    )

    if not news_content:
        return {"final_output": "暂无新闻内容。"}

    try:
        service = LLMServiceFactory.create("default")
        user_prompt = f"新闻输入：{news_content[:15000]}\n你的回答："
        messages = build_llm_messages(state, SYSTEM_PROMPT, user_prompt)
        result = service.chat(messages)
        final_output = result.strip()
        logger.debug(f"[NewsHandler] 新闻处理成功，长度={len(final_output)}")
        return {"final_output": final_output}
    except Exception as e:
        logger.exception(f"[NewsHandler] 新闻处理异常: {e}")
        return {"final_output": "抱歉，新闻处理过程中出现错误。"}