"""
文件存储服务

将所有接收到的文件保存到本地磁盘，按 session_id 组织。
当用户 @bot 处理文件时，从此处查找对应的文件内容。

目录结构:
  data/files/{session_id}/
    ├── 20260709_143022_test.pdf
    ├── 20260709_143522_report.docx
    └── index.json
"""

import os
import json
import shutil
from datetime import datetime
from typing import List, Optional, Dict
from dataclasses import dataclass, field

from common.log import logger
from config import conf, get_appdata_dir


@dataclass
class FileRecord:
    """已保存的文件记录"""
    filepath: str              # 文件绝对路径
    filename: str              # 原始文件名
    saved_at: str              # 保存时间 YYYYMMDD_HHmmss
    filetype: str              # 文件扩展名
    session_id: str = ""       # 会话ID
    content: str = ""          # 文件文本内容（懒加载）


class FileStore:
    """文件存储服务"""

    def __init__(self):
        base_dir = os.path.join(get_appdata_dir(), "files")
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def _session_dir(self, session_id: str) -> str:
        """获取 session 对应的文件目录"""
        # 只取 session_id 的后16位，避免路径过长
        safe_id = session_id.replace("/", "_").replace("\\", "_")[-16:]
        path = os.path.join(self.base_dir, safe_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _index_path(self, session_id: str) -> str:
        return os.path.join(self._session_dir(session_id), "index.json")

    def _load_index(self, session_id: str) -> list:
        """加载文件索引"""
        idx_path = self._index_path(session_id)
        if os.path.exists(idx_path):
            try:
                with open(idx_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return []
        return []

    def _save_index(self, session_id: str, index: list):
        """保存文件索引"""
        idx_path = self._index_path(session_id)
        with open(idx_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def save_file(self, file_path: str, session_id: str, file_name: str = "") -> Optional[FileRecord]:
        """
        保存文件到本地磁盘

        :param file_path: 源文件路径
        :param session_id: 会话ID
        :param file_name: 文件名（可选，不传则取源文件名）
        :return: FileRecord 或 None
        """
        if not os.path.exists(file_path):
            logger.warning(f"[FileStore] 文件不存在: {file_path}")
            return None

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        orig_name = file_name or os.path.basename(file_path)
        ext = os.path.splitext(orig_name)[1].lower() or ".bin"

        # 目标文件名: 时间戳_原始文件名
        dest_name = f"{now}_{orig_name}"
        dest_path = os.path.join(self._session_dir(session_id), dest_name)

        try:
            shutil.copy2(file_path, dest_path)
            file_size = os.path.getsize(dest_path)

            record = {
                "filepath": dest_path,
                "filename": orig_name,
                "saved_at": now,
                "filetype": ext,
                "size": file_size,
            }

            # 更新索引
            index = self._load_index(session_id)
            index.append(record)
            self._save_index(session_id, index)

            logger.info(
                f"[FileStore] 文件已保存: {orig_name} -> {dest_name}, "
                f"session={session_id[:16]}, size={file_size}"
            )

            return FileRecord(
                filepath=dest_path,
                filename=orig_name,
                saved_at=now,
                filetype=ext,
                session_id=session_id,
            )

        except Exception as e:
            logger.exception(f"[FileStore] 文件保存失败: {e}")
            return None

    def get_latest_file(self, session_id: str) -> Optional[FileRecord]:
        """
        获取某个 session 中最新的文件

        :param session_id: 会话ID
        :return: FileRecord 或 None
        """
        index = self._load_index(session_id)
        if not index:
            return None

        latest = index[-1]  # 最后一条是最新的
        if not os.path.exists(latest["filepath"]):
            logger.warning(f"[FileStore] 文件已不存在: {latest['filepath']}")
            return None

        return FileRecord(
            filepath=latest["filepath"],
            filename=latest["filename"],
            saved_at=latest["saved_at"],
            filetype=latest["filetype"],
            session_id=session_id,
        )

    def get_file_by_name(self, session_id: str, keyword: str) -> Optional[FileRecord]:
        """
        按文件名关键词匹配文件

        :param session_id: 会话ID
        :param keyword: 文件名关键词（如 "pdf", "报告", "test"）
        :return: 匹配到的第一个 FileRecord 或 None
        """
        index = self._load_index(session_id)
        keyword_lower = keyword.lower()

        # 从最新到最旧匹配
        for record in reversed(index):
            if keyword_lower in record["filename"].lower():
                if os.path.exists(record["filepath"]):
                    return FileRecord(
                        filepath=record["filepath"],
                        filename=record["filename"],
                        saved_at=record["saved_at"],
                        filetype=record["filetype"],
                        session_id=session_id,
                    )

        return None

    def list_files(self, session_id: str) -> List[Dict]:
        """列出某个 session 的所有文件"""
        index = self._load_index(session_id)
        return [
            {
                "filename": r["filename"],
                "saved_at": r["saved_at"],
                "filetype": r["filetype"],
                "size": r.get("size", 0),
                "exists": os.path.exists(r["filepath"]),
            }
            for r in index
        ]

    def read_file_content(self, file_record: FileRecord) -> str:
        """
        读取文件内容

        :param file_record: 文件记录
        :return: 文件文本内容
        """
        from bot.langgraph_workflow.services.document_loader import DocumentLoader

        if not os.path.exists(file_record.filepath):
            logger.warning(f"[FileStore] 文件不存在: {file_record.filepath}")
            return ""

        doc = DocumentLoader.load(file_record.filepath)
        if doc is None:
            return ""
        return doc.content

    def cleanup_old_files(self, session_id: str, max_files: int = 50):
        """清理旧文件，保留最近 max_files 个"""
        index = self._load_index(session_id)
        if len(index) <= max_files:
            return

        # 删除旧文件
        for record in index[:-max_files]:
            try:
                if os.path.exists(record["filepath"]):
                    os.remove(record["filepath"])
            except Exception:
                pass

        self._save_index(session_id, index[-max_files:])
        logger.info(f"[FileStore] 清理完成: session={session_id[:16]}, 保留={max_files}")