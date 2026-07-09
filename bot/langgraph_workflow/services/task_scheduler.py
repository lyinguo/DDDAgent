"""
任务调度器

存储: data/tasks/tasks.db (SQLite)
支持: 责任人、单次/每天/每周/工作日、到期@提醒
"""

import os
import sqlite3
import threading
import time
import re
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass

from common.log import logger
from config import get_appdata_dir


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
    schedule_weekdays: str = ""
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
        self._db_path = os.path.join(get_appdata_dir(), "tasks", "tasks.db")
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._local = threading.local()
        self._init_db()
        self._running = False
        self._thread = None

    def _get_conn(self):
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                creator_name TEXT DEFAULT '',
                creator_id TEXT DEFAULT '',
                assignee_name TEXT DEFAULT '',
                assignee_id TEXT DEFAULT '',
                trigger_time TEXT NOT NULL,
                schedule_type TEXT DEFAULT 'once',
                schedule_weekdays TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                last_triggered TEXT DEFAULT ''
            )
        """)
        conn.commit()
        logger.info(f"[Task] 数据库就绪: {self._db_path}")

    def add_task(self, content, trigger_time, creator_name="", creator_id="",
                 assignee_name="", assignee_id="",
                 schedule_type="once", schedule_weekdays=None) -> TaskItem:
        conn = self._get_conn()
        weekdays_str = ",".join(str(d) for d in (schedule_weekdays or []))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = conn.execute(
            """INSERT INTO tasks (content, creator_name, creator_id, assignee_name,
               assignee_id, trigger_time, schedule_type, schedule_weekdays, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (content, creator_name, creator_id, assignee_name or creator_name,
             assignee_id or creator_id, trigger_time, schedule_type, weekdays_str, now)
        )
        conn.commit()
        task_id = cursor.lastrowid
        logger.info(f"[Task] 添加 #{task_id}: [{assignee_name}] {content} @ {trigger_time}")
        return TaskItem(
            id=task_id, content=content, creator_name=creator_name,
            creator_id=creator_id, assignee_name=assignee_name or creator_name,
            assignee_id=assignee_id or creator_id,
            trigger_time=trigger_time, schedule_type=schedule_type,
            schedule_weekdays=weekdays_str, created_at=now,
        )

    def list_tasks(self, user_id="") -> List[TaskItem]:
        conn = self._get_conn()
        if user_id:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status='active' AND "
                "(creator_id=? OR assignee_id=?) ORDER BY trigger_time",
                (user_id, user_id)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status='active' ORDER BY trigger_time"
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def cancel_task(self, task_id, user_id="") -> bool:
        conn = self._get_conn()
        if user_id:
            cursor = conn.execute(
                "UPDATE tasks SET status='cancelled' WHERE id=? AND status='active' "
                "AND (creator_id=? OR assignee_id=?)",
                (task_id, user_id, user_id)
            )
        else:
            cursor = conn.execute(
                "UPDATE tasks SET status='cancelled' WHERE id=? AND status='active'",
                (task_id,)
            )
        conn.commit()
        return cursor.rowcount > 0

    def _get_due_tasks(self) -> List[TaskItem]:
        conn = self._get_conn()
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M")
        today_str = now.strftime("%Y-%m-%d")
        today_wd = now.weekday()

        rows = conn.execute(
            "SELECT * FROM tasks WHERE status='active' AND trigger_time<=?", (now_str,)
        ).fetchall()

        due = []
        for row in rows:
            t = self._row_to_task(row)
            if t.last_triggered:
                if t.schedule_type == "once":
                    continue
                if t.last_triggered.startswith(today_str):
                    continue
            if t.schedule_type == "weekdays" and today_wd >= 5:
                continue
            if t.schedule_type == "weekly":
                weekdays = [int(x) for x in t.schedule_weekdays.split(",") if x]
                if today_wd not in weekdays:
                    continue

            conn.execute("UPDATE tasks SET last_triggered=? WHERE id=?",
                         (now_str, t.id))
            if t.schedule_type == "once":
                conn.execute("UPDATE tasks SET status='done' WHERE id=?", (t.id,))
            due.append(t)

        if due:
            conn.commit()
        return due

    @staticmethod
    def _row_to_task(row) -> TaskItem:
        return TaskItem(
            id=row["id"], content=row["content"],
            creator_name=row["creator_name"], creator_id=row["creator_id"],
            assignee_name=row["assignee_name"], assignee_id=row["assignee_id"],
            trigger_time=row["trigger_time"],
            schedule_type=row["schedule_type"],
            schedule_weekdays=row["schedule_weekdays"],
            created_at=row["created_at"],
            status=row["status"],
            last_triggered=row["last_triggered"],
        )

    # ===== 时间/责任人/周期解析 =====

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
        m = re.search(r"@(\S+)", text)
        if m:
            name = m.group(1)
            remaining = text.replace(m.group(0), "", 1).strip()
            return (remaining, name)
        m = re.search(r"(?:提醒|告诉|通知)\s*([一-鿿]{2,3})(?:\s|，|。|的|$)", text)
        if m:
            name = m.group(1)
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