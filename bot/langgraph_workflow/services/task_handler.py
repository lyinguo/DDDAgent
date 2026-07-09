"""
任务指令处理器
"""

import re
from typing import Optional
from bot.langgraph_workflow.services.task_scheduler import TaskScheduler


def handle_task_command(query, user_name="", user_id="",
                        is_group=False, group_name="") -> Optional[str]:
    scheduler = TaskScheduler()
    text = query.strip()

    if text in ["查看任务", "我的任务", "任务列表"]:
        tasks = scheduler.list_tasks(user_id=user_id)
        if not tasks:
            return "当前没有待办任务。"
        lines = ["= 待办任务 ="]
        for t in tasks:
            who = f"[{t.assignee_name}]" if t.assignee_name else ""
            rpt = {"once": "", "daily": " [每天]", "weekly": " [每周]", "weekdays": " [工作日]"}.get(t.schedule_type, "")
            lines.append(f"  #{t.id} {t.trigger_time} {who}{rpt} {t.content}")
        return "\n".join(lines)

    m = re.match(r"删除任务\s*#?(\d+)", text)
    if m:
        tid = int(m.group(1))
        return f"已取消任务 #{tid}" if scheduler.cancel_task(tid, user_id=user_id) else f"未找到任务 #{tid} 或无权取消。"

    m = re.match(r"(?:帮我)?(?:记一下|提醒|记住|添加任务)\s*(.*)", text)
    if not m:
        return None
    content_part = m.group(1).strip()
    if not content_part:
        return None

    # 去掉"提醒我/帮我"中的"我"
    content_part = re.sub(r"^我\s*", "", content_part).strip()

    remaining, assignee_name = TaskScheduler.parse_assignee(content_part)
    if assignee_name:
        content_part = remaining

    schedule_type, schedule_weekdays = TaskScheduler.parse_schedule(content_part)

    trigger_time = TaskScheduler.parse_time(content_part)
    if trigger_time is None:
        return "没识别到时间。格式示例：帮我记一下明天9点开会"

    task_content = re.sub(
        r"(明天|后天|今晚|今天|早上|下午|晚上|工作日|每天|每日|"
        r"每周[一二三四五六日天1-7]+|"
        r"\d{1,2}:\d{2}|\d{1,2}点|\d+分钟|\d+小时|半)",
        "", content_part
    ).strip()
    if not task_content:
        task_content = content_part

    scheduler.add_task(
        content=task_content,
        trigger_time=trigger_time,
        creator_name=user_name, creator_id=user_id,
        assignee_name=assignee_name or user_name,
        assignee_id=user_id,
        schedule_type=schedule_type,
        schedule_weekdays=schedule_weekdays,
    )

    who = f"责任人：{assignee_name} " if assignee_name else ""
    rpt_str = {"once": "", "daily": "，每天重复", "weekly": "，每周重复", "weekdays": "，工作日重复"}.get(schedule_type, "")
    return f"已记下：{task_content}（{who}{trigger_time}{rpt_str}）"