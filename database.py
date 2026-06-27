"""
问渠（Wenqu）v1.1 数据库模块
SQLite数据库初始化与操作
"""

import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from config import DB_PATH, AFFINITY_BASE, AFFINITY_UNLOCK_THRESHOLDS


def get_conn() -> sqlite3.Connection:
    """获取数据库连接（线程级）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库表结构"""
    conn = get_conn()
    try:
        conn.executescript("""
        -- 课程表
        CREATE TABLE IF NOT EXISTS courses (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_type TEXT NOT NULL,  -- file/url/text/recommendation
            source_path TEXT,
            reading_mode TEXT DEFAULT 'standard',  -- speed/standard/deep
            total_chapters INTEGER DEFAULT 0,
            current_teacher TEXT,
            current_depth TEXT DEFAULT 'standard',  -- basic/standard/deep
            current_duration INTEGER DEFAULT 30,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- 章节表
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            idx INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT,
            content_slice TEXT,
            content_full TEXT,          -- 懒加载后的完整正文
            is_loaded INTEGER DEFAULT 1, -- 0=未加载占位, 1=正文已加载
            parent_idx INTEGER DEFAULT -1, -- -1=根章节
            level INTEGER DEFAULT 0,     -- 0=卷/部, 1=章, 2=节
            sort_order TEXT DEFAULT '',  -- "1.1.2" 排序键
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- 掌握项清单表
        CREATE TABLE IF NOT EXISTS syllabus_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            chapter_index INTEGER NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'pending',  -- pending | in_progress | mastered
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- 对话会话表
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            chapter_index INTEGER NOT NULL,
            teacher_role_id TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at TIMESTAMP,
            total_rounds INTEGER DEFAULT 0,
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- 对话消息表（主对话）
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,  -- user/assistant/system
            content TEXT NOT NULL,
            state_marker TEXT,  -- SHARE/PROBE/EXPLAIN/GUIDE/EVAL/END
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        -- 划词问答表（独立存储，不混入主对话）
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            session_id TEXT,
            quoted_text TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        -- 教师情感分表（按课程隔离）
        CREATE TABLE IF NOT EXISTS teacher_affinity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            teacher_role_id TEXT NOT NULL,
            score INTEGER DEFAULT 10,
            history TEXT DEFAULT '[]',  -- JSON数组
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(course_id, teacher_role_id),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- 学习画像表
        CREATE TABLE IF NOT EXISTS course_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            strengths TEXT DEFAULT '[]',     -- JSON数组
            weaknesses TEXT DEFAULT '[]',    -- JSON数组
            misunderstandings TEXT DEFAULT '[]',  -- JSON数组
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- 学习日记表
        CREATE TABLE IF NOT EXISTS diaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        -- 群聊记录表
        CREATE TABLE IF NOT EXISTS group_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            teacher_role_id TEXT NOT NULL,
            message TEXT NOT NULL,
            quoted_user_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        -- 复习总结表
        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        -- 学习事件表（热力图数据）
        CREATE TABLE IF NOT EXISTS learning_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT,
            event_type TEXT NOT NULL,  -- login | dialogue_round | annotation_ask | lesson_end
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        -- 按年分表 + 复合索引性能优化
        CREATE INDEX IF NOT EXISTS idx_events_date_type ON learning_events(created_at, event_type);

        -- 答辩记录表
        CREATE TABLE IF NOT EXISTS defense_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            passed INTEGER DEFAULT 0,
            questions TEXT NOT NULL,  -- JSON: 题目+答案+评估+参考答案
            certificate_id INTEGER,
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- 结业证书表
        CREATE TABLE IF NOT EXISTS certificates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            user_nickname TEXT DEFAULT '自学者',
            teacher_role_id TEXT NOT NULL,
            total_minutes INTEGER,
            strengths TEXT,
            weaknesses TEXT,
            teacher_comment TEXT,
            issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- 角色滑块配置表（按课程独立保存）
        CREATE TABLE IF NOT EXISTS role_sliders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id TEXT NOT NULL,
            teacher_role_id TEXT NOT NULL,
            strictness INTEGER DEFAULT 0,
            encouragement INTEGER DEFAULT 0,
            verbosity INTEGER DEFAULT 0,
            UNIQUE(course_id, teacher_role_id),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- LLM 提供商表
        CREATE TABLE IF NOT EXISTS llm_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            base_url TEXT NOT NULL,
            api_key TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- LLM 模型表
        CREATE TABLE IF NOT EXISTS llm_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (provider_id) REFERENCES llm_providers(id) ON DELETE CASCADE,
            UNIQUE(provider_id, name)
        );
        """)
        conn.commit()
    finally:
        conn.close()

    # ===== 数据库迁移：为旧表补上新列 =====
    _migrate_schema()


