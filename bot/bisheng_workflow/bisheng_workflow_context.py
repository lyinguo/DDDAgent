# encoding: utf-8

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WorkflowRequestContext:
    """
    工作流请求上下文，封装所有可能的输入参数
    后续新增功能只需在此类添加字段，无需修改方法签名
    """
    # 会话相关
    session_id: str = ""
    messages: str = ""

    # 用户信息
    user_name: str = ""
    user_title: str = ""

    # 文件相关
    upload_file_url: str = ""
    image_url_list: List[str] = field(default_factory=list)

    # 内容相关
    wechat_article_content: str = ""
    daily_news_content:  str = ""
    push_daily_news_content: str = ""

    def to_input_data(self, schema_fields: list = None) -> dict:
        """
        转换为API请求的input_data格式
        工作流使用form_input（表单输入）模式，数据需要包裹在form_input字段中
        :param schema_fields: 从初始化响应中提取的表单字段定义列表，用于映射正确的key
        : return: API请求所需的input_data字典
        """
        # 根据schema_fields动态构建字段映射
        if schema_fields:
            # 通过label匹配我们的字段名到实际key
            label_to_key = {}
            for sf in schema_fields:
                label = sf.get("label", "")
                key = sf.get("key", "")
                label_to_key[label] = key

            # 先构建 flat 数据（用我们的key）
            raw_data = {
                "用户输入问题": self.messages,
                "用户姓名": self.user_name or "",
                "职位": self.user_title or "",
                "用户上传文件": [self.upload_file_url] if self.upload_file_url else "",
                "微信文章内容": self.wechat_article_content or "",
                "每日新闻内容": self.daily_news_content or "",
                "用户上传图片": self.image_url_list if self.image_url_list else "",
                "定时推送新闻内容": self.push_daily_news_content or "",
            }

            # 用实际key替换
            form_data = {}
            for our_label, value in raw_data.items():
                actual_key = label_to_key.get(our_label)
                if actual_key:
                    form_data[actual_key] = value
                else:
                    # 如果没有匹配到，可能字段被删了，保留空值
                    pass
        else:
            # 降级：无schema时用默认key
            form_data = {
                "user_input": self.messages,
                "user_name": self.user_name or "",
                "user_title": self.user_title or "",
                "dialog_files_content": [self.upload_file_url] if self.upload_file_url else "",
                "wechat_article_content": self.wechat_article_content or "",
                "daily_news_content": self.daily_news_content or "",
                "images_file": self.image_url_list if self.image_url_list else "",
                "push_daily_news_content": self.push_daily_news_content or "",
            }

        return form_data