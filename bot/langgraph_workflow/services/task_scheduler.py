"""
任务调度器

存储: data/tasks/tasks.json
支持: 责任人、单次/每天/每周/工作日、到期@提醒
"""

import os
import json
import threading
import time
import re
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass, field, asdict

from common.log import logger
from config import conf, get_appdata_dir


WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
    "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}


@dataclass
class TaskItem:
    id: int
    content: str
    creator_name: str = ""
    creator_id: str = ""
    assignee_name: str = ""
    assignee_id: str = ""
    trigger_time: str = ""
    schedule_type: str = "once"
    schedule_weekdays: list = field(default_factory=list)
    created_at: str = ""
    status: str = "active"
    last_triggered: str = ""


class TaskScheduler:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._tasks_file = os.path.join(get_appdata_dir(), "tasks", "tasks.json")
        os.makedirs(os.path.dirname(self._tasks_file), exist_ok=True)
        self._tasks: List[TaskItem] = []
        self._next_id = 1
        self._load()
        self._running = False
        self._thread = None

    def _load(self):
        try:
            if os.path.exists(self._tasks_file):
                with open(self._tasks_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._tasks = [TaskItem(**t) for t in data.get("tasks", [])]
                self._next_id = data.get("next_id", 1)
        except Exception as e:
            logger.warning(f"[Task] 加载失败: {e}")

    def _save(self):
        try:
            data = {"next_id": self._next_id, "tasks": [asdict(t) for t in self._tasks]}
            with open(self._tasks_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[Task] 保存失败: {e}")

    def add_task(self, content, trigger_time, creator_name="", creator_id="",
                 assignee_name="", assignee_id="",
                 schedule_type="once", schedule_weekdays=None):
        task = TaskItem(
            id=self._next_id, content=content,
            creator_name=creator_name, creator_id=creator_id,
            assignee_name=assignee_name or creator_name,
            assignee_id=assignee_id or creator_id,
            trigger_time=trigger_time,
            schedule_type=schedule_type,
            schedule_weekdays=schedule_weekdays or [],
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._tasks.append(task)
        self._next_id += 1
        self._save()
        return task

    def list_tasks(self, user_id=""):
        active = [t for t in self._tasks if t.status == "active"]
        if user_id:
            active = [t for t in active if t.creator_id == user_id or t.assignee_id == user_id]
        return sorted(active, key=lambda t: t.trigger_time)

    def cancel_task(self, task_id, user_id=""):
        for t in self._tasks:
            if t.id == task_id and t.status == "active":
                if user_id and t.creator_id != user_id and t.assignee_id != user_id:
                    return False
                t.status = "cancelled"
                self._save()
                return True
        return False

    def _get_due_tasks(self):
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M")
        today_wd = now.weekday()
        due = []
        for t in self._tasks:
            if t.status != "active":
                continue
            if t.trigger_time > now_str:
                continue
            if t.last_triggered:
                if t.schedule_type == "once":
                    continue
                today_str = now.strftime("%Y-%m-%d")
                if t.last_triggered.startswith(today_str):
                    continue
            if t.schedule_type == "weekdays" and today_wd >= 5:
                continue
            if t.schedule_type == "weekly" and today_wd not in t.schedule_weekdays:
                continue
            t.last_triggered = now_str
            if t.schedule_type == "once":
                t.status = "done"
            due.append(t)
        if due:
            self._save()
        return due

    @staticmethod
    def parse_time(text: str) -> Optional[str]:
        now = datetime.now()
        m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})\s+(\d{1,2}):(\d{2})", text)
        if m:
            return f"{m.group(1)} {int(m.group(2)):02d}:{m.group(3)}"
        m = re.search(r"(?:今晚|今天)\s*(\d{1,2})点", text)
        if m:
            h = int(m.group(1))
            t = now.replace(hour=h, minute=0, second=0, microsecond=0)
            return t.strftime("%Y-%m-%d %H:%M") if t > now else (t + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        for kw, delta in [("后天", 2), ("明天", 1)]:
            if kw in text:
                m = re.search(rf"{kw}\s*(\d{{1,2}})点", text)
                h = int(m.group(1)) if m else 9
                return (now + timedelta(days=delta)).replace(hour=h, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        if re.search(r"半", text):
            return (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M")
        m = re.search(r"(\d+)\s*个?\s*分钟", text)
        if m:
            return (now + timedelta(minutes=int(m.group(1)))).strftime("%Y-%m-%d %H:%M")
        m = re.search(r"(\d+)\s*个?\s*小时", text)
        if m:
            return (now + timedelta(hours=int(m.group(1)))).strftime("%Y-%m-%d %H:%M")
        return None

    @staticmethod
    def parse_schedule(text: str) -> tuple:
        if "每天" in text or "每日" in text:
            return ("daily", [])
        if "工作日" in text:
            return ("weekdays", [])
        m = re.search(r"每周\s*([一二三四五六日天1-7]+)", text)
        if m:
            weekdays = []
            for ch in m.group(1):
                if ch in WEEKDAY_MAP:
                    d = WEEKDAY_MAP[ch]
                    if d not in weekdays:
                        weekdays.append(d)
            return ("weekly", sorted(weekdays)) if weekdays else ("weekly", [])
        return ("once", [])

    @staticmethod
    def parse_assignee(text: str) -> tuple:
        """
        解析责任人
        "@张三 做报表" → ("做报表", "张三")
        "提醒我开会" → ("提醒我开会", None)
        """
        m = re.search(r"@(\S+)", text)
        if m:
            name = m.group(1)
            remaining = text.replace(m.group(0), "", 1).strip()
            return (remaining, name)
        m = re.search(r"(?:提醒|告诉|通知)\s*([一-鿿]{2,3})(?:\s|，|。|的|$)", text)
        if m:
            name = m.group(1)
            # 排除名字含"我"的情况（提醒我、提醒我自己等）
            if "我" not in name:
                remaining = text.replace(m.group(0), "", 1).strip()
                return (remaining, name)
        return (text, None)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run_loop(self):
        while self._running:
            try:
                for task in self._get_due_tasks():
                    self._notify_task(task)
            except Exception as e:
                logger.error(f"[Task] 调度异常: {e}")
            time.sleep(30)

    def _notify_task(self, task):
        try:
            from channel.dingtalk.dingtalk_http_client import DingtalkHttp
            import requests
            http = DingtalkHttp()
            token = http.ensure_access_token()
            if not token:
                return
            target = task.assignee_id or task.creator_id
            if not target:
                return
            name = task.assignee_name or task.creator_name
            msg = f"[任务提醒] @{name} {task.content}"
            url = f"https://api.dingtalk.com/v1.0/im/users/{target}/messages"
            headers = {"Content-Type": "application/json", "x-acs-dingtalk-access-token": token}
            requests.post(url, headers=headers, json={"msgtype": "text", "text": {"content": msg}}, timeout=10)
            logger.info(f"[Task] 已提醒 {name}: {task.content}")
        except Exception as e:
            logger.error(f"[Task] 提醒失败: {e}")