def _migrate_schema():
    """增量迁移已有表，添加新字段"""
    conn = get_conn()
    try:
        # 检查 courses 表
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(courses)")}
        if "reading_mode" not in cols:
            conn.execute("ALTER TABLE courses ADD COLUMN reading_mode TEXT DEFAULT 'standard'")

        # 检查 chapters 表
        cols2 = {row["name"] for row in conn.execute("PRAGMA table_info(chapters)")}
        for col, col_def in [
            ("content_full", "TEXT DEFAULT ''"),
            ("is_loaded", "INTEGER DEFAULT 1"),
            ("parent_idx", "INTEGER DEFAULT -1"),
            ("level", "INTEGER DEFAULT 0"),
            ("sort_order", "TEXT DEFAULT ''"),
        ]:
            if col not in cols2:
                conn.execute(f"ALTER TABLE chapters ADD COLUMN {col} {col_def}")

        conn.commit()
    finally:
        conn.close()


# ==================== CRUD 操作 ====================

def create_course(title: str, source_type: str, source_path: str = "", reading_mode: str = "standard") -> str:
    """创建课程，返回课程ID"""
    course_id = str(uuid.uuid4())[:8]
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO courses (id, title, source_type, source_path, reading_mode) VALUES (?, ?, ?, ?, ?)",
            (course_id, title, source_type, source_path, reading_mode),
        )
        # 初始化学习画像
        conn.execute(
            "INSERT INTO course_profiles (course_id) VALUES (?)",
            (course_id,),
        )
        # 初始化8个角色的情感分
        role_ids = ["march7", "keqing", "ganyu", "socrates", "linmo", "yunyi", "zhiwei", "yunxiu"]
        for rid in role_ids:
            conn.execute(
                "INSERT OR IGNORE INTO teacher_affinity (course_id, teacher_role_id, score, history) VALUES (?, ?, ?, ?)",
                (course_id, rid, AFFINITY_BASE, "[]"),
            )
        conn.commit()
        return course_id
    finally:
        conn.close()


