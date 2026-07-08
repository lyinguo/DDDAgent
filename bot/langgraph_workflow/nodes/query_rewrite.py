"""
检索查询重写节点

将用户的最后问题重写为更适合知识库检索的独立查询语句。
对应原毕昇工作流的"提取用户检索知识库的最终问题"节点。

功能:
1. 指代消解 — 将"它"、"这个"等代词替换为具体的实体
2. 完整补全 — 将省略句/追问补全为完整问题
3. 去口语化 — 移除寒暄词，保留核心查询内容
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

SYSTEM_PROMPT = """你是一个专业的"搜索查询重写"专家。将用户的最后一句输入，转化为一个独立、完整、无歧义的检索问题。

规则:
1. 指代消解: 如果包含"它"、"这个"、"那边"等代词，结合上下文找到对应实体
2. 完整补全: 追问/省略句补全为完整问题（如"公积金"→"公积金的缴纳比例和提取规则是什么？"）
3. 去口语化: 移除"你好"、"请问"等寒暄词，保留核心查询
4. 直接输出重写后的问题，不要任何解释或前缀"""


def query_rewrite(state: WorkflowState):
    """
    重写用户问题为检索友好的查询
    """
    last_question = state.get("last_question", "")
    user_input = state.get("user_input", "")
    question = last_question or user_input

    if not question:
        return {"rewritten_query": ""}

    try:
        service = LLMServiceFactory.create("light")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"用户输入: {question}"},
        ]
        result = service.chat(messages, temperature=0.1)
        result = result.strip()
        if result:
            logger.debug(f"[QueryRewrite] 重写结果: {result[:80]}")
            return {"rewritten_query": result}
    except Exception as e:
        logger.warning(f"[QueryRewrite] LLM重写失败，使用原问题: {e}")

    return {"rewritten_query": question}