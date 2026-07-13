"""
任务系统工具函数 — 供 LLM 调用

LLM 通过工具定义 (function calling) 来创建/查询/删除任务，
不需要自己解析时间，LLM 自然语言理解后填入结构化的参数。
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from bot.langgraph_workflow.services.task_scheduler import TaskScheduler


def create_task(content: str, trigger_time: str, assignee: str = "",
                repeat: str = "once", repeat_days: list = None) -> str:
    """
    创建定时任务

    Args:
        content: 任务内容
        trigger_time: 触发时间，格式 "YYYY-MM-DD HH:MM"，例如 "2026-07-09 21:01"
        assignee: 责任人，空表示创建者本人
        repeat: 重复类型，once/daily/weekly/weekdays
        repeat_days: 每周哪几天，如 [0,2,4] 表示周一三五
    """
    scheduler = TaskScheduler()
    schedule_map = {"once":"once","daily":"daily","每周":"weekly","工作日":"weekdays","weekdays":"weekdays"}
    st = "once"
    for k, v in schedule_map.items():
        if k in repeat: st = v; break
    scheduler.add_task(
        content=content, trigger_time=trigger_time,
        creator_name="", creator_id="",
        assignee_name=assignee or "",
        schedule_type=st, schedule_weekdays=repeat_days or [],
    )
    return f"已记录：{content}（{trigger_time}）"


def list_tasks(filter_by: str = "") -> str:
    """
    查看任务列表

    Args:
        filter_by: 筛选条件，""=全部, "upcoming"=临近任务
    """
    scheduler = TaskScheduler()
    tasks = scheduler.list_tasks()
    if not tasks: return "当前没有待办任务。"

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if filter_by == "upcoming":
        tasks = sorted([t for t in tasks if t.trigger_time > now], key=lambda t: t.trigger_time)[:5]
        if not tasks: return "近期没有待办任务。"
        return "\n".join([f"#{t.id} {t.trigger_time} {t.content}" for t in tasks])

    return "\n".join([f"#{t.id} {t.trigger_time} {t.content}" for t in tasks])


def cancel_task(task_id: int) -> str:
    """
    取消任务

    Args:
        task_id: 任务编号
    """
    scheduler = TaskScheduler()
    return f"已取消任务 #{task_id}" if scheduler.cancel_task(task_id) else f"未找到任务 #{task_id}。"