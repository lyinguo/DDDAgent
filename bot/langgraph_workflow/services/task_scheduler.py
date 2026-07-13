"""
任务调度器

核心机制（双重保障）：
1. Timer 机制（主要）：任务创建时立即计算延迟，用 threading.Timer 定时触发
2. 轮询机制（兜底）：后台线程每30秒查数据库，确保进程重启后仍能触发
"""

import os, sqlite3, threading, time, json, requests
from datetime import datetime, timedelta
from typing import List, Optional
from dataclasses import dataclass
from common.log import logger
from config import get_appdata_dir
from bridge.context import ContextType

WEEKDAY_MAP = {"一":0,"二":1,"三":2,"四":3,"五":4,"六":5,"日":6,"天":6,"1":0,"2":1,"3":2,"4":3,"5":4,"6":5,"7":6}

# 存储每个用户的 webhook，用于发送提醒
_user_webhooks: dict = {}
_webhook_lock = threading.Lock()

def register_webhook(user_id: str, webhook: str):
    with _webhook_lock:
        _user_webhooks[user_id] = webhook

def get_webhook(user_id: str) -> Optional[str]:
    with _webhook_lock:
        return _user_webhooks.get(user_id)


@dataclass
class TaskItem:
    id: int; content: str; creator_name: str = ""; creator_id: str = ""
    assignee_name: str = ""; assignee_id: str = ""; trigger_time: str = ""
    schedule_type: str = "once"; schedule_weekdays: str = ""
    created_at: str = ""; status: str = "active"; last_triggered: str = ""
    group_name: str = ""  # 推送目标群聊名称（为空则推送给个人）


