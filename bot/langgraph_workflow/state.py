"""
LangGraph 工作流 State 定义

所有节点共享的状态，包含输入字段、中间结果、输出字段。
"""

from typing import List, Dict, Optional, Any, TypedDict, Annotated
from dataclasses import dataclass, field


class WorkflowState(TypedDict):
    """
    工作流状态，所有节点共享

    字段说明:
      user_input:      用户原始输入文本
      user_name:       用户姓名（来自钉钉/微信等渠道）
      user_title:      用户职位/身份
      current_time:    当前时间字符串

      chat_history:    对话历史列表 [{"role": "user", "content": "..."}, ...]
      session_id:      会话ID

      # 内容通道 —— 根据消息类型填充
      dialog_files_content:     上传文件内容文本
      wechat_article_content:   微信公众号文章内容
      daily_news_content:       每日新闻内容（主动获取）
      push_daily_news_content:  定时推送新闻内容
      upload_file_url:          上传文件后的URL
      image_url_list:           图片URL列表

      # 路由标识
      route:            当前路由分支标识

      # 中间结果
      context_messages: 提取的上下文最后两条消息
      last_question:    提取后的用户最新问题（去口语化）
      need_knowledge:   是否需要知识库检索（布尔值）
      rewritten_query:  重写后的检索查询语句
      retrieved_context: 知识库检索结果文本

      # 输出
      final_output:     最终回复内容
      system_prompt:    系统提示词（可动态设置）
    """
    # 输入
    user_input: str
    user_name: str
    user_title: str
    current_time: str

    # 会话
    chat_history: List[Dict[str, str]]
    session_id: str

    # 内容通道（可空）
    dialog_files_content: Optional[str]
    wechat_article_content: Optional[str]
    daily_news_content: Optional[str]
    push_daily_news_content: Optional[str]
    upload_file_url: Optional[str]
    image_url_list: Optional[List[str]]

    # 路由标识
    route: str
    file_intent: str                         # 文件意图: "summarize" 或 "ingest"

    # 中间结果（由各节点填充）
    context_messages: Optional[List[Dict[str, str]]]
    last_question: Optional[str]
    need_knowledge: Optional[bool]
    rewritten_query: Optional[str]
    retrieved_context: Optional[str]

    # 输出
    final_output: Optional[str]
    system_prompt: Optional[str]


def create_initial_state(
    user_input: str = "",
    user_name: str = "",
    user_title: str = "",
    session_id: str = "",
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> WorkflowState:
    """
    创建工作流初始状态
    :param user_input: 用户输入
    :param user_name: 用户姓名
    :param user_title: 用户职位
    :param session_id: 会话ID
    :param chat_history: 对话历史
    :return: 初始状态
    """
    from datetime import datetime

    return {
        # 输入
        "user_input": user_input or "",
        "user_name": user_name or "",
        "user_title": user_title or "",
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        # 会话
        "chat_history": chat_history or [],
        "session_id": session_id or "",

        # 内容通道（全部初始为空）
        "dialog_files_content": None,
        "wechat_article_content": None,
        "daily_news_content": None,
        "push_daily_news_content": None,
        "upload_file_url": None,
        "image_url_list": None,

        # 路由
        "route": "normal",
        "file_intent": "summarize",

        # 中间结果
        "context_messages": None,
        "last_question": None,
        "need_knowledge": None,
        "rewritten_query": None,
        "retrieved_context": None,

        # 输出
        "final_output": None,
        "system_prompt": None,
    }


# 路由常量
ROUTE_NORMAL = "normal"                # 普通对话
ROUTE_WECHAT_ARTICLE = "wechat_article"  # 微信文章
ROUTE_DAILY_NEWS = "daily_news"        # 每日新闻
ROUTE_PUSH_NEWS = "push_news"          # 定时推送新闻
ROUTE_FILE = "file"                    # 文件上传
ROUTE_IMAGE = "image"                  # 图片处理
ROUTE_KNOWLEDGE = "knowledge"          # 需要知识库检索
ROUTE_SIMPLE_REPLY = "simple_reply"    # 无需检索，简单回复