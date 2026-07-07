# encoding:utf-8

import time
import json
import requests

from common import const
from bot.bot import Bot
from bot.bisheng.bisheng_session import BishengSession
from bot.session_manager import SessionManager
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from common.token_bucket import TokenBucket
from config import conf, load_config

# 毕昇AI对话模型API
class BishengBot(Bot):
    def __init__(self):
        super().__init__()
        self.base_url = conf().get("bisheng_api_base", "http://10.21.199.102:3001/api/v2/assistant/chat/completions")
        self.model_id = conf().get("bisheng_model_id", "7ea31a87-9a90-4d66-936c-12e126ffeb47")
        if conf().get("rate_limit_bisheng"):
            self.tb4bisheng = TokenBucket(conf().get("rate_limit_bisheng", 20))
        self.sessions = SessionManager(BishengSession, model=self.model_id)
        
        self.args = {
            "model": self.model_id,  # 毕昇模型的ID
            "temperature": conf().get("temperature", 0),  # 值在[0,1]之间，越大表示回复越具有不确定性
            "stream": False  # 是否使用流式响应
        }
        self.proxy = conf().get("proxy")

    def reply(self, query, context=None):
        # 获取回复内容
        if context.type == ContextType.TEXT:
            logger.info("[BISHENG] query={}".format(query))

            session_id = context["session_id"]
            reply = None
            clear_memory_commands = conf().get("clear_memory_commands", ["#清除记忆"])
            if query in clear_memory_commands:
                self.sessions.clear_session(session_id)
                reply = Reply(ReplyType.INFO, "记忆已清除")
            elif query == "#清除所有":
                self.sessions.clear_all_session()
                reply = Reply(ReplyType.INFO, "所有人记忆已清除")
            elif query == "#更新配置":
                load_config()
                reply = Reply(ReplyType.INFO, "配置已更新")
            if reply:
                return reply
            
            session = self.sessions.session_query(query, session_id)
            logger.debug("[BISHENG] session query={}".format(session.messages))

            model = context.get("bisheng_model") or self.model_id
            new_args = None
            if model:
                new_args = self.args.copy()
                new_args["model"] = model

            reply_content = self.reply_text(session, args=new_args)
            logger.debug(
                "[BISHENG] new_query={}, session_id={}, reply_cont={}, completion_tokens={}".format(
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
                reply = Reply(ReplyType.TEXT, reply_content["content"])
            else:
                reply = Reply(ReplyType.ERROR, reply_content["content"])
                logger.debug("[BISHENG] reply {} used 0 tokens.".format(reply_content))
            return reply
        else:
            reply = Reply(ReplyType.ERROR, "Bot不支持处理{}类型的消息".format(context.type))
            return reply

    def reply_text(self, session: BishengSession, args=None, retry_count=0) -> dict:
        """
        调用毕昇AI接口获取回复
        :param session: 会话对象
        :param args: 参数
        :param retry_count: 重试次数
        :return: {}
        """
        try:
            if conf().get("rate_limit_bisheng") and not self.tb4bisheng.get_token():
                raise Exception("RateLimitError: rate limit exceeded")
            
            if args is None:
                args = self.args
            
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'ChatGPT-on-WeChat'
            }
            
            payload = {
                "model": args["model"],
                "messages": session.messages,
                "temperature": args.get("temperature", 0),
                "stream": args.get("stream", False)
            }
            
            # 准备请求
            proxy_dict = None
            if self.proxy:
                proxy_dict = {
                    "http": self.proxy,
                    "https": self.proxy
                }
            
            # 发送请求
            response = requests.post(
                self.base_url, 
                headers=headers, 
                json=payload, 
                proxies=proxy_dict,
                timeout=conf().get("request_timeout", 30)
            )
            
            if response.status_code != 200:
                logger.error(f"[BISHENG] API请求失败，状态码：{response.status_code}，响应：{response.text}")
                return {
                    "total_tokens": 0,
                    "completion_tokens": 0,
                    "content": f"毕昇API调用失败，状态码：{response.status_code}"
                }
            
            response_json = response.json()
            
            # 解析响应
            content = response_json["choices"][0]["message"]["content"]
            # 简单估算token数量
            completion_tokens = len(content)
            total_tokens = sum(len(msg["content"]) for msg in session.messages) + completion_tokens
            
            return {
                "total_tokens": total_tokens,
                "completion_tokens": completion_tokens,
                "content": content,
            }
        except Exception as e:
            need_retry = retry_count < 2
            result = {"completion_tokens": 0, "content": "我现在有点累了，等会再来吧"}
            
            if "RateLimitError" in str(e):
                logger.warn("[BISHENG] RateLimitError: {}".format(e))
                result["content"] = "提问太快啦，请休息一下再问我吧"
                if need_retry:
                    time.sleep(20)
            elif isinstance(e, requests.exceptions.Timeout):
                logger.warn("[BISHENG] Timeout: {}".format(e))
                result["content"] = "我没有收到你的消息，请稍后再试"
                if need_retry:
                    time.sleep(5)
            else:
                logger.exception("[BISHENG] Exception: {}".format(e))
                if need_retry:
                    time.sleep(5)
            
            if need_retry:
                logger.warn("[BISHENG] 第{}次重试".format(retry_count + 1))
                return self.reply_text(session, args, retry_count + 1)
            return result 