class TaskScheduler:
    _instance = None; _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized: return
        self._initialized = True
        self._db_path = os.path.join(get_appdata_dir(), "tasks", "tasks.db")
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._local = threading.local(); self._init_db()
        self._running = False; self._thread = None
        self._channel = None  # 钉钉 channel 实例，用于群聊推送
        self._timers = {}     # task_id -> timer，已调度的定时器

    def _get_conn(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self):
        self._get_conn().execute("""CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,content TEXT NOT NULL,
            creator_name TEXT DEFAULT '',creator_id TEXT DEFAULT '',
            assignee_name TEXT DEFAULT '',assignee_id TEXT DEFAULT '',
            trigger_time TEXT NOT NULL,schedule_type TEXT DEFAULT 'once',
            schedule_weekdays TEXT DEFAULT '',created_at TEXT DEFAULT '',
            status TEXT DEFAULT 'active',last_triggered TEXT DEFAULT '',
            group_name TEXT DEFAULT '')""")
        self._get_conn().commit()
        # 兼容旧表：如果缺少 group_name 列则添加
        try:
            self._get_conn().execute("ALTER TABLE tasks ADD COLUMN group_name TEXT DEFAULT ''")
            self._get_conn().commit()
        except Exception:
            pass  # 列已存在，忽略

    def add_task(self, content, trigger_time, creator_name="", creator_id="",
                 assignee_name="", assignee_id="", schedule_type="once", schedule_weekdays=None,
                 group_name="") -> TaskItem:
        conn = self._get_conn(); wds = ",".join(str(d) for d in (schedule_weekdays or []))
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute("INSERT INTO tasks (content,creator_name,creator_id,assignee_name,"
            "assignee_id,trigger_time,schedule_type,schedule_weekdays,created_at,group_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (content, creator_name, creator_id, assignee_name or creator_name,
             assignee_id or creator_id, trigger_time, schedule_type, wds, now, group_name))
        conn.commit()
        task = TaskItem(id=cur.lastrowid, content=content, creator_name=creator_name, creator_id=creator_id,
            assignee_name=assignee_name or creator_name, assignee_id=assignee_id or creator_id,
            trigger_time=trigger_time, schedule_type=schedule_type, schedule_weekdays=wds, created_at=now,
            group_name=group_name)
        logger.info(f"[Task] add #{task.id}: {content} ({trigger_time}) group={group_name}")

        # ===== 核心：立刻调度一个 Timer =====
        self._schedule_task_timer(task)

        return task

    def _schedule_task_timer(self, task):
        """为任务调度一个 threading.Timer，到时间直接触发通知"""
        try:
            # trigger_time 格式可能是 "YYYY-MM-DD HH:MM" 或 "YYYY-MM-DD HH:MM:SS"
            trigger_str = task.trigger_time
            if len(trigger_str) == 16:  # "YYYY-MM-DD HH:MM"
                trigger_dt = datetime.strptime(trigger_str, "%Y-%m-%d %H:%M")
            else:  # "YYYY-MM-DD HH:MM:SS"
                trigger_dt = datetime.strptime(trigger_str[:16], "%Y-%m-%d %H:%M")
            delay = (trigger_dt - datetime.now()).total_seconds()
            if delay <= 0:
                delay = 1  # 至少等1秒，避免立即触发时的竞态
            timer = threading.Timer(delay, self._fire_task, args=[task])
            timer.daemon = True
            # 取消同 ID 的旧 timer（如果有）
            old = self._timers.get(task.id)
            if old:
                old.cancel()
            self._timers[task.id] = timer
            timer.start()
            logger.info(f"[Task] Timer 已调度: #{task.id} '{task.content}' 将在 {delay:.0f} 秒后触发")
        except Exception as e:
            logger.error(f"[Task] 调度 Timer 失败: {e}")

    def _fire_task(self, task):
        """Timer 触发：直接通知并更新数据库状态"""
        logger.info(f"[Task] Timer 触发: #{task.id} '{task.content}'")
        try:
            self._notify_task(task)
            # 通知成功后更新数据库
            ns = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn = self._get_conn()
            conn.execute("UPDATE tasks SET last_triggered=?, status='done' WHERE id=? AND status='active'",
                         (ns, task.id))
            conn.commit()
            logger.info(f"[Task] #{task.id} 已完成")
        except Exception as e:
            logger.error(f"[Task] 通知异常: {e}")

    def list_tasks(self, user_id=""):
        conn = self._get_conn()
        if user_id:
            rows = conn.execute("SELECT * FROM tasks WHERE status='active' AND "
                "(creator_id=? OR assignee_id=?) ORDER BY trigger_time", (user_id, user_id))
        else:
            rows = conn.execute("SELECT * FROM tasks WHERE status='active' ORDER BY trigger_time")
        return [self._row_to_task(r) for r in rows.fetchall()]

    def cancel_task(self, task_id, user_id=""):
        conn = self._get_conn()
        cur = conn.execute("UPDATE tasks SET status='cancelled' WHERE id=? AND status='active'",
            [task_id] + ([user_id, user_id] if user_id else []))
        conn.commit()
        # 取消对应的 timer
        timer = self._timers.pop(task_id, None)
        if timer:
            timer.cancel()
            logger.info(f"[Task] 已取消 Timer: #{task_id}")
        return cur.rowcount > 0

    def update_task(self, task_id, content=None, trigger_time=None, assignee_name=None,
                    schedule_type=None, schedule_weekdays=None, group_name=None) -> bool:
        """
        更新任务信息，只更新非 None 的字段
        :return: True=更新成功, False=任务不存在或已取消
        """
        task = self._get_task_by_id(task_id)
        if not task or task.status != "active":
            return False

        updates = []
        values = []
        if content is not None:
            updates.append("content=?")
            values.append(content)
        if trigger_time is not None:
            updates.append("trigger_time=?")
            values.append(trigger_time)
        if assignee_name is not None:
            updates.append("assignee_name=?")
            values.append(assignee_name)
        if schedule_type is not None:
            updates.append("schedule_type=?")
            values.append(schedule_type)
        if schedule_weekdays is not None:
            wds = ",".join(str(d) for d in schedule_weekdays)
            updates.append("schedule_weekdays=?")
            values.append(wds)
        if group_name is not None:
            updates.append("group_name=?")
            values.append(group_name)

        if not updates:
            return False

        values.append(task_id)
        conn = self._get_conn()
        conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id=?", values)
        conn.commit()

        # 重新调度 timer（如果时间变了）
        if trigger_time is not None:
            task.trigger_time = trigger_time
            self._schedule_task_timer(task)

        logger.info(f"[Task] 更新 #{task_id}: {', '.join(updates)}")
        return True

    def _get_task_by_id(self, task_id):
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def _get_due_tasks(self):
        conn = self._get_conn(); now = datetime.now(); ns = now.strftime("%Y-%m-%d %H:%M")
        today = now.strftime("%Y-%m-%d"); wd = now.weekday()
        due = []
        for row in conn.execute("SELECT * FROM tasks WHERE status='active' AND trigger_time<=?", (ns,)).fetchall():
            t = self._row_to_task(row)
            if t.last_triggered and (t.schedule_type == "once" or t.last_triggered.startswith(today)): continue
            if t.schedule_type == "weekdays" and wd >= 5: continue
            if t.schedule_type == "weekly":
                if wd not in [int(x) for x in t.schedule_weekdays.split(",") if x]: continue
            conn.execute("UPDATE tasks SET last_triggered=? WHERE id=?", (ns, t.id))
            if t.schedule_type == "once": conn.execute("UPDATE tasks SET status='done' WHERE id=?", (t.id,))
            due.append(t)
        if due: conn.commit(); logger.info(f"[Task] 轮询触发 {len(due)} 个任务")
        return due

    @staticmethod
    def _row_to_task(row):
        # sqlite3.Row 在 Python 3.9+ 有 .get()，低版本用列名判断
        try:
            group_name = row["group_name"]
        except (KeyError, IndexError):
            group_name = ""
        return TaskItem(id=row["id"],content=row["content"],
            creator_name=row["creator_name"],creator_id=row["creator_id"],
            assignee_name=row["assignee_name"],assignee_id=row["assignee_id"],
            trigger_time=row["trigger_time"],schedule_type=row["schedule_type"],
            schedule_weekdays=row["schedule_weekdays"],created_at=row["created_at"],
            status=row["status"],last_triggered=row["last_triggered"],
            group_name=group_name)

    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("[Task] 调度器已启动（轮询 + Timer）")

    def stop(self):
        self._running = False
        # 取消所有 timer
        for tid, timer in list(self._timers.items()):
            timer.cancel()
        self._timers.clear()

    def set_channel(self, channel):
        """注册钉钉 channel 实例，用于群聊推送"""
        self._channel = channel
        logger.info("[Task] DingTalk channel 已注册")

    def _run_loop(self):
        """后台轮询线程：兜底机制，每30秒检查到期任务"""
        while self._running:
            try:
                for task in self._get_due_tasks():
                    logger.info(f"[Task] 轮询发现到期任务: #{task.id} '{task.content}'")
                    self._notify_task(task)
            except Exception as e:
                logger.error(f"[Task] 轮询异常: {e}")
            time.sleep(30)

    def _notify_task(self, task):
        """通过钉钉群聊或 webhook 发送任务提醒"""
        # 优先群聊推送
        if task.group_name and self._channel:
            logger.info(f"[Task] 尝试群聊推送: #{task.id} -> {task.group_name}")
            try:
                self._notify_task_to_group(task)
                return
            except Exception as e:
                logger.warning(f"[Task] 群聊推送失败，降级为个人通知: {e}")

        # 个人 webhook 推送（原有逻辑）
        uid = task.assignee_id or task.creator_id
        if not uid:
            logger.warning(f"[Task] 无用户ID，无法推送 #{task.id}")
            return
        webhook = get_webhook(uid)
        if not webhook:
            logger.warning(f"[Task] 无webhook，无法提醒 {task.assignee_name}")
            return
        try:
            body = {"msgtype":"text","text":{"content":f"[提醒] {task.content}"}}
            resp = requests.post(webhook, json=body, timeout=10)
            if resp.status_code == 200:
                logger.info(f"[Task] 已提醒 {task.assignee_name}")
            else:
                logger.warning(f"[Task] 发送失败: {resp.status_code}")
        except Exception as e:
            logger.error(f"[Task] 通知异常: {e}")

    def _notify_task_to_group(self, task):
        """通过钉钉 Stream reply 机制向群聊发送任务提醒（不走机器人处理流程）"""
        from dingtalk_stream.chatbot import reply_specified_group_chat, TextContent
        from channel.dingtalk.dingtalk_groups_manager import GroupManager
        from bridge.reply import Reply, ReplyType

        # 根据群名称获取群ID
        group_id = GroupManager().get_group_id_by_name(task.group_name)
        if not group_id:
            logger.warning(f"[Task] 未找到群 '{task.group_name}' 的ID")
            raise ValueError(f"群 '{task.group_name}' 未找到")

        # 构造一条伪 incoming_message，指向该群
        incoming_message = reply_specified_group_chat(group_id)
        # 直接用 reply_message 发送（不走 handle_group / 机器人处理流程）
        reply = Reply(ReplyType.TEXT, f"⏰ 任务提醒：{task.content}")

        logger.info(f"[Task] 向群 '{task.group_name}'({group_id}) 发送任务提醒: {task.content}")
        self._channel.reply_message(reply, incoming_message)
        logger.info(f"[Task] 群聊消息发送完成: #{task.id}")