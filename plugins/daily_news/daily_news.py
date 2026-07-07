# encoding: utf-8

import json
import os
import plugins
import threading
import time
import schedule
import requests
import uuid
from datetime import datetime
from bs4 import BeautifulSoup
from bridge.context import Context, ContextType
from plugins import *
from common.log import logger

from dingtalk_stream.chatbot import reply_specified_group_chat, TextContent
from channel.dingtalk.dingtalk_channel import DingTalkChanel
from channel.dingtalk.dingtalk_message import DingTalkMessage
from common.daily_news_fetcher import *

from config import conf
from channel.dingtalk.dingtalk_groups_manager import GroupManager
from .daily_news_subscribed_group_manager import SubscribedGroupManager

@plugins.register(
    name="DailyNews",
    desc="每日定时推送AI行业新闻热点",
    hidden=False,
    version="0.3",
    author="quhao",
    desire_priority=990
)
class DailyNews(Plugin):
    def __init__(self):
        super().__init__()
        self.channel = None
        self.group_manager = GroupManager()
        self.subscribed_manager = SubscribedGroupManager()
        self.target_groups_name =  self.subscribed_manager.get_all_subscribed_groups()
        self.push_time = conf().get("bisheng_daily_news_push_time", "09:00")
        self._start_scheduler()
        logger.info(f"[DailyNews Plugin] 插件初始化完成，推送时间：{self.push_time}, 目标群: {self.target_groups_name}")

    def _start_scheduler(self):
        """启动定时任务"""
        schedule.every().day.at(self.push_time).do(self._scheduled_task)
        
        def run_scheduler():
            logger.info("[DailyNews Plugin] 调度线程已启动")
            while True:
                try:
                    schedule.run_pending()
                except Exception as e:
                    logger.error(f"[DailyNews Plugin] 调度器执行出错: {e}")
                time.sleep(30) 
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()
        logger.info(f"[DailyNews Plugin] 定时任务已启动，推送时间: {self.push_time}")

    def _scheduled_task(self):
        """定时任务入口"""
        if not self._is_workday():
            logger.info("[DailyNews Plugin] 今天不是工作日，跳过推送")
            return
        
        self._send_daily_news()

    def _is_workday(self) -> bool:
        """
        判断今天是否为工作日
        优先调用节假日API，失败则使用周一到周五托底
        """
        try:
            # 调用公共节假日API
            response = requests.get(
                "https://publicapi.xiaoai.me/holiday/day",
                timeout=5
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0 and data.get("data"):
                    day_info = data["data"][0]
                    # rest:  0=工作日, 1=休息日
                    is_work = day_info.get("rest") == 0
                    logger.info(f"[DailyNews Plugin] 节假日API返回: {day_info.get('holiday')}, 是否工作日: {is_work}")
                    return is_work
        except Exception as e:
            logger.warning(f"[DailyNews Plugin] 节假日API调用失败")
        
        is_weekday = datetime.now().weekday() < 5
        logger.info(f"[DailyNews Plugin] 今天是周{datetime.now().weekday() + 1}，是否工作日: {is_weekday}")
        return is_weekday

    def _send_daily_news(self):
        """发送每日新闻"""
        self.target_groups_name = self.subscribed_manager.get_all_subscribed_groups()
        
        if not self.target_groups_name:
            logger.info("[DailyNews Plugin] 未配置目标群聊，跳过推送")
            return

        try:
            target_groups_id = self.group_manager.get_group_ids_by_names(self.target_groups_name)
            if not target_groups_id: 
                logger.warning(f"[DailyNews Plugin] 订阅的群 {self.target_groups_name} 均未找到对应ID，跳过推送")
                return
            
            self.channel = DingTalkChanel()
            if not self.channel:
                logger.error("[DailyNews Plugin] 无法获取channel实例")
                return
            
            news_content = get_industry_news()
            if not news_content:
                logger.warning("[DailyNews Plugin] 获取新闻内容为空，跳过推送")
                return

            logger.info(f"[DailyNews Plugin] 准备向群聊推送新闻")
            
            for group_id in target_groups_id:
                try:
                    # 构造主动推送基础消息
                    incoming_message = reply_specified_group_chat(group_id) 
                    incoming_message.text = TextContent()
                    incoming_message.text.content = news_content
                    incoming_message.create_at = int(time.time() * 1000)
                    incoming_message.message_id = str(uuid.uuid4())

                    # 构造 DingTalkMessage
                    dingtalk_msg = DingTalkMessage(incoming_message, None)
                    dingtalk_msg.ctype = ContextType.DAILY_NEWS
                    dingtalk_msg.content = incoming_message.text.content
                    dingtalk_msg.other_user_id = group_id
                    dingtalk_msg.sender_staff_id = "888"
                    dingtalk_msg.to_user_id = group_id
                    dingtalk_msg.actual_user_id = "888"
                    
                    logger.info(f"[DailyNews Plugin] 向群 {group_id} 推送新闻")
                    self.channel.handle_group(dingtalk_msg)
                except Exception as e:
                    logger.error(f"[DailyNews Plugin] 向群 {group_id} 推送失败: {e}")
                
        except Exception as e:
            logger.error(f"[DailyNews Plugin] 推送失败: {e}")

    def get_help_text(self, **kwargs):
        return f"在工作日的{self.push_time}，我会向指定群聊推送AI行业新闻热点。"