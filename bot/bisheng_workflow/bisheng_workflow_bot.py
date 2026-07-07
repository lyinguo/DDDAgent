# encoding:utf-8

import time
import json
import requests
import os
from bs4 import BeautifulSoup

from common import const
from bot.bot import Bot
from bot.bisheng_workflow.bisheng_workflow_session import BishengWorkflowSession
from bot.bisheng_workflow.bisheng_workflow_context import WorkflowRequestContext
from bot.session_manager import SessionManager
from bridge.context import ContextType, Context
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.token_bucket import TokenBucket
from config import conf, load_config

from common.daily_news_fetcher import get_industry_news
from plugins.daily_news.daily_news_subscribed_group_manager import SubscribedGroupManager

# 毕昇工作流对话模型API
class BishengWorkflowBot(Bot):
    def __init__(self):
        super().__init__()
        self.base_url = conf().get("bisheng_workflow_api_base", "http://agentdev.qdai.qd-metro.com/api/v2/workflow/invoke")
        self.workflow_id = conf().get("bisheng_workflow_id", "4143c38f06094a35baf2207914e0b204")
        if conf().get("rate_limit_bisheng"):
            self.tb4bisheng = TokenBucket(conf().get("rate_limit_bisheng", 20))
        self.sessions = SessionManager(BishengWorkflowSession, model=self.workflow_id)
        
        self.args = {
            "workflow_id": self.workflow_id,
            "stream": False,
        }
        self.proxy = conf().get("proxy")

    def reply(self, query, context=None):
        """
        处理消息并返回回复
        :param query: 用户查询内容（TEXT 时是文本，FILE 时是文件路径）
        :param context: 上下文对象
        :return: Reply 对象
        """
        if context.type not in [ContextType.TEXT, ContextType.FILE, ContextType.DAILY_NEWS, ContextType.IMAGE]:
            logger.warning(f"[BISHENG WORKFLOW] 不支持的消息类型: {context.type}")
            return Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
        
        logger.debug("[BISHENG WORKFLOW] query={}, context_type={}".format(query, context.type))

        session_id = context["session_id"]
        
        # 处理特殊命令（清除记忆功能）
        reply = self._handle_special_commands(query, session_id)
        if reply:
            return reply

        # 处理订阅新闻的指令 （订阅新闻，取消订阅，查看当前已经订阅的所有群聊）
        reply = self._handle_subscribe_daily_news_commands(query, context)
        if reply:
            return reply

        # 构建工作流请求上下文
        workflow_context = self._build_workflow_context(query, context)
        # 更新query（可能被修改为默认提示语）
        query = workflow_context.messages
        
        session = self.sessions.session_query(query, session_id)
        logger.debug("[BISHENG WORKFLOW] session query={}".format(session.messages))
        
        # 更新上下文中的messages为session的messages
        workflow_context.messages = session.messages

        if self.args.get("stream", False):
            return self.reply_stream(session, workflow_context, args=self.args)
        else:
            reply_content = self.reply_text(session, workflow_context, args=self.args)
            logger.debug(
                "[BISHENG WORKFLOW] new_query={}, session_id={}, reply_cont={}, completion_tokens={}".format(
                    session.messages,
                    session_id,
                    reply_content["content"],
                    reply_content["completion_tokens"],
                )
            )
            if reply_content["completion_tokens"] == 0 and len(reply_content["content"]) > 0:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
            elif reply_content["completion_tokens"] > 0:
                self.sessions.session_reply(reply_content["content"], session_id, reply_content["total_tokens"])
                self.sessions.trim_context(session_id)
                reply = Reply(ReplyType.TEXT, reply_content["content"])
            else:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
                logger.debug("[BISHENG WORKFLOW] reply {} used 0 tokens. ".format(reply_content))
            return reply

    def _handle_special_commands(self, query:  str, session_id: str) -> Reply:
        """
        处理特殊命令
        :param query: 用户输入
        :param session_id: 会话ID
        :return:  Reply对象或None
        """
        clear_memory_commands = conf().get("clear_memory_commands", ["#清除记忆"])
        if query in clear_memory_commands:
            self.sessions.clear_session(session_id)
            return Reply(ReplyType.INFO, "记忆已清除")
        elif query == "#清除所有": 
            self.sessions.clear_all_session()
            return Reply(ReplyType.INFO, "所有人记忆已清除")
        elif query == "#更新配置":
            load_config()
            return Reply(ReplyType.INFO, "配置已更新")
        return None

    def _handle_subscribe_daily_news_commands(self, query: str, context: Context) -> Reply:
        '''
        处理群聊中订阅每日行业新闻的指令，如订阅新闻、取消订阅、查看当前已订阅的所有群聊名称
        :param query: 用户输入
        :param context: 上下文对象
        :return: Reply对象或None
        '''
        subscribe_command = conf().get("bisheng_daily_news_subscribe_command", "订阅新闻")
        unsubscribe_command = conf().get("bisheng_daily_news_unsubscribe_command", "取消订阅")
        status_command = conf().get("bisheng_daily_news_status_command", "查看订阅")
        push_time = conf().get("bisheng_daily_news_push_time", "09:00")

        if query not in [subscribe_command, unsubscribe_command, status_command]:
            return None
        if context.get("isgroup") is False:
            return Reply(ReplyType.INFO, "私聊暂不支持订阅新闻等操作！")

        subscribed_manager = SubscribedGroupManager()
        group_name = context.get("msg").incoming_message.conversation_title

        if query == subscribe_command:
            if subscribed_manager.subscribe(group_name):
                return Reply(ReplyType.INFO, f"每日新闻订阅成功！推送时间每工作日{push_time}")
            else:
                return Reply(ReplyType.INFO, f"已订阅，无需重复操作")
        elif query == unsubscribe_command:
            if subscribed_manager.unsubscribe(group_name):
                return Reply(ReplyType.INFO, f"已取消订阅每日新闻")
            else:
                return Reply(ReplyType.INFO, f"未订阅，无需取消")
        elif query == status_command:
            subscribed_list = subscribed_manager.get_all_subscribed_groups()
            if subscribed_list: 
                groups_str = "\n".join([f"  • {name}" for name in subscribed_list])
                return Reply(ReplyType.INFO, f"当前已订阅每日新闻的群聊({len(subscribed_list)}个)：\n{groups_str}")
            else:
                return Reply(ReplyType.INFO, "当前没有群聊订阅每日新闻")

        return None


    def _build_workflow_context(self, query: str, context: Context) -> WorkflowRequestContext: 
        """
        构建工作流请求上下文
        :param query: 用户查询内容
        :param context: 上下文对象
        : return: WorkflowRequestContext对象，或出错时返回Reply对象
        """
        # 获取用户信息
        user_name = context["msg"].incoming_message.sender_nick
        user_title = context['msg'].from_user_title
        logger.debug(f"[BISHENG WORKFLOW] 用户姓名: {user_name}，用户职位：{user_title}")
        
        # 初始化上下文
        workflow_context = WorkflowRequestContext(
            session_id=context["session_id"],
            messages=query,
            user_name=user_name,
            user_title=user_title,
        )
        
        # 处理文件上传
        if context.type == ContextType.FILE:
            workflow_context.upload_file_url = self._process_file_upload(context)
            workflow_context.messages = conf().get("bisheng_workflow_default_query_uploadfile", "请快速帮我总结一下这个文档的内容。")
        
        # 处理图片上传
        if context.type == ContextType.IMAGE:
            image_url_list = []
            for image_url in context["msg"].image_url_list:
                image_url_list.append(self._bisheng_workflow_upload_file(image_url))
            workflow_context.image_url_list = image_url_list
        
        # 处理每日新闻（定时推送）
        if context.type == ContextType.DAILY_NEWS:
            workflow_context.push_daily_news_content = query
            workflow_context.messages = conf().get("bisheng_workflow_default_query_news", "请快速帮我今日行业热点新闻内容。")
        
        # 处理每日新闻（主动获取）
        daily_news_commands = conf().get("bisheng_daily_news_commands", ["今日新闻"])
        if query in daily_news_commands:
            workflow_context.daily_news_content = get_industry_news()
            workflow_context.messages = conf().get("bisheng_workflow_default_query_news", "请快速帮我今日行业热点新闻内容。")
        
        # 处理微信公众号链接
        if query.startswith("https://mp.weixin.qq.com/"):
            workflow_context.wechat_article_content = self._process_wechat_article(query)
            workflow_context.messages = conf().get("bisheng_workflow_default_query_wechat", "请快速帮我总结一下这个文章的内容。")
        
        return workflow_context

    def _process_file_upload(self, context: Context) -> str:
        """
        处理文件上传
        :param context: 上下文对象
        :return: 上传后的文件URL，或出错时返回Reply对象
        """
        file_path = context.get("file_path")
        file_name = context.get("file_name")
        
        if not file_path or not os.path.exists(file_path):
            logger.error(f"[BISHENG WORKFLOW] 文件路径无效: {file_path}")
            return Reply(ReplyType.ERROR, "文件路径无效，无法处理")
        
        try:
            upload_file_url = self._bisheng_workflow_upload_file(file_path)
            logger.info(f"[BISHENG WORKFLOW] 文件上传成功: {file_name}, url={upload_file_url}")
            return upload_file_url
        except Exception as e:
            logger.exception(f"[BISHENG WORKFLOW] 文件上传失败: {file_name}, error={e}")
            return Reply(ReplyType.ERROR, f"文件上传失败:  {str(e)}")

    def _process_wechat_article(self, url: str) -> str:
        """
        处理微信公众号文章
        :param url: 微信公众号文章链接
        :return:  文章内容，或出错时返回Reply对象
        """
        try:
            wechat_article_content = self._fetch_wechat_article_content(url)
            if wechat_article_content: 
                logger.info(f"[BISHENG WORKFLOW] 微信公众号文章内容获取成功，长度={len(wechat_article_content)}")
                return wechat_article_content
            else:
                logger.warning(f"[BISHENG WORKFLOW] 微信公众号文章内容为空:  {url}")
                return Reply(ReplyType.ERROR, "无法获取微信公众号文章内容，请稍后重试")
        except Exception as e:
            logger.exception(f"[BISHENG WORKFLOW] 获取微信公众号文章失败: {url}, error={e}")
            return Reply(ReplyType.ERROR, f"获取微信公众号文章失败: {str(e)}")

    def reply_stream(self, session: BishengWorkflowSession, workflow_context: WorkflowRequestContext, args=None, retry_count=0):
        """
        流式回复，返回一个生成器对象
        :param session: 会话对象
        :param workflow_context: 工作流请求上下文
        :param args: 参数
        :param retry_count: 重试次数
        :return: Reply对象（包含生成器）
        """
        try:
            if args is None:
                args = self.args
            
            workflow_session_id, input_node_id, message_id = session.get_workflow_session()
            
            if workflow_session_id is None or input_node_id is None or message_id is None:
                init_result = self._init_workflow_session(args)
                if init_result.get("error"):
                    logger.error(f"[BISHENG WORKFLOW] 创建工作流会话失败: {init_result.get('content')}")
                    return Reply(ReplyType.ERROR, init_result.get("content", "创建工作流会话失败"))
                
                workflow_session_id = init_result["workflow_session_id"]
                input_node_id = init_result["input_node_id"]
                message_id = init_result["message_id"]
                schema_fields = init_result.get("schema_fields", [])
                session.set_workflow_session(workflow_session_id, input_node_id, message_id, schema_fields)
                logger.info(f"[BISHENG WORKFLOW] 创建新的工作流会话成功，workflow_session_id={workflow_session_id}")
            
            if conf().get("rate_limit_bisheng") and not self.tb4bisheng.get_token():
                logger.warning("[BISHENG WORKFLOW] 触发限流")
                raise Exception("RateLimitError: rate limit exceeded")
            
            session_id = workflow_context.session_id
            
            # 处理超时时间的设置
            initial_timeout = 5
            is_heavy_task = any([
                workflow_context.upload_file_url
                # workflow_context.image_url_list
            ])
            if is_heavy_task:
                current_line_timeout = 20
                logger.info(f"[BISHENG WORKFLOW] 检测到复杂任务(文件/文章)，调整超时时间为: {current_line_timeout}s")
            else:
                current_line_timeout = initial_timeout
                logger.info(f"[BISHENG WORKFLOW] 普通对话任务，使用默认超时: {current_line_timeout}s")

            def smart_iter_lines(response, strict_timeout):
                """
                智能迭代器：
                1. 第一行数据（连接建立时间）使用 requests 默认的宽裕超时 (60s)
                2. 收到第一行后，立即修改 Socket 为严格超时 (strict_timeout)
                3. 后续数据使用严格超时
                """
                iterator = response.iter_lines()
                
                try:
                    first_line = next(iterator)
                    yield first_line
                except StopIteration:
                    return
                except Exception as e:
                    raise e
                try:
                    sock = getattr(response.raw, '_connection', None) or getattr(response.raw, 'connection', None)
                    if sock and hasattr(sock, 'sock') and sock.sock:
                        sock.sock.settimeout(strict_timeout)
                        logger.debug(f"[BISHENG WORKFLOW] 首字已接收，Socket超时已调整为严格模式: {strict_timeout}s")
                except Exception as e:
                    logger.warning(f"[BISHENG WORKFLOW] 设置流式超时失败 (不影响主流程): {e}")

                yield from iterator
            
            def stream_generator():
                total_tokens = 0
                full_message = ""
                completed = False
                try:
                    response = self._send_stream_request(
                        args, 
                        workflow_session_id, 
                        input_node_id, 
                        message_id, 
                        workflow_context
                    )
                    if response is None:
                        logger.error("[BISHENG WORKFLOW] 发送流式请求失败，response 为 None")
                        yield "[ERROR]\n 毕昇工作流请求失败，无法获取响应"
                        return
                    
                    # for line in response.iter_lines():
                    for line in smart_iter_lines(response, current_line_timeout):
                        if not line:
                            continue

                        try:
                            line_str = line.decode('utf-8') if isinstance(line, bytes) else line
                            line_str = line_str.strip()
                            
                            if not line_str.startswith("data:"):
                                continue
                                
                            line_str = line_str[len("data:"):].strip()
                            event_data = json.loads(line_str).get("data")

                            # 检查是否有错误状态
                            if event_data.get("event") == "close":
                                output_schema = event_data.get("output_schema", {})
                                message_data = output_schema.get("message", {})
                                
                                if isinstance(message_data, dict):
                                    error_code = message_data.get("code") or message_data.get("status_code")
                                    error_message = message_data.get("message", "") or message_data.get("status_message")
                                    if error_code:
                                        logger.error(f"[BISHENG WORKFLOW] 服务器错误 ({error_code}): {error_message}")
                                        yield "[ERROR]\n 毕昇工作流请求失败，无法获取响应"
                                        return
                            # 进行流式输出
                            if event_data.get("event") == "stream_msg":
                                output_schema = event_data.get("output_schema", {})
                                message = output_schema.get("message", "")
                                status = event_data.get("status")
                                
                                if status == "stream":
                                    total_tokens += len(message)
                                    full_message += message
                                    logger.debug(f"[BISHENG WORKFLOW] 流式消息块: {message}")
                                    yield message
                                    
                                elif status == "end":
                                    completed = True
                                    self.sessions.session_reply(full_message, session_id, total_tokens)
                                    self.sessions.trim_context(session_id)
                                    logger.debug(f"[BISHENG WORKFLOW] 流式消息完成，total_tokens={total_tokens}")
                                    return                        

                        except json.JSONDecodeError as e:
                            logger.warning(f"[BISHENG WORKFLOW] 解析流式响应行失败: {line_str[:100]}, error={e}")
                            continue

                except requests.exceptions.ConnectionError:
                    if full_message:
                        logger.warning(f"[BISHENG WORKFLOW] 流式输出 end 事件读取超时，视为主动结束 (已接收内容长度: {len(full_message)})")
                        self._stop_workflow(args, workflow_session_id, message_id)
                        session.reset_workflow_session()
                except Exception as e:
                    if not full_message:
                        logger.exception(f"[BISHENG WORKFLOW] 流式输出生成器异常: {e}")
                        yield "[ERROR]\n 抱歉，流式输出过程中出现了问题"
                        return
                finally:
                    if full_message and not completed:
                        try:
                            self.sessions.session_reply(full_message, session_id, total_tokens)
                            self.sessions.trim_context(session_id)
                            logger.info(f"[BISHENG WORKFLOW] Session 处理完成，tokens={total_tokens}")
                        except Exception as e:
                            logger.error(f"[BISHENG WORKFLOW] Session 处理失败: {e}")

            return Reply(ReplyType.TEXT_STREAM, stream_generator())
        
        except Exception as e:
            logger.exception(f"[BISHENG WORKFLOW] reply_stream异常: {e}")
            if retry_count < 2:
                logger.warning(f"[BISHENG WORKFLOW] 第{retry_count + 1}次重试")
                time.sleep(5)
                return self.reply_stream(session, workflow_context, args, retry_count + 1)
            return Reply(ReplyType.ERROR, "流式获取回复失败，请稍后重试")

    def reply_text(self, session: BishengWorkflowSession, workflow_context: WorkflowRequestContext, args=None, retry_count=0) -> dict:
        """
        非流式调用毕昇AI接口获取回复
        :param session: 会话对象
        :param workflow_context: 工作流请求上下文
        :param args: 参数
        :param retry_count:  重试次数
        : return: {}
        """
        try:
            if args is None:
                args = self.args
            
            headers = {
                'Content-Type': 'application/json'  
            }

            workflow_session_id, input_node_id, message_id = session.get_workflow_session()
            if workflow_session_id is None or input_node_id is None or message_id is None:
                logger.info("[BISHENG WORKFLOW] 工作流会话不存在，开始创建")
                init_result = self._init_workflow_session(args)
                if init_result.get("error"):
                    logger.error(f"[BISHENG WORKFLOW] 创建工作流会话失败: {init_result.get('content')}")
                    return {
                        "total_tokens": 0,
                        "completion_tokens": 0,
                        "content": init_result.get("content", "创建工作流会话失败")
                    }
                
                workflow_session_id = init_result["workflow_session_id"]
                input_node_id = init_result["input_node_id"]
                message_id = init_result["message_id"]
                schema_fields = init_result.get("schema_fields", [])
                session.set_workflow_session(workflow_session_id, input_node_id, message_id, schema_fields)
                logger.info(f"[BISHENG WORKFLOW] 创建新的工作流会话成功，workflow_session_id={workflow_session_id}")
            else:
                schema_fields = session.get_schema_fields() or []

            if conf().get("rate_limit_bisheng") and not self.tb4bisheng.get_token():
                logger.warning("[BISHENG WORKFLOW] 触发限流")
                raise Exception("RateLimitError: rate limit exceeded")

            # 使用上下文对象生成input_data，传入表单字段schema
            input_data = workflow_context.to_input_data(schema_fields=schema_fields)
            
            payload = {
                "workflow_id": args["workflow_id"],
                "stream": False,
                "input": {
                    input_node_id: input_data
                },
                "message_id": message_id,
                "session_id": workflow_session_id
            }
            
            logger.debug(f"[BISHENG WORKFLOW] 发送请求: {json.dumps(payload, ensure_ascii=False)[:500]}")
            
            response = requests.post(
                self.base_url, 
                headers=headers, 
                json=payload,
                timeout=60
            )
            
            if response.status_code != 200:
                logger.error(f"[BISHENG WORKFLOW] API请求失败，状态码：{response.status_code}，响应：{response.text}")
                return {
                    "total_tokens": 0,
                    "completion_tokens": 0,
                    "content": f"毕昇API调用失败，状态码：{response.status_code}"
                }

            response_json = response.json()
            logger.debug(f"[BISHENG WORKFLOW] API响应: {json.dumps(response_json, ensure_ascii=False)[:500]}")
            
            events = response_json.get("data", {}).get("events", [])
            # 优先查找 output_msg 事件，如果不存在则查找 stream_msg (end) 事件
            output_event = next((e for e in events if e.get("event") == "output_msg"), None)
            if output_event is None:
                output_event = next((e for e in events if e.get("event") == "stream_msg" and e.get("status") == "end"), None)
            
            if output_event is None:
                logger.error(f"[BISHENG WORKFLOW] API响应解析失败，未找到output事件，响应：{response.text}")
                return {
                    "total_tokens": 0,
                    "completion_tokens": 0,
                    "content": "毕昇工作流API响应解析失败，未找到output事件"
                }
            
            content = output_event.get("output_schema", {}).get("message", "")
            
            completion_tokens = len(content)
            total_tokens = sum(len(msg.get("content", "")) for msg in session.messages) + completion_tokens
            
            logger.info(f"[BISHENG WORKFLOW] 回复成功，content长度={len(content)}, total_tokens={total_tokens}")
            
            return {
                "total_tokens": total_tokens,
                "completion_tokens": completion_tokens,
                "content": content,
            }
        
        except Exception as e:
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            
            if "RateLimitError" in str(e):
                logger.warning("[BISHENG WORKFLOW] RateLimitError: {}".format(e))
                result["content"] = "提问太快啦，请休息一下再问我吧"
                if need_retry:
                    time.sleep(20)
            elif isinstance(e, requests.exceptions.Timeout):
                logger.warning("[BISHENG WORKFLOW] Timeout: {}".format(e))
                result["content"] = "我没有收到你的消息，请稍后再试"
                if need_retry:
                    time.sleep(5)
            elif isinstance(e, requests.exceptions.RequestException):
                logger.exception("[BISHENG WORKFLOW] RequestException: {}".format(e))
                result["content"] = "网络请求异常，请稍后再试"
                if need_retry:
                    time.sleep(5)
            else:
                logger.exception("[BISHENG WORKFLOW] Exception: {}".format(e))
                if need_retry:
                    time.sleep(5)
            
            if need_retry:
                logger.warning("[BISHENG WORKFLOW] 第{}次重试".format(retry_count + 1))
                return self.reply_text(session, workflow_context, args, retry_count + 1)
            return result

    def _init_workflow_session(self, args):
        """
        初始化工作流会话
        """
        try:
            headers = {'Content-Type': 'application/json'}
            payload = json.dumps({
                "workflow_id": args["workflow_id"],
                "stream": False
            })
            
            logger.debug(f"[BISHENG WORKFLOW] 初始化工作流会话，payload={payload}")
            
            response = requests.post(
                self.base_url,
                headers=headers,
                data=payload,
                timeout=30
            )
            
            if response.status_code != 200:
                logger.error(f"[BISHENG WORKFLOW] 创建工作流会话失败，状态码：{response.status_code}，响应：{response.text}")
                return {
                    "error": True,
                    "content": f"毕昇工作流API调用失败，状态码：{response.status_code}"
                }
            
            response_json = response.json()
            logger.debug(f"[BISHENG WORKFLOW] 初始化响应: {json.dumps(response_json, ensure_ascii=False)[:500]}")
            
            workflow_session_id = response_json.get("data", {}).get("session_id")
            events = response_json.get("data", {}).get("events", [])
            input_event = next((e for e in events if e.get("event") == "input"), None)
            
            if input_event is None:
                logger.error(f"[BISHENG WORKFLOW] 创建工作流会话失败，响应：{response.text}")
                return {
                    "error": True,
                    "content": "毕昇工作流API调用失败"
                }
            
            input_node_id = input_event.get("node_id")
            message_id = input_event.get("message_id")

            # 提取form_input的表单字段定义，用于动态构建正确的key
            input_schema = input_event.get("input_schema", {})
            schema_fields = []
            if input_schema.get("input_type") == "form_input":
                schema_fields = input_schema.get("value", [])
                logger.debug(f"[BISHENG WORKFLOW] 表单字段: {[sf.get('key') for sf in schema_fields]}")

            logger.info(f"[BISHENG WORKFLOW] 初始化成功，workflow_session_id={workflow_session_id}, input_node_id={input_node_id}")

            return {
                "error": False,
                "workflow_session_id": workflow_session_id,
                "input_node_id": input_node_id,
                "message_id": message_id,
                "schema_fields": schema_fields
            }
        
        except Exception as e:
            logger.exception(f"[BISHENG WORKFLOW] 初始化工作流会话异常: {e}")
            return {
                "error": True,
                "content": "初始化工作流会话异常"
            }

    def _send_stream_request(self, args, workflow_session_id, input_node_id, message_id, workflow_context: WorkflowRequestContext):
        """
        发送流式请求
        : param args: 参数
        :param workflow_session_id:  工作流会话ID
        :param input_node_id: 输入节点ID
        :param message_id: 消息ID
        :param workflow_context: 工作流请求上下文
        :return: response对象或None
        """
        try:
            headers = {'Content-Type': 'application/json'}
            
            # 使用上下文对象生成input_data
            input_data = workflow_context.to_input_data()

            payload = {
                "workflow_id": args["workflow_id"],
                "stream": True,
                "input": {
                    input_node_id: input_data
                },
                "message_id": message_id,
                "session_id": workflow_session_id
            }
            
            logger.debug(f"[BISHENG WORKFLOW] 发送流式请求: {json.dumps(payload, ensure_ascii=False)[:500]}")
            
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=(60, 60)
            )
            
            if response.status_code != 200:
                logger.error(f"[BISHENG WORKFLOW] API请求失败，状态码：{response.status_code}，响应：{response.text}")
                return None

            return response
        except Exception as e:
            logger.exception(f"[BISHENG WORKFLOW] 发送流式请求异常: {e}")
            return None
        
    def _stop_workflow(self, args, workflow_session_id, message_id):
        """
        显式调用接口终止服务端的流式生成
        """
        try:
            workflow_api_stop = conf().get("bisheng_workflow_api_stop", "http://agentdev.qdai.qd-metro.com/api/v2/workflow/stop")
            
            headers = {'Content-Type': 'application/json'}

            payload = {
                "workflow_id": args["workflow_id"],
                "session_id": workflow_session_id,
                "message_id": message_id
            }
            
            requests.post(workflow_api_stop, json=payload, headers=headers)
        except Exception as e:
            logger.warning(f"[BISHENG WORKFLOW] 主动终止任务请求失败: {e}")

    def _bisheng_workflow_upload_file(self, local_path: str) -> str:
        """
        上传文件到毕昇工作流
        :param local_path: 本地文件路径
        :return: 上传后的文件URL
        """
        bisheng_workflow_upload_file_url = conf().get(
            "bisheng_workflow_upload_file_url", 
            "http://agentdev.qdai.qd-metro.com/api/v1/knowledge/upload"
        )
        
        if not os.path.exists(local_path):
            logger.error(f"[BISHENG WORKFLOW] 文件不存在: {local_path}")
            raise FileNotFoundError(f"文件不存在: {local_path}")
        
        file_size = os.path.getsize(local_path)
        logger.info(f"[BISHENG WORKFLOW] 开始上传文件: {local_path}, size={file_size} bytes")
        
        try:
            headers = {}
            with open(local_path, 'rb') as f:
                files = {'file': f}
                response = requests.post(
                    bisheng_workflow_upload_file_url, 
                    headers=headers, 
                    files=files,
                )
                response.raise_for_status()
            
            response_data = response.json()
            logger.debug(f"[BISHENG WORKFLOW] 上传响应: {json.dumps(response_data, ensure_ascii=False)}")
            
            file_path = response_data.get('data', {}).get('file_path', '')
            
            if not file_path:
                logger.error(f"[BISHENG WORKFLOW] 上传返回的file_path为空，响应: {response_data}")
                raise Exception("上传文件返回的 file_path 为空")
            
            os.remove(local_path)
            logger.debug(f"[BISHENG WORKFLOW] 本地文件已删除: {local_path}")

            return file_path        
        except Exception as e:
            logger.exception(f"[BISHENG WORKFLOW] 文件上传失败: {local_path}, error={e}")
            raise

    def _fetch_wechat_article_content(self, url: str) -> str:
        """
        获取微信公众号文章内容
        :param url: 微信公众号文章链接
        :return: 文章正文内容
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        try:
            logger.info(f"[BISHENG WORKFLOW] 开始获取微信公众号文章: {url}")
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            html = response.text
            soup = BeautifulSoup(html, "html.parser")
            
            # 微信文章正文 div id 通常是 js_content
            content = soup.select_one("#js_content")
            
            if content:
                # 获取纯文本内容并清理多余空白
                article_text = content.get_text(separator="\n", strip=True)
                logger.debug(f"[BISHENG WORKFLOW] 微信文章内容长度: {len(article_text)}")
                return article_text
            else:
                logger.warning(f"[BISHENG WORKFLOW] 未能获取到微信文章正文内容: {url}")
                return ""
                
        except requests.exceptions.Timeout:
            logger.error(f"[BISHENG WORKFLOW] 获取微信文章超时: {url}")
            raise Exception("获取微信公众号文章超时")
        except requests.exceptions.RequestException as e:
            logger.error(f"[BISHENG WORKFLOW] 获取微信文章网络请求失败: {url}, error={e}")
            raise Exception(f"获取微信公众号文章网络请求失败: {str(e)}")
        except Exception as e:
            logger.exception(f"[BISHENG WORKFLOW] 获取微信文章异常: {url}, error={e}")
            raise