def get_course(course_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_courses() -> list:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM courses ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_course(course_id: str) -> bool:
    conn = get_conn()
    try:
        # 先删子表（有外键约束），最后删主表
        conn.execute("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE course_id=?)", (course_id,))
        conn.execute("DELETE FROM group_chats WHERE session_id IN (SELECT id FROM sessions WHERE course_id=?)", (course_id,))
        conn.execute("DELETE FROM diaries WHERE session_id IN (SELECT id FROM sessions WHERE course_id=?)", (course_id,))
        conn.execute("DELETE FROM summaries WHERE session_id IN (SELECT id FROM sessions WHERE course_id=?)", (course_id,))
        conn.execute("DELETE FROM annotations WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM sessions WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM chapters WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM syllabus_items WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM teacher_affinity WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM course_profiles WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM role_sliders WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM learning_events WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM certificates WHERE course_id=?", (course_id,))
        conn.execute("DELETE FROM courses WHERE id=?", (course_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def update_course_reading_mode(course_id: str, mode: str):
    conn = get_conn()
    try:
        conn.execute("UPDATE courses SET reading_mode=? WHERE id=?", (mode, course_id))
        conn.commit()
    finally:
        conn.close()


def update_course_depth(course_id: str, depth: str):
    conn = get_conn()
    try:
        conn.execute("UPDATE courses SET current_depth=? WHERE id=?", (depth, course_id))
        conn.commit()
    finally:
        conn.close()


def update_course_duration(course_id: str, duration: int):
    conn = get_conn()
    try:
        conn.execute("UPDATE courses SET current_duration=? WHERE id=?", (duration, course_id))
        conn.commit()
    finally:
        conn.close()


def update_course_teacher(course_id: str, teacher: str):
    conn = get_conn()
    try:
        conn.execute("UPDATE courses SET current_teacher=? WHERE id=?", (teacher, course_id))
        conn.commit()
    finally:
        conn.close()


# ==================== 章节 ====================

def add_chapter(course_id: str, idx: int, title: str, content_slice: str = "", summary: str = "",
                content_full: str = "", is_loaded: int = 1, parent_idx: int = -1,
                level: int = 0, sort_order: str = "") -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO chapters (course_id, idx, title, content_slice, summary, content_full, is_loaded, parent_idx, level, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (course_id, idx, title, content_slice, summary, content_full, is_loaded, parent_idx, level, sort_order),
        )
        conn.execute("UPDATE courses SET total_chapters = (SELECT COUNT(*) FROM chapters WHERE course_id=?) WHERE id=?", (course_id, course_id))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_chapter_content(course_id: str, chapter_idx: int, content_full: str, summary: str = ""):
    """懒加载完成时更新章节的完整内容和摘要"""
    conn = get_conn()
    try:
        if summary:
            conn.execute(
                "UPDATE chapters SET content_full=?, summary=?, is_loaded=1 WHERE course_id=? AND idx=?",
                (content_full, summary, course_id, chapter_idx),
            )
        else:
            conn.execute(
                "UPDATE chapters SET content_full=?, is_loaded=1 WHERE course_id=? AND idx=?",
                (content_full, course_id, chapter_idx),
            )
        conn.commit()
    finally:
        conn.close()


def get_chapters(course_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM chapters WHERE course_id=? ORDER BY idx", (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_chapter(course_id: str, chapter_index: int) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM chapters WHERE course_id=? AND idx=?", (course_id, chapter_index)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ==================== 掌握项 ====================

def add_syllabus_item(course_id: str, chapter_index: int, description: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO syllabus_items (course_id, chapter_index, description) VALUES (?, ?, ?)",
            (course_id, chapter_index, description),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_syllabus_items(course_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM syllabus_items WHERE course_id=? ORDER BY chapter_index, id", (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_syllabus_item(item_id: int, status: str) -> bool:
    assert status in ("pending", "in_progress", "mastered")
    conn = get_conn()
    try:
        conn.execute("UPDATE syllabus_items SET status=? WHERE id=?", (status, item_id))
        conn.commit()
        return True
    finally:
        conn.close()


def get_syllabus_progress(course_id: str) -> dict:
    """获取课程进度统计"""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM syllabus_items WHERE course_id=? GROUP BY status",
            (course_id,),
        ).fetchall()
        total = 0
        mastered = 0
        for r in rows:
            d = dict(r)
            total += d["cnt"]
            if d["status"] == "mastered":
                mastered += d["cnt"]
        return {"total": total, "mastered": mastered, "percent": round(mastered / total * 100, 1) if total > 0 else 0}
    finally:
        conn.close()


def batch_update_mastered(course_id: str, mastered_ids: list[int]):
    """批量更新掌握项为mastered"""
    conn = get_conn()
    try:
        for mid in mastered_ids:
            conn.execute("UPDATE syllabus_items SET status='mastered' WHERE id=? AND course_id=?", (mid, course_id))
        conn.commit()
    finally:
        conn.close()


def get_course_learning_stats(course_id: str) -> dict:
    """获取课程学习统计（时长、估算token）"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT total_rounds, started_at, ended_at
               FROM sessions WHERE course_id=? AND total_rounds > 0""",
            (course_id,),
        ).fetchall()
        total_minutes = 0
        total_tokens = 0
        total_rounds_sum = 0
        for r in rows:
            rounds = r["total_rounds"] or 0
            total_rounds_sum += rounds
            # 从开始结束时间计算实际分钟数
            if r["ended_at"] and r["started_at"]:
                try:
                    from datetime import datetime
                    start = datetime.strptime(r["started_at"][:19], "%Y-%m-%d %H:%M:%S")
                    end = datetime.strptime(r["ended_at"][:19], "%Y-%m-%d %H:%M:%S")
                    minutes = max(1, int((end - start).total_seconds() // 60))
                    total_minutes += minutes
                except (ValueError, IndexError):
                    total_minutes += rounds * 2  # 兜底：每轮2分钟
            else:
                # 对话未正常结束（如断线），按每轮2分钟估算
                total_minutes += rounds * 2
            # 估算token：每轮对话平均约800 tokens（输入+输出）
            total_tokens += rounds * 800
        return {
            "total_minutes": max(1, total_minutes),
            "total_rounds": total_rounds_sum,
            "total_tokens": total_tokens,
        }
    finally:
        conn.close()


# ==================== 对话会话 ====================

def create_session(course_id: str, chapter_index: int, teacher_role_id: str) -> str:
    session_id = str(uuid.uuid4())[:12]
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO sessions (id, course_id, chapter_index, teacher_role_id) VALUES (?, ?, ?, ?)",
            (session_id, course_id, chapter_index, teacher_role_id),
        )
        conn.commit()
        return session_id
    finally:
        conn.close()


def end_session(session_id: str, total_rounds: int):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE sessions SET ended_at=CURRENT_TIMESTAMP, total_rounds=? WHERE id=?",
            (total_rounds, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_session(session_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_sessions(course_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE course_id=? ORDER BY started_at DESC", (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 消息 ====================

def add_message(session_id: str, role: str, content: str, state_marker: str = "") -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO messages (session_id, role, content, state_marker) VALUES (?, ?, ?, ?)",
            (session_id, role, content, state_marker),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_messages(session_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 划词问答 ====================

def add_annotation(course_id: str, session_id: str, quoted_text: str, question: str, answer: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO annotations (course_id, session_id, quoted_text, question, answer) VALUES (?, ?, ?, ?, ?)",
            (course_id, session_id, quoted_text, question, answer),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_annotations(session_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 情感分 ====================

def get_affinity(course_id: str, teacher_role_id: str) -> int:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT score FROM teacher_affinity WHERE course_id=? AND teacher_role_id=?",
            (course_id, teacher_role_id),
        ).fetchone()
        return row["score"] if row else AFFINITY_BASE
    finally:
        conn.close()


def update_affinity(course_id: str, teacher_role_id: str, delta: int, reason: str):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT score, history FROM teacher_affinity WHERE course_id=? AND teacher_role_id=?",
            (course_id, teacher_role_id),
        ).fetchone()
        if not row:
            return
        new_score = max(0, min(100, row["score"] + delta))
        history = json.loads(row["history"] if row["history"] else "[]")
        history.append({
            "change": delta,
            "reason": reason,
            "score_after": new_score,
            "timestamp": datetime.now().isoformat(),
        })
        if len(history) > 50:
            history = history[-50:]
        conn.execute(
            "UPDATE teacher_affinity SET score=?, history=? WHERE course_id=? AND teacher_role_id=?",
            (new_score, json.dumps(history, ensure_ascii=False), course_id, teacher_role_id),
        )
        conn.commit()
        # 检测彩蛋解锁
        for threshold in AFFINITY_UNLOCK_THRESHOLDS:
            if row["score"] < threshold <= new_score:
                _record_affinity_event(course_id, teacher_role_id, threshold)
    finally:
        conn.close()


def _record_affinity_event(course_id: str, teacher_role_id: str, threshold: int):
    """记录情感分达到阈值的彩蛋事件"""
    add_learning_event(course_id, f"affinity_unlock_{threshold}", {"teacher": teacher_role_id})


def get_all_affinities(course_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT teacher_role_id, score FROM teacher_affinity WHERE course_id=? ORDER BY score DESC",
            (course_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 学习画像 ====================

def get_profile(course_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM course_profiles WHERE course_id=?", (course_id,)
        ).fetchone()
        if row:
            d = dict(row)
            d["strengths"] = json.loads(d.get("strengths", "[]"))
            d["weaknesses"] = json.loads(d.get("weaknesses", "[]"))
            d["misunderstandings"] = json.loads(d.get("misunderstandings", "[]"))
            return d
        return None
    finally:
        conn.close()


def update_profile(course_id: str, field: str, items: list):
    """更新画像中的强项/弱项/误解，保留最近10条"""
    assert field in ("strengths", "weaknesses", "misunderstandings")
    conn = get_conn()
    try:
        existing = get_profile(course_id)
        if existing:
            current = existing.get(field, [])
        else:
            current = []
        # 合并新项，去重，保留最近10条
        seen = set()
        merged = items + current
        deduped = []
        for item in merged:
            key = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        kept = deduped[:10]
        conn.execute(
            f"UPDATE course_profiles SET {field}=?, updated_at=CURRENT_TIMESTAMP WHERE course_id=?",
            (json.dumps(kept, ensure_ascii=False), course_id),
        )
        conn.commit()
    finally:
        conn.close()


# ==================== 学习日记 ====================

def add_diary(course_id: str, session_id: str, title: str, content: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO diaries (course_id, session_id, title, content) VALUES (?, ?, ?, ?)",
            (course_id, session_id, title, content),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_diaries(course_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM diaries WHERE course_id=? ORDER BY created_at DESC", (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 群聊 ====================

def add_group_chat(course_id: str, session_id: str, teacher_role_id: str, message: str, quoted_text: str = ""):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO group_chats (course_id, session_id, teacher_role_id, message, quoted_user_text) VALUES (?, ?, ?, ?, ?)",
            (course_id, session_id, teacher_role_id, message, quoted_text),
        )
        conn.commit()
    finally:
        conn.close()


def get_group_chats(session_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM group_chats WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 复习总结 ====================

def add_summary(course_id: str, session_id: str, content: str) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO summaries (course_id, session_id, content) VALUES (?, ?, ?)",
            (course_id, session_id, content),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_summaries(course_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM summaries WHERE course_id=? ORDER BY created_at DESC", (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 学习事件=热力图 ====================

def add_learning_event(course_id: str, event_type: str, metadata: dict = None):
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO learning_events (course_id, event_type, metadata) VALUES (?, ?, ?)",
            (course_id, event_type, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def get_heatmap_data(year: int) -> list:
    """获取指定年份的热力图数据：每天的事件总数"""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT DATE(created_at) as day, COUNT(*) as count
               FROM learning_events
               WHERE strftime('%Y', created_at) = ?
               GROUP BY DATE(created_at)
               ORDER BY day""",
            (str(year),),
        ).fetchall()
        return [{"date": r["day"], "count": r["count"]} for r in rows]
    finally:
        conn.close()


def get_daily_summary(date_str: str) -> dict:
    """获取某天的学习摘要"""
    conn = get_conn()
    try:
        events = conn.execute(
            """SELECT event_type, COUNT(*) as cnt
               FROM learning_events
               WHERE DATE(created_at) = ?
               GROUP BY event_type""",
            (date_str,),
        ).fetchall()
        courses = conn.execute(
            """SELECT DISTINCT c.id, c.title
               FROM learning_events e JOIN courses c ON e.course_id = c.id
               WHERE DATE(e.created_at) = ?""",
            (date_str,),
        ).fetchall()
        return {
            "events": {r["event_type"]: r["cnt"] for r in events},
            "courses": [dict(r) for r in courses],
            "total": sum(r["cnt"] for r in events),
        }
    finally:
        conn.close()


# ==================== 答辩记录 ====================

def add_defense_record(course_id: str, passed: bool, questions: list, certificate_id: int = None) -> int:
    """保存答辩记录"""
    import json
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO defense_records (course_id, passed, questions, certificate_id) VALUES (?, ?, ?, ?)",
            (course_id, 1 if passed else 0, json.dumps(questions, ensure_ascii=False), certificate_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_defense_records(course_id: str) -> list:
    """获取课程的所有答辩记录"""
    import json
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM defense_records WHERE course_id=? ORDER BY created_at DESC", (course_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["questions"] = json.loads(d.get("questions", "[]"))
            d["passed"] = bool(d["passed"])
            result.append(d)
        return result
    finally:
        conn.close()


# ==================== 结业证书 ====================

def add_certificate(course_id: str, teacher_role_id: str, total_minutes: int,
                    strengths: str, weaknesses: str, teacher_comment: str,
                    nickname: str = "自学者") -> int:
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO certificates
               (course_id, user_nickname, teacher_role_id, total_minutes, strengths, weaknesses, teacher_comment)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (course_id, nickname, teacher_role_id, total_minutes, strengths, weaknesses, teacher_comment),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_certificates(course_id: str) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM certificates WHERE course_id=? ORDER BY issued_at DESC", (course_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==================== 角色滑块 ====================

def get_sliders(course_id: str, teacher_role_id: str) -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT strictness, encouragement, verbosity FROM role_sliders WHERE course_id=? AND teacher_role_id=?",
            (course_id, teacher_role_id),
        ).fetchone()
        if row:
            return dict(row)
        return {"strictness": 0, "encouragement": 0, "verbosity": 0}
    finally:
        conn.close()


def save_sliders(course_id: str, teacher_role_id: str, strictness: int, encouragement: int, verbosity: int):
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO role_sliders (course_id, teacher_role_id, strictness, encouragement, verbosity)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(course_id, teacher_role_id)
               DO UPDATE SET strictness=excluded.strictness, encouragement=excluded.encouragement, verbosity=excluded.verbosity""",
            (course_id, teacher_role_id, strictness, encouragement, verbosity),
        )
        conn.commit()
    finally:
        conn.close()


# ==================== LLM 提供商管理 ====================

def add_llm_provider(name: str, base_url: str, api_key: str = "") -> int:
    conn = get_conn()
    try:
        # 如果表为空，自动设置为激活
        existing = conn.execute("SELECT COUNT(*) as cnt FROM llm_providers").fetchone()
        is_active = 1 if existing["cnt"] == 0 else 0
        cur = conn.execute(
            "INSERT INTO llm_providers (name, base_url, api_key, is_active) VALUES (?, ?, ?, ?)",
            (name, base_url, api_key, is_active),
        )
        provider_id = cur.lastrowid
        conn.commit()
        return provider_id
    finally:
        conn.close()


def get_all_llm_providers() -> list:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT * FROM llm_providers ORDER BY created_at ASC").fetchall()
        providers = [dict(r) for r in rows]
        for p in providers:
            p["models"] = get_llm_models(p["id"])
        return providers
    finally:
        conn.close()


def get_llm_provider(provider_id: int) -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM llm_providers WHERE id=?", (provider_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_llm_provider(provider_id: int, **kwargs) -> bool:
    allowed = {"name", "base_url", "api_key"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return False
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [provider_id]
    conn = get_conn()
    try:
        conn.execute(f"UPDATE llm_providers SET {set_clause} WHERE id=?", values)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_llm_provider(provider_id: int) -> bool:
    conn = get_conn()
    try:
        # 手动级联删除 models（SQLite 不一定自动触发）
        conn.execute("DELETE FROM llm_models WHERE provider_id=?", (provider_id,))
        conn.execute("DELETE FROM llm_providers WHERE id=?", (provider_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def set_active_provider(provider_id: int):
    conn = get_conn()
    try:
        conn.execute("UPDATE llm_providers SET is_active=0")
        conn.execute("UPDATE llm_providers SET is_active=1 WHERE id=?", (provider_id,))
        conn.commit()
    finally:
        conn.close()


def get_active_provider() -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM llm_providers WHERE is_active=1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_llm_model(provider_id: int, name: str) -> int:
    conn = get_conn()
    try:
        # 如果该 provider 下无 model，自动激活
        existing = conn.execute(
            "SELECT COUNT(*) as cnt FROM llm_models WHERE provider_id=?", (provider_id,)
        ).fetchone()
        is_active = 1 if existing["cnt"] == 0 else 0
        cur = conn.execute(
            "INSERT INTO llm_models (provider_id, name, is_active) VALUES (?, ?, ?)",
            (provider_id, name, is_active),
        )
        model_id = cur.lastrowid
        conn.commit()
        return model_id
    finally:
        conn.close()


def get_llm_models(provider_id: int) -> list:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM llm_models WHERE provider_id=? ORDER BY created_at ASC",
            (provider_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_llm_model(model_id: int) -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM llm_models WHERE id=?", (model_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_llm_model(model_id: int) -> bool:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM llm_models WHERE id=?", (model_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def update_llm_model_rename(model_id: int, name: str) -> bool:
    conn = get_conn()
    try:
        conn.execute("UPDATE llm_models SET name=? WHERE id=?", (name, model_id))
        conn.commit()
        return True
    finally:
        conn.close()


def set_active_model(model_id: int):
    conn = get_conn()
    try:
        conn.execute("UPDATE llm_models SET is_active=0")
        conn.execute("UPDATE llm_models SET is_active=1 WHERE id=?", (model_id,))
        conn.commit()
    finally:
        conn.close()


def get_active_model() -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM llm_models WHERE is_active=1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_active_model_with_provider() -> dict:
    """JOIN 查询获取当前激活的 provider + model 组合"""
    conn = get_conn()
    try:
        row = conn.execute("""
            SELECT p.id as provider_id, p.name as provider_name, p.base_url, p.api_key,
                   m.id as model_id, m.name as model_name
            FROM llm_providers p
            JOIN llm_models m ON m.provider_id = p.id AND m.is_active = 1
            WHERE p.is_active = 1
        """).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
