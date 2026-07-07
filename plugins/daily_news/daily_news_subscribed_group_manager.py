# encoding: utf-8
"""
订阅群管理器 - 管理订阅每日新闻推送的群聊名称列表
"""

import json
import os
import threading
from common.log import logger
from common.singleton import singleton

@singleton
class SubscribedGroupManager:
    _lock = threading.Lock()

    def __init__(self):
        # 订阅信息存储在项目根目录
        self.subscribed_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "groups_info/subscribed_groups.json"
        )
        self.subscribed_data = self._load_subscribed()
        self._file_lock = threading.Lock()

    def _load_subscribed(self) -> dict:
        """加载订阅信息"""
        try:
            if os.path.exists(self.subscribed_file):
                with open(self.subscribed_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"[SubscribedGroupManager] 加载订阅信息失败: {e}")
        return {"subscribed_groups": []}

    def _save_subscribed(self):
        """保存订阅信息"""
        try:
            with self._file_lock:
                with open(self.subscribed_file, "w", encoding="utf-8") as f:
                    json.dump(self.subscribed_data, f, ensure_ascii=False, indent=2)
            logger.debug(f"[SubscribedGroupManager] 订阅信息已保存")
        except Exception as e: 
            logger.error(f"[SubscribedGroupManager] 保存订阅信息失败: {e}")

    def subscribe(self, group_name: str) -> bool:
        """
        添加群到订阅列表
        : param group_name: 群名称
        :return: True=新增成功, False=已存在
        """
        if not group_name: 
            return False

        subscribed_list = self.subscribed_data.get("subscribed_groups", [])

        if group_name in subscribed_list:
            logger.info(f"[SubscribedGroupManager] 群 '{group_name}' 已订阅，无需重复订阅")
            return False

        subscribed_list.append(group_name)
        self.subscribed_data["subscribed_groups"] = subscribed_list
        self._save_subscribed()
        logger.info(f"[SubscribedGroupManager] 群 '{group_name}' 订阅成功")
        return True

    def unsubscribe(self, group_name: str) -> bool:
        """
        从订阅列表中移除群
        :param group_name: 群名称
        : return: True=移除成功, False=不存在
        """
        if not group_name:
            return False

        subscribed_list = self.subscribed_data.get("subscribed_groups", [])

        if group_name not in subscribed_list:
            logger.info(f"[SubscribedGroupManager] 群 '{group_name}' 未订阅")
            return False

        subscribed_list.remove(group_name)
        self.subscribed_data["subscribed_groups"] = subscribed_list
        self._save_subscribed()
        logger.info(f"[SubscribedGroupManager] 群 '{group_name}' 已取消订阅")
        return True

    def is_subscribed(self, group_name: str) -> bool:
        """检查群是否已订阅"""
        return group_name in self.subscribed_data.get("subscribed_groups", [])

    def get_all_subscribed_groups(self) -> list:
        """获取所有已订阅的群名称列表"""
        return self.subscribed_data.get("subscribed_groups", [])

    def get_subscribed_count(self) -> int:
        """获取订阅群数量"""
        return len(self.subscribed_data.get("subscribed_groups", []))