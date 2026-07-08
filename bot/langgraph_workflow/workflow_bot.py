"""
LangGraph 工作流 Bot 入口

与现有 Bot 基类接口兼容，替换原有的 BishengWorkflowBot。
"""

import os
import time
import base64
import requests
from bs4 import BeautifulSoup

from bot.bot import Bot
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.token_bucket import TokenBucket
from common.daily_news_fetcher import get_industry_news
from config import conf, load_config
from bot.session_manager import SessionManager
from bot.bisheng_workflow.bisheng_workflow_session import BishengWorkflowSession

from bot.langgraph_workflow.state import create_initial_state, WorkflowState
from bot.langgraph_workflow.workflow import get_workflow
from bot.langgraph_workflow.services.document_loader import DocumentLoader


class LangGraphWorkflowBot(Bot):
    """基于 LangGraph 的工作流 Bot"""

    def __init__(self):
        super().__init__()
        # 复用原有的 Session 管理
        self.sessions = SessionManager(BishengWorkflowSession, model="langgraph_workflow")
        # 限流
        rate_limit = conf().get("rate_limit_bisheng", 20)
        self.tb = TokenBucket(rate_limit)

    def reply(self, query, context=None):
        """
        处理消息并返回回复
        :param query: 用户查询内容
        :param context: 上下文对象
        :return: Reply 对象
        """
        if context.type not in [ContextType.TEXT, ContextType.FILE,
                                 ContextType.DAILY_NEWS, ContextType.IMAGE]:
            logger.warning(f"[LangGraphBot] 不支持的消息类型: {context.type}")
            return Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))

        logger.debug(f"[LangGraphBot] query={query}, context_type={context.type}")
        session_id = context["session_id"]

        # 处理特殊命令
        reply = self._handle_special_commands(query, session_id)
        if reply:
            return reply

        # 处理订阅新闻指令
        reply = self._handle_subscribe_daily_news_commands(query, context)
        if reply:
            return reply

        # 构建工作流状态
        state = self._build_workflow_state(query, context, session_id)

        # 限流检查
        if not self.tb.get_token():
            logger.warning("[LangGraphBot] 触发限流")
            return Reply(ReplyType.ERROR, "提问太快啦，请休息一下再问我吧")

        # 执行工作流
        try:
            result = get_workflow().invoke(state)
            final_output = result.get("final_output", "")
            route = result.get("route", "unknown")

            if not final_output:
                return Reply(ReplyType.ERROR, "抱歉，我暂时无法回答这个问题。")

            # 保存会话
            self.sessions.session_reply(final_output, session_id, len(final_output))
            self.sessions.trim_context(session_id)

            logger.info(f"[LangGraphBot] 回复成功, route={route}, 长度={len(final_output)}")
            return Reply(ReplyType.TEXT, final_output)

        except Exception as e:
            logger.exception(f"[LangGraphBot] 工作流执行异常: {e}")
            return Reply(ReplyType.ERROR, "抱歉，处理过程中出现错误，请稍后重试。")

    def _handle_special_commands(self, query: str, session_id: str):
        """处理特殊命令"""
        clear_commands = conf().get("clear_memory_commands", ["#清除记忆"])
        if query in clear_commands:
            self.sessions.clear_session(session_id)
            return Reply(ReplyType.INFO, "记忆已清除")
        elif query == "#清除所有":
            self.sessions.clear_all_session()
            return Reply(ReplyType.INFO, "所有人记忆已清除")
        elif query == "#更新配置":
            load_config()
            return Reply(ReplyType.INFO, "配置已更新")
        return None

    def _handle_subscribe_daily_news_commands(self, query: str, context: Context):
        """处理订阅新闻指令"""
        subscribe_cmd = conf().get("bisheng_daily_news_subscribe_command", "订阅新闻")
        unsubscribe_cmd = conf().get("bisheng_daily_news_unsubscribe_command", "取消订阅")
        status_cmd = conf().get("bisheng_daily_news_status_command", "查看订阅")
        push_time = conf().get("bisheng_daily_news_push_time", "09:00")

        if query not in [subscribe_cmd, unsubscribe_cmd, status_cmd]:
            return None
        if context.get("isgroup") is False:
            return Reply(ReplyType.INFO, "私聊暂不支持订阅新闻等操作！")

        from plugins.daily_news.daily_news_subscribed_group_manager import SubscribedGroupManager
        manager = SubscribedGroupManager()
        group_name = context.get("msg").incoming_message.conversation_title

        if query == subscribe_cmd:
            if manager.subscribe(group_name):
                return Reply(ReplyType.INFO, f"每日新闻订阅成功！推送时间每工作日{push_time}")
            return Reply(ReplyType.INFO, "已订阅，无需重复操作")
        elif query == unsubscribe_cmd:
            if manager.unsubscribe(group_name):
                return Reply(ReplyType.INFO, "已取消订阅每日新闻")
            return Reply(ReplyType.INFO, "未订阅，无需取消")
        elif query == status_cmd:
            groups = manager.get_all_subscribed_groups()
            if groups:
                text = "\n".join([f"  · {name}" for name in groups])
                return Reply(ReplyType.INFO, f"当前已订阅的群聊({len(groups)}个)：\n{text}")
            return Reply(ReplyType.INFO, "当前没有群聊订阅每日新闻")

    def _build_workflow_state(self, query: str, context: Context, session_id: str) -> WorkflowState:
        """
        根据消息类型构建工作流状态
        """
        # 获取用户信息
        user_name = context["msg"].incoming_message.sender_nick
        user_title = getattr(context['msg'], 'from_user_title', '')

        # 获取对话历史
        session = self.sessions.session_query(query, session_id)
        chat_history = session.messages if session else []

        # 创建初始状态
        state = create_initial_state(
            user_input=query,
            user_name=user_name,
            user_title=user_title,
            session_id=session_id,
            chat_history=chat_history,
        )

        # 根据消息类型填充内容通道
        if context.type == ContextType.FILE:
            state["dialog_files_content"] = self._process_file_locally(context)
            state["upload_file_url"] = ""

        elif context.type == ContextType.IMAGE:
            image_urls = self._process_image_locally(context)
            state["image_url_list"] = image_urls

        elif context.type == ContextType.DAILY_NEWS:
            state["push_daily_news_content"] = query
            state["user_input"] = conf().get(
                "bisheng_workflow_default_query_news",
                "请快速帮我今日行业热点新闻内容。"
            )

        # 处理主动查询新闻
        daily_news_commands = conf().get("bisheng_daily_news_commands", ["今日新闻"])
        if query in daily_news_commands:
            state["daily_news_content"] = get_industry_news()
            state["user_input"] = conf().get(
                "bisheng_workflow_default_query_news",
                "请快速帮我今日行业热点新闻内容。"
            )

        # 处理微信公众号链接
        if query.startswith("https://mp.weixin.qq.com/"):
            state["wechat_article_content"] = self._fetch_wechat_article_content(query)
            state["user_input"] = conf().get(
                "bisheng_workflow_default_query_wechat",
                "请快速帮我总结一下这个文章的内容。"
            )

        return state

    def _process_file_locally(self, context: Context) -> str:
        """本地读取文件内容"""
        file_path = context.get("file_path")
        file_name = context.get("file_name", "未知文件")

        if not file_path or not os.path.exists(file_path):
            logger.error(f"[LangGraphBot] 文件不存在: {file_path}")
            return ""

        # 用 DocumentLoader 读取文件内容
        doc = DocumentLoader.load(file_path)
        if doc is None:
            logger.error(f"[LangGraphBot] 文件读取失败: {file_name}")
            return ""

        logger.info(f"[LangGraphBot] 文件读取成功: {file_name}, {len(doc.content)} 字符")
        return doc.content

    def _process_image_locally(self, context: Context) -> list:
        """本地处理图片，编码为 base64 data URI"""
        image_urls = []
        image_paths = context.get("msg").image_url_list or []
        for path in image_paths:
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode("utf-8")
                    ext = os.path.splitext(path)[1].lower().lstrip(".")
                    if ext in ["jpg", "jpeg"]:
                        mime = "image/jpeg"
                    elif ext == "png":
                        mime = "image/png"
                    elif ext == "gif":
                        mime = "image/gif"
                    elif ext == "webp":
                        mime = "image/webp"
                    else:
                        mime = "image/jpeg"
                    data_uri = f"data:{mime};base64,{img_data}"
                    image_urls.append(data_uri)
                    logger.debug(f"[LangGraphBot] 图片编码成功: {path}")
                except Exception as e:
                    logger.warning(f"[LangGraphBot] 图片编码失败: {path}, {e}")
            else:
                # 如果不是本地路径，可能是 URL，直接使用
                image_urls.append(path)
        return image_urls

    def _fetch_wechat_article_content(self, url: str) -> str:
        """获取微信公众号文章内容"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/91.0.4472.124 Safari/537.36"
        }
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            content = soup.select_one("#js_content")
            if content:
                return content.get_text(separator="\n", strip=True)
            return ""
        except Exception as e:
            logger.exception(f"[LangGraphBot] 获取微信文章失败: {e}")
            return ""