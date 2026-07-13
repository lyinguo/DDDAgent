"""
LangGraph 工作流 Bot 入口
"""

import os, base64, requests
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
from bot.langgraph_workflow.state import create_initial_state
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
            conv_id = context.get("msg").incoming_message.conversation_id
            if not conv_id: return None, None
            http = self._get_dingtalk_http()
            if not http: return None, None
            files = http.fetch_recent_file_messages(conv_id, max_results=5)
            if not files: return None, None
            f = files[0]
            if hasattr(context.get("msg"), "image_download_handler"):
                url = context["msg"].image_download_handler.get_image_download_url(f["download_code"])
                if url:
                    from channel.dingtalk.dingtalk_message import download_image_file
                    from common.tmp_dir import TmpDir
                    p = download_image_file(url, TmpDir().path())
                    if p and os.path.exists(p):
                        rec = self.file_store.save_file(p, session_id, f["filename"])
                        if rec: return (self.file_store.read_file_content(rec), rec.filename)
            return None, None
        except Exception as e:
            logger.exception(f"[LangGraphBot] 拉取文件异常: {e}")
            return None, None

    def _handle_file_message(self, context, session_id):
        fp = context.get("file_path")
        fn = context.get("file_name", "文件")
        if not fp or not os.path.exists(fp):
            return Reply(ReplyType.ERROR, "文件读取失败。")
        rec = self.file_store.save_file(fp, session_id, fn)
        if not rec: return Reply(ReplyType.ERROR, "文件保存失败。")
        return Reply(ReplyType.TEXT, f"文件《{rec.filename}》已收到。需要总结还是入库？直接告诉我。")

    def _resolve_file_from_query(self, query, session_id):
        rec = self.file_store.get_file_by_name(session_id, query)
        if not rec: rec = self.file_store.get_latest_file(session_id)
        if not rec: return None, None
        c = self.file_store.read_file_content(rec)
        return (c, rec.filename) if c else (None, None)

    def _process_with_file(self, query, context, session_id, content, filename):
        state = self._build_workflow_state(query, context, session_id)
        state["dialog_files_content"] = content
        state["user_input"] = query
        if not self.tb.get_token(): return Reply(ReplyType.ERROR, "提问太快了。")
        try:
            result = get_workflow().invoke(state)
            out = result.get("final_output", "")
            if not out: return Reply(ReplyType.ERROR, "无法回答。")
            self.sessions.session_reply(out, session_id, len(out))
            self.sessions.trim_context(session_id)
            return Reply(ReplyType.TEXT, out)
        except Exception as e:
            logger.exception(f"[LangGraphBot] 文件处理异常: {e}")
            return Reply(ReplyType.ERROR, "处理文件时出错。")

    def reply(self, query, context=None):
        if context.type not in [ContextType.TEXT, ContextType.FILE,
                                 ContextType.DAILY_NEWS, ContextType.IMAGE]:
            return Reply(ReplyType.ERROR, f"不支持 {context.type}")

        session_id = context["session_id"]

        r = self._handle_special_commands(query, session_id)
        if r: return r

        r = self._handle_subscribe_daily_news_commands(query, context)
        if r: return r

        # 任务指令（用 staffId 发提醒）
        if context.type == ContextType.TEXT:
            user_name = context["msg"].incoming_message.sender_nick
            user_id = getattr(context["msg"], 'sender_staff_id', '') or context["msg"].actual_user_id
            is_group = context.get("isgroup", False)
            group_name = context.get("msg").incoming_message.conversation_title or ""
            task_reply = handle_task_command(query, user_name, user_id, is_group, group_name)
            if task_reply: return Reply(ReplyType.TEXT, task_reply)

        if context.type == ContextType.FILE:
            return self._handle_file_message(context, session_id)

        if context.type == ContextType.TEXT:
            file_kw = ["总结","文档","文件","入库","学习","知识库","读一下","看看","pdf"]
            if any(k in query for k in file_kw):
                fc, fn = self._resolve_file_from_query(query, session_id)
                if fc: return self._process_with_file(query, context, session_id, fc, fn)
                fc, fn = self._fetch_file_from_dingtalk(context, session_id)
                if fc: return self._process_with_file(query, context, session_id, fc, fn)
                return Reply(ReplyType.TEXT, "未找到相关文件。请先发送文件。")

        if context.type == ContextType.IMAGE:
            for p in (context.get("msg").image_url_list or []):
                if os.path.exists(p): self.file_store.save_file(p, session_id, f"图片_{os.path.basename(p)}")

        state = self._build_workflow_state(query, context, session_id)
        if not self.tb.get_token(): return Reply(ReplyType.ERROR, "提问太快了。")
        try:
            result = get_workflow().invoke(state)
            out = result.get("final_output", "")
            if not out: return Reply(ReplyType.ERROR, "无法回答。")
            self.sessions.session_reply(out, session_id, len(out))
            self.sessions.trim_context(session_id)
            return Reply(ReplyType.TEXT, out)
        except Exception as e:
            logger.exception(f"[LangGraphBot] 异常: {e}")
            return Reply(ReplyType.ERROR, "处理出错。")

    def _handle_special_commands(self, query, session_id):
        cmds = conf().get("clear_memory_commands", ["#清除记忆"])
        if query in cmds: self.sessions.clear_session(session_id); return Reply(ReplyType.INFO, "记忆已清除")
        if query == "#清除所有": self.sessions.clear_all_session(); return Reply(ReplyType.INFO, "已清除所有")
        if query == "#更新配置": load_config(); return Reply(ReplyType.INFO, "配置已更新")
        return None

    def _handle_subscribe_daily_news_commands(self, query, context):
        cmds = [conf().get("bisheng_daily_news_subscribe_command","订阅新闻"),
                conf().get("bisheng_daily_news_unsubscribe_command","取消订阅"),
                conf().get("bisheng_daily_news_status_command","查看订阅")]
        if query not in cmds: return None
        if not context.get("isgroup"): return Reply(ReplyType.INFO, "群聊才支持订阅。")
        from plugins.daily_news.daily_news_subscribed_group_manager import SubscribedGroupManager
        mgr = SubscribedGroupManager()
        gn = context.get("msg").incoming_message.conversation_title
        if query == cmds[0]: return Reply(ReplyType.INFO, "已订阅" if mgr.subscribe(gn) else "已订阅，勿重复")
        if query == cmds[1]: return Reply(ReplyType.INFO, "已取消" if mgr.unsubscribe(gn) else "未订阅")
        groups = mgr.get_all_subscribed_groups()
        return Reply(ReplyType.INFO, f"已订阅({len(groups)}个):\n"+"\n".join(f"· {n}" for n in groups)) if groups else Reply(ReplyType.INFO, "无订阅")

    def _build_workflow_state(self, query, context, session_id):
        user_name = context["msg"].incoming_message.sender_nick
        user_title = getattr(context['msg'], 'from_user_title', '')
        session = self.sessions.session_query(query, session_id)
        state = create_initial_state(user_input=query, user_name=user_name, user_title=user_title,
            session_id=session_id, chat_history=session.messages if session else [])
        if context.type == ContextType.FILE:
            fp = context.get("file_path")
            if fp: state["dialog_files_content"] = DocumentLoader.load(fp).content if DocumentLoader.load(fp) else ""
        elif context.type == ContextType.IMAGE:
            state["image_url_list"] = self._process_image_locally(context)
        elif context.type == ContextType.DAILY_NEWS:
            state["push_daily_news_content"] = query
            state["user_input"] = conf().get("bisheng_workflow_default_query_news","请快速帮我今日行业热点新闻内容。")
        if query in conf().get("bisheng_daily_news_commands",["今日新闻"]):
            state["daily_news_content"] = get_industry_news()
            state["user_input"] = conf().get("bisheng_workflow_default_query_news","请快速帮我今日行业热点新闻内容。")
        if query.startswith("https://mp.weixin.qq.com/"):
            state["wechat_article_content"] = self._fetch_wechat_article_content(query)
            state["user_input"] = conf().get("bisheng_workflow_default_query_wechat","请快速帮我总结一下这个文章的内容。")
        return state

    def _process_image_locally(self, context):
        urls = []
        for p in (context.get("msg").image_url_list or []):
            if os.path.exists(p):
                try:
                    with open(p,"rb") as f: data = base64.b64encode(f.read()).decode()
                    ext = os.path.splitext(p)[1].lower().lstrip(".")
                    mime = {"jpg":"image/jpeg","jpeg":"image/jpeg","png":"image/png","gif":"image/gif","webp":"image/webp"}.get(ext,"image/jpeg")
                    urls.append(f"data:{mime};base64,{data}")
                except: urls.append(p)
            else: urls.append(p)
        return urls

    def _fetch_wechat_article_content(self, url):
        try:
            r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15); r.raise_for_status()
            c = BeautifulSoup(r.text,"html.parser").select_one("#js_content")
            return c.get_text(separator="\n",strip=True) if c else ""
        except: return ""