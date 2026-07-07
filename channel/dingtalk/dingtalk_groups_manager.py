# encoding: utf-8
"""
群信息管理器 - 自动维护群ID和群名的映射关系
"""

import json
import os
import threading
from common.log import logger
from common.singleton import singleton

@singleton
class GroupManager: 
    _lock = threading.Lock()
    
    def __init__(self):
        # 群信息存储在项目根目录
        self.groups_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 
            "groups_info/groups.json"
        )
        self.groups_data = self._load_groups()
        self._file_lock = threading.Lock()
    
    def _load_groups(self) -> dict:
        """加载群信息"""
        try:
            if os.path.exists(self.groups_file):
                with open(self.groups_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"[GroupManager] 加载群信息失败: {e}")
        return {"groups": {}}
    
    def _save_groups(self):
        """保存群信息"""
        try:
            with self._file_lock:
                with open(self.groups_file, "w", encoding="utf-8") as f:
                    json.dump(self.groups_data, f, ensure_ascii=False, indent=2)
            logger.debug(f"[GroupManager] 群信息已保存")
        except Exception as e: 
            logger.error(f"[GroupManager] 保存群信息失败: {e}")
    
    def update_group(self, conversation_id: str, conversation_title: str):
        """
        更新群信息 - 在收到群消息时调用
        如果群ID存在但群名变了，会自动更新群名
        """
        if not conversation_id or not conversation_title:
            return
        
        groups = self.groups_data.get("groups", {})
        
        if conversation_id in groups:
            if groups[conversation_id]["name"] != conversation_title:
                old_name = groups[conversation_id]["name"]
                groups[conversation_id]["name"] = conversation_title
                self._save_groups()
                logger.info(f"[GroupManager] 群名变更:  {old_name} -> {conversation_title}")
        else:
            groups[conversation_id] = {"name": conversation_title}
            self.groups_data["groups"] = groups
            self._save_groups()
            logger.info(f"[GroupManager] 发现新群: {conversation_title} ({conversation_id})")
    
    def get_group_id_by_name(self, group_name: str) -> str:
        """根据群名获取群ID"""
        groups = self.groups_data.get("groups", {})
        for gid, info in groups.items():
            if info.get("name") == group_name:
                return gid
        return None
    
    def get_group_ids_by_names(self, group_names: list) -> list:
        """根据群名列表获取群ID列表"""
        result = []
        for name in group_names:
            gid = self.get_group_id_by_name(name)
            if gid:
                result.append(gid)
                logger.debug(f"[GroupManager] 群名 '{name}' -> ID: {gid}")
            else:
                logger.warning(f"[GroupManager] 未找到群 '{name}'，请确保机器人已在该群收到过消息")
        return result
    
    def get_all_groups(self) -> dict:
        """获取所有群信息"""
        return self.groups_data.get("groups", {})