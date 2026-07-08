"""
知识库问答节点

根据用户问题从知识库检索相关内容，结合检索结果生成回答。
对应原毕昇工作流的"文档知识库问答"节点。

包含两个步骤:
1. 检索知识库（调用 knowledge_service）
2. 结合检索结果调用 LLM 生成回答
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory
from bot.langgraph_workflow.services.knowledge_service import KnowledgeServiceFactory

SYSTEM_PROMPT = """你是一位既严谨专业又得体贴心的公司制度智能助手。

核心身份：你不仅是制度的百科全书，更是员工值得信赖的职场伙伴。

工作流程：
1. 基于用户职位，从参考文本中提取最匹配的条款
2. 结合用户姓名和当前时间，在文末提供一句暖心的职场关怀
3. 注意公司工作时间：上午9:00-12:00，午休12:00-13:30，下午13:30-17:40

输出规范：
- 全文只有一个 # 一级标题
- 使用 * 开头的列表呈现内容层级
- 严禁使用 ## 或 ### 二级标题
- 回答结束后，用 *(关怀语)* 格式添加暖心关怀

如果参考文本中没有相关信息，请诚实告知用户。"""


def knowledge_qa(state: WorkflowState):
    """
    知识库问答：检索 + 生成
    """
    rewritten_query = state.get("rewritten_query", "")
    last_question = state.get("last_question", "")
    user_input = state.get("user_input", "")
    question = rewritten_query or last_question or user_input

    if not question:
        return {"final_output": "请告诉我您想了解什么？"}

    # 1. 检索知识库
    retrieved_text = ""
    try:
        knowledge_service = KnowledgeServiceFactory.create()
        response = knowledge_service.retrieve(question)

        if response.error:
            logger.warning(f"[KnowledgeQA] 知识库检索失败: {response.error}")
        elif response.is_empty:
            logger.info(f"[KnowledgeQA] 知识库未检索到相关内容")
        else:
            retrieved_text = response.to_context_text()
            logger.debug(
                f"[KnowledgeQA] 检索成功，{len(response.results)} 条结果"
            )
    except Exception as e:
        logger.warning(f"[KnowledgeQA] 检索异常: {e}")

    # 2. LLM 生成回答
    try:
        service = LLMServiceFactory.create("default")
        user_name = state.get("user_name", "")
        user_title = state.get("user_title", "")
        current_time = state.get("current_time", "")

        user_prompt = (
            f"用户姓名：{user_name}\n"
            f"用户职位：{user_title}\n"
            f"当前时间：{current_time}\n"
            f"用户当前问题：{question}\n"
            f"参考文本：{retrieved_text if retrieved_text else '（未检索到相关文档）'}\n"
            f"你的回答："
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        result = service.chat(messages)
        final_output = result.strip()
        logger.debug(f"[KnowledgeQA] 回答生成成功，长度={len(final_output)}")
        return {"final_output": final_output}
    except Exception as e:
        logger.exception(f"[KnowledgeQA] 回答生成异常: {e}")
        return {"final_output": "抱歉，知识库查询过程中出现错误，请稍后重试。"}