"""
判断用户意图节点

判断用户当前问题是否需要检索知识库才能回答。
对应原毕昇工作流的"判断用户意图大模型"节点。

输出: "是" 或 "否"（字符串形式，路由节点根据此判断）
"""

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.services.llm_service import LLMServiceFactory

SYSTEM_PROMPT = """你是一个企业内部知识库的守门员。
判断用户的最新提问是否需要检索"公司内部数据库/员工手册/业务文档"才能回答。

输入是对话历史列表，最后一条User消息是判断对象。

决定规则：
✅ 需要检索（输出"是"）：
1. 公司概况类：问公司介绍、组织架构、部门职能、企业文化等
2. 行政/人事/财务类：请假、报销、考勤、工资、福利、社保、合同、离职等
3. IT/工具/权限类：VPN、WIFI、邮箱、账号、系统登录等
4. 业务/制度类：审批流、签字、盖章、合同审核、业务规范等
5. 短语/追问：上文在谈制度，追问"那这个呢？"、"多久？"

❌ 无需检索（输出"否"）：
1. 通用技能指令：翻译、写代码、润色邮件、Excel公式等
2. 纯闲聊/情感交互：你好、你是谁、讲个笑话等
3. 纯外部公共知识：天气、汇率、法律条文（除非问"公司产假怎么休"）

仅输出单个字符：是 或 否"""


def intent_judge(state: WorkflowState):
    """
    判断用户是否需要知识库检索
    """
    last_question = state.get("last_question", "")
    user_input = state.get("user_input", "")
    context_msgs = state.get("context_messages", [])
    question = last_question or user_input

    if not question:
        return {"need_knowledge": False}

    try:
        service = LLMServiceFactory.create("light")
        context_text = str(context_msgs) if context_msgs else f"用户输入: {question}"
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": context_text},
        ]
        result = service.chat(messages, temperature=0.1)
        result = result.strip()
        need_knowledge = "是" in result
        logger.debug(f"[IntentJudge] 判断结果: need_knowledge={need_knowledge}")
        return {"need_knowledge": need_knowledge}
    except Exception as e:
        logger.warning(f"[IntentJudge] 判断失败，默认无需检索: {e}")
        return {"need_knowledge": False}