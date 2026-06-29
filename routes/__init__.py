"""
问渠 (Wenqu) v1.1 — 路由包

路由模块：
- course_routes: 课程管理、文件上传、分章、掌握项、知识快照
- task_routes: 异步任务管理
- defense_settings: 结业答辩、LLM设置、课程设置、学习事件、日记、思辨笔记
- websocket_routes: WebSocket 实时对话（单聊/群聊）
"""

from routes.course_routes import (
    api_list_courses,
    api_create_course,
    api_upload_file,
    api_get_course,
    api_remove_course,
    api_generate_chapters,
    api_load_chapter_content,
    api_regenerate_syllabus,
    api_generate_syllabus,
    api_generate_next_preview,
    api_recommend_roles,
    api_get_all_roles,
    api_get_role_detail,
    api_get_chapter_snapshots,
    api_generate_snapshots,
    api_get_course_overview,
    api_get_spiritual_notes,
    api_get_mastery_progress,
)
from routes.task_routes import async_tasks, TASK_STATUS
from routes.defense_settings import (
    api_generate_defense_questions,
    api_submit_answer,
    api_get_defense_questions,
    api_get_defense_progress,
    api_complete_defense,
    api_get_certificate,
    api_issue_certificate,
    api_get_llm_config,
    api_update_llm_config,
    api_update_course_settings,
    api_get_course_settings,
    api_add_learning_event,
    api_get_learning_events,
    api_save_diary_entry,
    api_get_diary_entries,
    api_delete_diary_entry,
    api_get_summary,
    api_clear_summary,
    api_get_course_groups,
    api_get_group_chats,
    api_reset_course_progress,
    api_update_syllabus_item,
    api_add_spiritual_note,
    api_delete_spiritual_note,
)

__all__ = [
    # Course routes
    "api_list_courses",
    "api_create_course",
    "api_upload_file",
    "api_get_course",
    "api_remove_course",
    "api_generate_chapters",
    "api_load_chapter_content",
    "api_regenerate_syllabus",
    "api_generate_syllabus",
    "api_generate_next_preview",
    "api_recommend_roles",
    "api_get_all_roles",
    "api_get_role_detail",
    "api_get_chapter_snapshots",
    "api_generate_snapshots",
    "api_get_course_overview",
    "api_get_spiritual_notes",
    "api_get_mastery_progress",
    # Task routes
    "async_tasks",
    "TASK_STATUS",
    # Defense & Settings routes
    "api_generate_defense_questions",
    "api_submit_answer",
    "api_get_defense_questions",
    "api_get_defense_progress",
    "api_complete_defense",
    "api_get_certificate",
    "api_issue_certificate",
    "api_get_llm_config",
    "api_update_llm_config",
    "api_update_course_settings",
    "api_get_course_settings",
    "api_add_learning_event",
    "api_get_learning_events",
    "api_save_diary_entry",
    "api_get_diary_entries",
    "api_delete_diary_entry",
    "api_get_summary",
    "api_clear_summary",
    "api_get_course_groups",
    "api_get_group_chats",
    "api_reset_course_progress",
    "api_update_syllabus_item",
    "api_add_spiritual_note",
    "api_delete_spiritual_note",
]
