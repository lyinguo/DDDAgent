"""
条件路由节点

根据输入内容类型，将请求路由到对应的处理分支。

路由逻辑（与原始毕昇工作流条件分支一致）:
  微信文章内容不为空 → wechat_article
  每日新闻内容不为空 → daily_news
  定时推送新闻不为空 → push_news
  图片列表不为空     → image
  文件内容不为空     → file
  其他               → normal（进一步判断是否需要RAG）
"""

from typing import Literal

from common.log import logger
from bot.langgraph_workflow.state import WorkflowState
from bot.langgraph_workflow.state import (
    ROUTE_NORMAL,
    ROUTE_WECHAT_ARTICLE,
    ROUTE_DAILY_NEWS,
    ROUTE_PUSH_NEWS,
    ROUTE_FILE,
    ROUTE_IMAGE,
)


def input_router(state: WorkflowState):
    """
    根据 state 中的内容通道字段判断路由分支
    设置 route 字段，路由由条件边函数 route_after_router 处理
    """
    # 微信文章
    if state.get("wechat_article_content"):
        logger.debug(f"[Router] 路由 -> 微信文章")
        return {"route": ROUTE_WECHAT_ARTICLE}

    # 每日新闻
    if state.get("daily_news_content"):
        logger.debug(f"[Router] 路由 -> 每日新闻")
        return {"route": ROUTE_DAILY_NEWS}

    # 定时推送新闻
    if state.get("push_daily_news_content"):
        logger.debug(f"[Router] 路由 -> 定时推送新闻")
        return {"route": ROUTE_PUSH_NEWS}

    # 图片
    if state.get("image_url_list"):
        logger.debug(f"[Router] 路由 -> 图片处理")
        return {"route": ROUTE_IMAGE}

    # 文件
    if state.get("dialog_files_content") or state.get("upload_file_url"):
        logger.debug(f"[Router] 路由 -> 文件处理")
        return {"route": ROUTE_FILE}

    # 普通对话（默认）
    logger.debug(f"[Router] 路由 -> 普通对话")
    return {"route": ROUTE_NORMAL}