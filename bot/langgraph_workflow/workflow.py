"""
LangGraph 工作流图构建

将各节点组装为 StateGraph，编译为可执行应用。

图结构（对应原毕昇工作流）:

输入 → input_router
  ├─ wechat_article → query_extractor → wechat_article → 输出
  ├─ daily_news    → query_extractor → news_handler   → 输出
  ├─ push_news     → news_handler                     → 输出
  ├─ image         → query_extractor → image_analysis → 输出
  ├─ file          → query_extractor → doc_summary    → 输出
  └─ normal        → context_builder → intent_judge
                      ├─ 需要RAG → query_extractor → query_rewrite → knowledge_qa → 输出
                      └─ 无需RAG → query_extractor → simple_reply  → 输出
"""

from typing import Literal

from langgraph.graph import StateGraph, END

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState, create_initial_state
from bot.langgraph_workflow.state import (
    ROUTE_NORMAL,
    ROUTE_WECHAT_ARTICLE,
    ROUTE_DAILY_NEWS,
    ROUTE_PUSH_NEWS,
    ROUTE_FILE,
    ROUTE_IMAGE,
    ROUTE_KNOWLEDGE,
    ROUTE_SIMPLE_REPLY,
)

from bot.langgraph_workflow.nodes.input_router import input_router
from bot.langgraph_workflow.nodes.context_builder import context_builder
from bot.langgraph_workflow.nodes.query_extractor import query_extractor
from bot.langgraph_workflow.nodes.query_rewrite import query_rewrite
from bot.langgraph_workflow.nodes.intent_judge import intent_judge
from bot.langgraph_workflow.nodes.simple_reply import simple_reply
from bot.langgraph_workflow.nodes.doc_summary import doc_summary
from bot.langgraph_workflow.nodes.wechat_article import wechat_article
from bot.langgraph_workflow.nodes.news_handler import news_handler
from bot.langgraph_workflow.nodes.image_analysis import image_analysis
from bot.langgraph_workflow.nodes.knowledge_qa import knowledge_qa
from bot.langgraph_workflow.nodes.file_intent_router import file_intent_router
from bot.langgraph_workflow.nodes.knowledge_ingest import knowledge_ingest


def route_after_router(state: WorkflowState) -> Literal[
    "wechat_article",
    "daily_news",
    "push_news",
    "image",
    "file",
    "normal",
]:
    """根据 route 字段路由到对应分支"""
    return state.get("route", ROUTE_NORMAL)


def route_after_intent(state: WorkflowState) -> Literal[
    "knowledge_qa",
    "simple_reply",
]:
    """根据意图判断结果路由"""
    if state.get("need_knowledge"):
        return "knowledge_qa"
    else:
        return "simple_reply"


def build_workflow() -> StateGraph:
    """
    构建工作流图
    :return: 编译后的 StateGraph
    """
    # 1. 创建图
    workflow = StateGraph(WorkflowState)

    # 2. 添加所有节点
    workflow.add_node("input_router", input_router)
    workflow.add_node("context_builder", context_builder)
    workflow.add_node("query_extractor", query_extractor)
    workflow.add_node("query_rewrite", query_rewrite)
    workflow.add_node("intent_judge", intent_judge)
    workflow.add_node("simple_reply", simple_reply)
    workflow.add_node("doc_summary", doc_summary)
    workflow.add_node("wechat_article", wechat_article)
    workflow.add_node("news_handler", news_handler)
    workflow.add_node("image_analysis", image_analysis)
    workflow.add_node("knowledge_qa", knowledge_qa)
    workflow.add_node("file_intent_router", file_intent_router)
    workflow.add_node("knowledge_ingest", knowledge_ingest)

    # 3. 设置入口
    workflow.set_entry_point("input_router")

    # 4. 入口路由: input_router → 各分支
    workflow.add_conditional_edges(
        "input_router",
        route_after_router,
        {
            ROUTE_NORMAL: "context_builder",
            ROUTE_WECHAT_ARTICLE: "query_extractor",
            ROUTE_DAILY_NEWS: "query_extractor",
            ROUTE_PUSH_NEWS: "news_handler",
            ROUTE_FILE: "file_intent_router",
            ROUTE_IMAGE: "query_extractor",
        },
    )

    # 4a. 文件意图路由: summarize → doc_summary, ingest → knowledge_ingest
    def route_after_file_intent(state):
        intent = state.get("file_intent", "summarize")
        return "knowledge_ingest" if intent == "ingest" else "query_extractor"

    workflow.add_conditional_edges(
        "file_intent_router",
        route_after_file_intent,
        {
            "knowledge_ingest": "knowledge_ingest",
            "query_extractor": "query_extractor",
        },
    )

    # 5. 普通对话分支
    workflow.add_edge("context_builder", "intent_judge")
    workflow.add_conditional_edges(
        "intent_judge",
        route_after_intent,
        {
            "knowledge_qa": "query_extractor",  # 需要RAG: 提取问题后继续
            "simple_reply": "query_extractor",  # 无需RAG: 提取问题后简单回复
        },
    )

    # 6. intent_judge → knowledge_qa 子分支
    workflow.add_edge("query_rewrite", "knowledge_qa")

    # 7. intent_judge → simple_reply
    workflow.add_edge("simple_reply", END)

    # 8. 各特殊分支的 query_extractor 后续路由
    # 注意: query_extractor 被多个入口使用，根据 route 字段判断去向
    def route_after_query_extractor(state: WorkflowState) -> str:
        route = state.get("route", ROUTE_NORMAL)
        if route == ROUTE_WECHAT_ARTICLE:
            return "wechat_article"
        elif route == ROUTE_DAILY_NEWS:
            return "news_handler"
        elif route == ROUTE_IMAGE:
            return "image_analysis"
        elif route == ROUTE_FILE:
            return "doc_summary"
        elif route == ROUTE_NORMAL:
            # intent_judge 已经在前面处理了
            # 如果没有 need_knowledge，说明是从 intent 分支来的
            if state.get("need_knowledge") is True:
                return "query_rewrite"
            else:
                # 到这里意味着是从 intent_judge → query_extractor → ?
                # intent_judge 已经决定了，直接路由
                if state.get("need_knowledge") is False:
                    return "simple_reply"
                # 兜底
                return "simple_reply"
        else:
            return END

    workflow.add_conditional_edges(
        "query_extractor",
        route_after_query_extractor,
        {
            "wechat_article": "wechat_article",
            "news_handler": "news_handler",
            "image_analysis": "image_analysis",
            "doc_summary": "doc_summary",
            "query_rewrite": "query_rewrite",
            "simple_reply": "simple_reply",
            END: END,
        },
    )

    # 9. 各处理节点 → END
    for node_name in [
        "wechat_article",
        "doc_summary",
        "image_analysis",
        "news_handler",
        "knowledge_qa",
        "knowledge_ingest",
    ]:
        workflow.add_edge(node_name, END)

    # 10. 编译
    app = workflow.compile()
    logger.info("[Workflow] LangGraph 工作流图构建完成")
    return app


# 全局编译实例
_compiled_app = None


def get_workflow():
    """
    获取编译后的工作流实例（带缓存）
    """
    global _compiled_app
    if _compiled_app is None:
        _compiled_app = build_workflow()
    return _compiled_app


def run_workflow(state: WorkflowState) -> WorkflowState:
    """
    执行工作流
    :param state: 初始状态
    :return: 执行完成后的状态
    """
    app = get_workflow()
    result = app.invoke(state)
    logger.debug(f"[Workflow] 工作流执行完成，route={result.get('route', '?')}")
    return result