import os

import requests
from dingtalk_stream import ChatbotMessage

from bridge.context import ContextType
from channel.chat_message import ChatMessage
# -*- coding=utf-8 -*-
from common.log import logger
from common.tmp_dir import TmpDir
from config import conf
from .dingtalk_groups_manager import GroupManager

class DingTalkMessage(ChatMessage):
    def __init__(self, event: ChatbotMessage, image_download_handler):
        super().__init__(event)
        self.image_download_handler = image_download_handler
        self.msg_id = event.message_id
        self.message_type = event.message_type
        self.incoming_message = event
        self.sender_staff_id = event.sender_staff_id
        self.other_user_id = event.conversation_id
        self.create_time = event.create_at
        self.image_content = event.image_content
        self.rich_text_content = event.rich_text_content
        self.file_content = None
        self.from_user_title = ""
        self.image_url_list = []

        if event.conversation_type == "1":
            self.is_group = False
        else:
            self.is_group = True

        if self.message_type == "text":
            self.ctype = ContextType.TEXT

            self.content = event.text.content.strip()
        elif self.message_type == "audio":
            # 钉钉支持直接识别语音，所以此处将直接提取文字，当文字处理
            self.content = event.extensions['content']['recognition'].strip()
            self.ctype = ContextType.TEXT
        elif self.message_type == 'picture':
            self.ctype = ContextType.IMAGE
            # 钉钉图片类型处理
            image_list = event.get_image_list()
            if len(image_list) > 0:
                download_code = image_list[0]
                download_url = image_download_handler.get_image_download_url(download_code)
                self.image_url_list = [download_image_file(download_url, TmpDir().path())]
                self.content = conf().get("bisheng_workflow_default_query_image", "请判断这张图片的内容类型：如果图片包含文档、文字、表格或图表，请提炼其核心主题、关键信息和主要结论，并用清晰的要点列出；如果不是文档类内容，请客观、详细地描述图片中的主要元素、场景和关键信息。")
            else:
                logger.debug(f"[Dingtalk] messageType :{self.message_type} , imageList isEmpty")
        elif self.message_type == 'richText':
            self.ctype = ContextType.IMAGE
            # 钉钉富文本类型消息处理
            image_list = event.get_image_list()
            if len(image_list) > 0:
                for download_code in image_list:
                    download_url = image_download_handler.get_image_download_url(download_code)
                    self.image_url_list.append(download_image_file(download_url, TmpDir().path()))

                if self.rich_text_content and hasattr(self.rich_text_content, "rich_text_list") and self.rich_text_content.rich_text_list:
                    text = "".join(
                        item["text"]
                        for item in self.rich_text_content.rich_text_list
                        if "text" in item and item["text"].strip()
                    )
                if not text:
                    self.content = conf().get("bisheng_workflow_default_query_image", "请判断这张图片的内容类型：如果图片包含文档、文字、表格或图表，请提炼其核心主题、关键信息和主要结论，并用清晰的要点列出；如果不是文档类内容，请客观、详细地描述图片中的主要元素、场景和关键信息。")
                else:
                    self.content = text
            else:
                logger.debug(f"[Dingtalk] messageType :{self.message_type} , imageList isEmpty")
        elif self.message_type == 'file':
            self.ctype = ContextType.FILE
            # 处理文件类型
            if 'content' in event.extensions:
                content = event.extensions['content']
                self.file_content = {
                    'download_code': content.get('downloadCode'),
                    'file_name': content.get('fileName'),
                    'space_id': content.get('spaceId'),
                    'file_id': content.get('fileId'),
                }
                
                download_code = self.file_content['download_code']
                if download_code:
                    download_url = image_download_handler.get_image_download_url(download_code)
                    self.content = self.download_file(
                        download_url, 
                        TmpDir().path(), 
                        self.file_content['file_name']
                    )
                else:
                    logger.warning(f"[Dingtalk] file message has no download_code")
            elif self.message_type == "news":
                self.ctype = ContextType.DAILY_NEWS
                self.content = event.text.content.strip()

        if self.is_group:
            self.from_user_id = event.conversation_id
            self.actual_user_id = event.sender_id
            if self.ctype != ContextType.DAILY_NEWS:
                self.is_at = True
            # 记录群聊信息日志信息
            logger.info(
                f"[DingTalk] 群聊消息 | "
                f"群ID(conversation_id): {event.conversation_id} | "
                f"群名称: {event.conversation_title} | "
                f"发送者ID: {event.sender_id} | "
                f"发送者昵称: {event.sender_nick} | "
                f"发送者staffId: {event.sender_staff_id}"
            )
            group_manager = GroupManager()
            group_manager.update_group(event.conversation_id, event.conversation_title)
        else:
            self.from_user_id = event.sender_id
            self.actual_user_id = event.sender_id
            # 记录单聊信息日志信息
            logger.info(
                f"[DingTalk] 单聊消息 | "
                f"用户ID(sender_id): {event.sender_id} | "
                f"用户昵称: {event.sender_nick} | "
                f"用户staffId: {event.sender_staff_id}"
            )
        self.to_user_id = event.chatbot_user_id
        self.other_user_nickname = event.conversation_title

    def download_file(self, file_url, temp_dir, file_name):
        """下载文件到本地"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36'
        }
        
        try:
            response = requests.get(file_url, headers=headers, stream=True, timeout=60 * 5)
            if response.status_code == 200:
                if not os.path.exists(temp_dir):
                    os.makedirs(temp_dir)

                file_path = os.path.join(temp_dir, file_name)
                with open(file_path, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
                
                logger.info(f"[Dingtalk] File downloaded successfully: {file_name}")
                return file_path
            else:
                logger.error(f"[Dingtalk] Failed to download file, status: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"[Dingtalk] Error downloading file: {str(e)}")
            return None

def download_image_file(image_url, temp_dir):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36'
    }
    # 设置代理
    # self.proxies
    # , proxies=self.proxies
    response = requests.get(image_url, headers=headers, stream=True, timeout=60 * 5)
    if response.status_code == 200:

        # 生成文件名
        file_name = image_url.split("/")[-1].split("?")[0]

        # 检查临时目录是否存在，如果不存在则创建
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        # 将文件保存到临时目录
        file_path = os.path.join(temp_dir, file_name)
        with open(file_path, 'wb') as file:
            file.write(response.content)
        return file_path
    else:
        logger.info(f"[Dingtalk] Failed to download image file, {response.content}")
        return None
