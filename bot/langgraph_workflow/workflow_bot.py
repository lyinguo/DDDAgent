"""
LangGraph 工作流 Bot 入口
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
from bot.langgraph_workflow.services.file_store import FileStore
from bot.langgraph_workflow.services.task_handler import handle_task_command
from bot.langgraph_workflow.services.task_scheduler import TaskScheduler


class LangGraphWorkflowBot(Bot):

    def __init__(self):
        super().__init__()
        self.sessions = SessionManager(BishengWorkflowSession, model="langgraph_workflow")
        rate_limit = conf().get("rate_limit_bisheng", 20)
        self.tb = TokenBucket(rate_limit)
        self.file_store = FileStore()
        self._dingtalk_http = None
        # 启动任务调度器
        TaskScheduler().start()

    def _get_dingtalk_http(self):
        if self._dingtalk_http is None:
            try:
                from channel.dingtalk.dingtalk_http_client import DingtalkHttp
                self._dingtalk_http = DingtalkHttp()
            except Exception as e:
                logger.warning(f"[LangGraphBot] 钉钉客户端初始化失败: {e}")
        return self._dingtalk_http

    def _fetch_file_from_dingtalk(self, context, session_id):
        try:
            conversation_id = context.get("msg").incoming_message.conversation_id
            if not conversation_id:
                return None, None
            http = self._get_dingtalk_http()
            if not http:
                return None, None
            file_msgs = http.fetch_recent_file_messages(conversation_id, max_results=5)
            if not file_msgs:
                return None, None
            latest = file_msgs[0]
            download_code = latest["download_code"]
            filename = latest["filename"]
            if hasattr(context.get("msg"), "image_download_handler"):
                download_url = context["msg"].image_download_handler.get_image_download_url(download_code)
                if download_url:
                    from channel.dingtalk.dingtalk_message import download_image_file
                    from common.tmp_dir import TmpDir
                    file_path = download_image_file(download_url, TmpDir().path())
                    if file_path and os.path.exists(file_path):
                        record = self.file_store.save_file(file_path, session_id, filename)
                        if record:
                            content = self.file_store.read_file_content(record)
                            return content, filename
            return None, None
        except Exception as e:
            logger.exception(f"[LangGraphBot] 从钉钉拉取文件异常: {e}")
            return None, None

    def _handle_file_message(self, context, session_id):
        file_path = context.get("file_path")
        file_name = context.get("file_name", "文件")
        if not file_path or not os.path.exists(file_path):
            return Reply(ReplyType.ERROR, "文件读取失败，请重新发送。")
        record = self.file_store.save_file(file_path, session_id, file_name)
        if record is None:
            return Reply(ReplyType.ERROR, "文件保存失败。")
        return Reply(ReplyType.TEXT, f"文件《{record.filename}》已收到并保存。需要我总结还是入库？直接告诉我。")

    def _resolve_file_from_query(self, query, session_id):
        file_record = self.file_store.get_file_by_name(session_id, query)
        if file_record is None:
            file_record = self.file_store.get_latest_file(session_id)
        if file_record is None:
            return None, None
        content = self.file_store.read_file_content(file_record)
        if not content:
            return None, None
        return content, file_record.filename

    def _process_with_file(self, query, context, session_id, file_content, filename):
        state = self._build_workflow_state(query, context, session_id)
        state["dialog_files_content"] = file_content
        state["user_input"] = query
        if not self.tb.get_token():
            return Reply(ReplyType.ERROR, "提问太快啦，请休息一下再问我吧")
        try:
            result = get_workflow().invoke(state)
            final_output = result.get("final_output", "")
            if not final_output:
                return Reply(ReplyType.ERROR, "抱歉，我暂时无法回答这个问题。")
            self.sessions.session_reply(final_output, session_id, len(final_output))
            self.sessions.trim_context(session_id)
            return Reply(ReplyType.TEXT, final_output)
        except Exception as e:
            logger.exception(f"[LangGraphBot] 文件处理异常: {e}")
            return Reply(ReplyType.ERROR, "处理文件时出现错误，请稍后重试。")

    def reply(self, query, context=None):
        if context.type not in [ContextType.TEXT, ContextType.FILE,
                                 ContextType.DAILY_NEWS, ContextType.IMAGE]:
            return Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))

        session_id = context["session_id"]

        # 特殊命令
        r = self._handle_special_commands(query, session_id)
        if r:
            return r

        # 订阅新闻命令
        r = self._handle_subscribe_daily_news_commands(query, context)
        if r:
            return r

        # ==== 任务指令（不走 LangGraph，省 token）====
        if context.type == ContextType.TEXT:
            user_name = context["msg"].incoming_message.sender_nick
            user_id = context["msg"].actual_user_id
            is_group = context.get("isgroup", False)
            group_name = context.get("msg").incoming_message.conversation_title or ""
            task_reply = handle_task_command(query, user_name, user_id, is_group, group_name)
            if task_reply:
                return Reply(ReplyType.TEXT, task_reply)

        # 文件消息
        if context.type == ContextType.FILE:
            return self._handle_file_message(context, session_id)

        # 文本消息：文件相关
        if context.type == ContextType.TEXT:
            file_keywords = ["总结", "文档", "文件", "入库", "学习", "知识库", "读一下", "看看", "pdf"]
            if any(kw in query for kw in file_keywords):
                file_content, filename = self._resolve_file_from_query(query, session_id)
                if file_content:
                    return self._process_with_file(query, context, session_id, file_content, filename)
                file_content, filename = self._fetch_file_from_dingtalk(context, session_id)
                if file_content:
                    return self._process_with_file(query, context, session_id, file_content, filename)
                return Reply(ReplyType.TEXT, "未找到相关文件，请先发送文件再告诉我需要总结还是入库。")

        if context.type == ContextType.IMAGE:
            for p in (context.get("msg").image_url_list or []):
                if os.path.exists(p):
                    self.file_store.save_file(p, session_id, f"图片_{os.path.basename(p)}")

        state = self._build_workflow_state(query, context, session_id)
        if not self.tb.get_token():
            return Reply(ReplyType.ERROR, "提问太快啦，请休息一下再问我吧")
        try:
            result = get_workflow().invoke(state)
            final_output = result.get("final_output", "")
            if not final_output:
                return Reply(ReplyType.ERROR, "抱歉，我暂时无法回答这个问题。")
            self.sessions.session_reply(final_output, session_id, len(final_output))
            self.sessions.trim_context(session_id)
            return Reply(ReplyType.TEXT, final_output)
        except Exception as e:
            logger.exception(f"[LangGraphBot] 工作流异常: {e}")
            return Reply(ReplyType.ERROR, "抱歉，处理过程中出现错误，请稍后重试。")

    def _handle_special_commands(self, query, session_id):
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

    def _handle_subscribe_daily_news_commands(self, query, context):
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

    def _build_workflow_state(self, query, context, session_id):
        user_name = context["msg"].incoming_message.sender_nick
        user_title = getattr(context['msg'], 'from_user_title', '')
        session = self.sessions.session_query(query, session_id)
        chat_history = session.messages if session else []
        state = create_initial_state(
            user_input=query, user_name=user_name, user_title=user_title,
            session_id=session_id, chat_history=chat_history,
        )
        if context.type == ContextType.FILE:
            state["dialog_files_content"] = self._process_file_locally(context)
            state["upload_file_url"] = ""
        elif context.type == ContextType.IMAGE:
            state["image_url_list"] = self._process_image_locally(context)
        elif context.type == ContextType.DAILY_NEWS:
            state["push_daily_news_content"] = query
            state["user_input"] = conf().get("bisheng_workflow_default_query_news",
                                             "请快速帮我今日行业热点新闻内容。")
        daily_news_commands = conf().get("bisheng_daily_news_commands", ["今日新闻"])
        if query in daily_news_commands:
            state["daily_news_content"] = get_industry_news()
            state["user_input"] = conf().get("bisheng_workflow_default_query_news",
                                             "请快速帮我今日行业热点新闻内容。")
        if query.startswith("https://mp.weixin.qq.com/"):
            state["wechat_article_content"] = self._fetch_wechat_article_content(query)
            state["user_input"] = conf().get("bisheng_workflow_default_query_wechat",
                                             "请快速帮我总结一下这个文章的内容。")
        return state

    def _process_file_locally(self, context):
        file_path = context.get("file_path")
        file_name = context.get("file_name", "未知文件")
        if not file_path or not os.path.exists(file_path):
            return ""
        doc = DocumentLoader.load(file_path)
        return doc.content if doc else ""

    def _process_image_locally(self, context):
        urls = []
        for path in (context.get("msg").image_url_list or []):
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        data = base64.b64encode(f.read()).decode("utf-8")
                    ext = os.path.splitext(path)[1].lower().lstrip(".")
                    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                            "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/jpeg")
                    urls.append(f"data:{mime};base64,{data}")
                except Exception as e:
                    logger.warning(f"[LangGraphBot] 图片编码失败: {path}, {e}")
            else:
                urls.append(path)
        return urls

    def _fetch_wechat_article_content(self, url):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/91.0.4472.124 Safari/537.36"
        }
        try:
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            c = soup.select_one("#js_content")
            return c.get_text(separator="\n", strip=True) if c else ""
        except Exception as e:
            logger.exception(f"[LangGraphBot] 获取微信文章失败: {e}")
            return ""