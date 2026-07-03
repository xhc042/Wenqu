"""
问渠（Wenqu）v1.1 对话状态机模块

严格状态流转：INIT → SHARE → PROBE → WAIT_USER → EVAL → ACTION → (循环至PROBE) / END
严禁LLM自由决定对话流程
"""

import json
import re
import asyncio
from datetime import datetime
from typing import Optional, AsyncGenerator, List

from llm_client import llm, build_system_prompt
from config import FLOW_DETECTION, DEPTH_CONFIG, READING_MODE_TO_DEPTH
import database as db

# v1.5 优化: 预编译正则表达式，避免每次调用 strip_thinking_tags 时重复编译
_THINKING_TAG_PATTERN = re.compile(r'<think>[\s\S]*?</think>', re.IGNORECASE)
_STANDARD_THINKING_PATTERN = re.compile(r'<thinking>[\s\S]*?</thinking>', re.IGNORECASE)
_NEWLINE_PATTERN = re.compile(r'\n{3,}')


def strip_thinking_tags(content: str) -> str:
    """移除思考标签，兼容带思考模式的模型（如o1、Claude等）

    v1.5 优化: 使用预编译的正则表达式，减少重复编译开销
    """
    if not content:
        return content
    # 移除 <think>...</think> 标签及其内容
    content = _THINKING_TAG_PATTERN.sub('', content)
    # 移除 <thinking>...</thinking> 标签及其内容
    content = _STANDARD_THINKING_PATTERN.sub('', content)
    # 清理多余空白
    content = _NEWLINE_PATTERN.sub('\n\n', content)
    return content.strip()


async def noop():
    """空操作占位函数（用于 asyncio.gather 条件分支）"""
    return None


class DialogueStateMachine:
    """
    苏格拉底式对话状态机
    每个会话一个实例，状态由后端代码控制，LLM仅负责生成各状态对应的文本
    """

    # 状态常量
    INIT = "INIT"
    SHARE = "SHARE"
    PROBE = "PROBE"
    WAIT_USER = "WAIT_USER"
    EVAL = "EVAL"
    EXPLAIN = "EXPLAIN"
    GUIDE = "GUIDE"
    ACTION = "ACTION"
    DEBATE = "DEBATE"
    END = "END"

    def __init__(
        self,
        session_id: str,
        course_id: str,
        chapter_index: int,
        teacher_role_id: str,
        depth: str = "standard",
        sliders: dict = None,
        duration_minutes: int = 30,
    ):
        self.session_id = session_id
        self.course_id = course_id
        self.chapter_index = chapter_index
        self.teacher_role_id = teacher_role_id
        self.depth = depth
        self.sliders = sliders or {}
        self.duration_minutes = duration_minutes

        # 运行时状态
        self.state = self.INIT
        self.current_round = 0
        self.total_rounds = 0
        self.is_flow_state = False
        self.flow_extend_count = 0
        self.consecutive_stuck = 0
        self.consecutive_thinking = 0
        # v1.3 P1-任务7: 强制核心 probe 标记,确保每章节最多触发一次,防止无限循环
        self._core_probe_attempted = False
        self.started_at = None

        # 对话上下文
        self.messages: List[dict] = []  # 用于LLM调用的消息列表
        self.course_info = {}
        self.chapter_info = {}
        self.previous_summaries = []
        self.syllabus_items = []

        # 会话中已掌握的项
        self.session_mastered_ids = set()

        # 群聊参与的角色列表（当前课程已有的教师）
        self.all_teachers = ["march7", "keqing", "ganyu", "socrates", "linmo", "yunyi", "zhiwei", "yunxiu"]

    async def load_context(self):
        """加载课程和章节上下文"""
        self.course_info = db.get_course(self.course_id) or {}
        self.chapter_info = db.get_chapter(self.course_id, self.chapter_index) or {}
        self.syllabus_items = db.get_syllabus_items(self.course_id)

        # P2-⑤: depth 与 reading_mode 一致性断言
        # 通过 READING_MODE_TO_DEPTH 反推 expected_depth，与 self.depth 对比
        reading_mode = self.course_info.get("reading_mode")
        if reading_mode:
            expected_depth = READING_MODE_TO_DEPTH.get(reading_mode)
            if expected_depth and self.depth != expected_depth:
                import logging
                logging.warning(
                    f"[一致性] depth={self.depth} 与 reading_mode={reading_mode} 不匹配, "
                    f"应为 {expected_depth} (course_id={self.course_id})"
                )

        # 获取前序章节摘要
        chapters = db.get_chapters(self.course_id)
        for ch in chapters:
            if ch["idx"] < self.chapter_index and ch.get("summary"):
                self.previous_summaries.append(ch["summary"])

        # 获取角色滑块
        sliders_db = db.get_sliders(self.course_id, self.teacher_role_id)
        if not self.sliders:
            self.sliders = sliders_db

    def _get_min_rounds(self) -> int:
        """根据学习时长计算最小对话轮次

        映射规则：
        - 15min → 3 轮（快速浏览）
        - 30min → 6 轮（标准学习）
        - 60min → 12 轮（深度学习）
        - 120min → 24 轮（沉浸式学习）
        - 其他 → 6 轮（默认，与旧实现一致以避免行为变更）
        """
        mapping = {15: 3, 30: 6, 60: 12, 120: 24}
        return mapping.get(self.duration_minutes, 6)

    async def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        course_title = self.course_info.get("title", "未知课程")
        chapter_title = self.chapter_info.get("title", f"第{self.chapter_index + 1}章")
        prev_summaries = [s for s in self.previous_summaries]

        return build_system_prompt(
            role_id=self.teacher_role_id,
            depth=self.depth,
            sliders=self.sliders,
            course_title=course_title,
            chapter_title=chapter_title,
            previous_summaries=prev_summaries,
        )

    async def initialize(self) -> AsyncGenerator[str, None]:
        """INIT状态：初始化对话上下文"""
        self.started_at = datetime.now()
        system_prompt = await self._build_system_prompt()

        # P2-①: speed 模式用 snapshot + global_highlights 构造上下文（避免依赖章节原文）
        reading_mode = self.course_info.get("reading_mode", "standard")
        if reading_mode == "speed":
            chapter_content = self._build_speed_chapter_context()
        else:
            # 获取章节内容
            chapter_content = self.chapter_info.get("content_slice", "")

        # 构建初始消息
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"我们开始学习本章内容。教材内容如下：\n\n{chapter_content[:4000]}"},
        ]

        # 保存系统消息
        db.add_message(self.session_id, "system", system_prompt[:200], self.INIT)

        # 记录学习事件
        db.add_learning_event(self.course_id, "dialogue_round", {
            "chapter": self.chapter_index,
            "teacher": self.teacher_role_id,
        })

        self.state = self.SHARE
        # 进入SHARE状态
        async for chunk in self._share():
            yield chunk

    def _build_speed_chapter_context(self) -> str:
        """P2-①: speed 模式用 snapshot + global_highlights 构造上下文

        章节原文（content_slice ≤ 600 字）只覆盖头尾，看不到中段。
        用 snapshot 替代：包含 core_viewpoint / keywords / learning_goal，
        再叠加全书精华 key_points，让对话不依赖章节原文。
        """
        parts: List[str] = []

        # 1. 本章快照
        snapshot = db.get_chapter_snapshot(self.course_id, self.chapter_index)
        if snapshot:
            parts.append("## 本章速览")
            if snapshot.get("core_viewpoint"):
                parts.append(f"**核心观点**：{snapshot['core_viewpoint']}")
            if snapshot.get("keywords"):
                # keywords 在 DB 里是 JSON 字符串
                try:
                    import json as _json
                    kws = _json.loads(snapshot["keywords"]) if isinstance(snapshot["keywords"], str) else snapshot["keywords"]
                except Exception:
                    kws = []
                if kws:
                    parts.append(f"**关键词**：{', '.join(str(k) for k in kws)}")
            if snapshot.get("learning_goal"):
                parts.append(f"**学习目标**：{snapshot['learning_goal']}")
            if snapshot.get("difficulty"):
                parts.append(f"**难度**：{snapshot['difficulty']}")

        # 2. 全书精华中的相关项
        highlights = db.get_global_highlights(self.course_id) or {}
        key_points = highlights.get("key_points", []) if isinstance(highlights, dict) else []
        if key_points:
            parts.append("\n## 全书精华")
            parts.extend(f"- {kp}" for kp in key_points[:10])

        if not parts:
            # 兜底：快照缺失时退回原文
            return self.chapter_info.get("content_slice", "")

        return "\n".join(parts)

    async def _share(self) -> AsyncGenerator[str, None]:
        """SHARE状态：AI分享教材片段"""
        self.state = self.SHARE

        # 获取本章未掌握的项，用于注入提示
        mastery_hint = self._get_mastery_hint()
        # v1.3 P0-⑥: SHARE 阶段强化 — 必须先揭示核心观点,不从基础定义开始
        share_prompt = f"""请用自己的话复述这段教材的核心观点。

要求：
1. 必须先揭示本章最核心的观点（[重要度4-5] 的项），不要从基础概念定义开始
2. 用具体例子/案例/数据支撑核心观点
3. 让用户听完就知道"这一章最值得记住的是什么"

{mastery_hint}
以'书上有个很有趣的观点...'或'这段让我联想到...'开头。"""

        self.messages.append({
            "role": "user",
            "content": share_prompt,
        })

        content_parts = []
        async for chunk in llm.chat_stream(self.messages, temperature=0.7):
            content_parts.append(chunk)
            yield chunk

        full_content = "".join(content_parts)
        self.messages.append({"role": "assistant", "content": full_content})
        db.add_message(self.session_id, "assistant", full_content, self.SHARE)

        # 进入PROBE
        self.state = self.PROBE

    async def _probe(self) -> AsyncGenerator[str, None]:
        """PROBE状态：AI提出1个问题"""
        self.state = self.PROBE

        # 从学习画像中获取薄弱点
        profile = db.get_profile(self.course_id)
        weaknesses = profile.get("weaknesses", []) if profile else []

        weak_prompt = ""
        if weaknesses:
            weak_prompt = f"用户当前的薄弱点包括：{'、'.join(weaknesses[:3])}。请针对薄弱点提问。"

        # 获取掌握项检查清单
        mastery_check = self._get_mastery_check()

        self.messages.append({
            "role": "user",
            "content": f"请针对这段教材内容提出1个开放性问题，帮助用户深入思考。{weak_prompt}\n{mastery_check}\n只能提1个问题，不要多问。",
        })

        # P2-③: PROBE temperature 从 0.7 降到 0.5，与 EXPLAIN/EVAL 一致
        content_parts = []
        async for chunk in llm.chat_stream(self.messages, temperature=0.5):
            content_parts.append(chunk)
            yield chunk

        full_content = "".join(content_parts)
        self.messages.append({"role": "assistant", "content": full_content})
        db.add_message(self.session_id, "assistant", full_content, self.PROBE)

        # 进入WAIT_USER
        self.state = self.WAIT_USER

    def _get_mastery_hint(self) -> str:
        """获取掌握项提示（用于SHARE阶段）

        v1.3 P0-④: 按 importance desc 排序,优先揭示本章最重要的核心知识,
        并在文本中标注 [重要度N/5],让 LLM 优先讲核心
        """
        chapter_pending = sorted(
            [s for s in self.syllabus_items
             if s["chapter_index"] == self.chapter_index
             and s["status"] == "pending"],
            key=lambda x: x.get("importance", 0),
            reverse=True,
        )
        if chapter_pending:
            hint_items = [
                f"- [重要度{item.get('importance', 0)}/5] {item['description']}"
                for item in chapter_pending[:3]
            ]
            return f"本章最重要的能力点（按重要度排序）：\n{chr(10).join(hint_items)}\n"
        return ""

    def _get_mastery_check(self) -> str:
        """获取掌握项检查清单（用于PROBE阶段）

        v1.3 P0-④: 按 importance desc 排序,PROBE 优先问核心,
        并显式禁止停留在基础概念定义
        """
        chapter_items = sorted(
            [s for s in self.syllabus_items if s["chapter_index"] == self.chapter_index],
            key=lambda x: x.get("importance", 0),
            reverse=True,
        )
        if chapter_items:
            pending_items = [
                f"- [重要度{s.get('importance', 0)}/5] {s['description']}"
                for s in chapter_items if s["status"] == "pending"
            ]
            if pending_items:
                return f"""请严格按重要度提问（v1.3 增强）：
1. 必须先围绕高重要度的知识点（[重要度4-5]）提问
2. 不要停留在基础概念定义（如"什么是X"）
3. 优先问"应用/对比/判断"类问题（如"如果...会怎样"、"X和Y的区别"、"你会怎么做"）

{chr(10).join(pending_items[:3])}"""
        return ""

    def _check_knowledge_coverage(self) -> dict:
        """
        检查当前章节的知识点覆盖情况
        返回：哪些知识点已掌握、哪些还需要强化
        """
        chapter_items = [s for s in self.syllabus_items
                        if s["chapter_index"] == self.chapter_index]

        coverage = {
            "total": len(chapter_items),
            "mastered": sum(1 for s in chapter_items if s["status"] == "mastered"),
            "in_progress": sum(1 for s in chapter_items if s["status"] == "in_progress"),
            "pending": sum(1 for s in chapter_items if s["status"] == "pending"),
        }

        # 如果本章还有pending项，提示用户继续学习
        if coverage["pending"] > 0 and coverage["mastered"] > 0:
            coverage["partial"] = True
            coverage["suggestion"] = f"本章还有{coverage['pending']}个知识点未掌握"

        return coverage

    def _auto_mark_syllabus(self):
        """自动标记掌握项（SY-03规则 + 兜底）"""
        # 刷新最新状态
        self.syllabus_items = db.get_syllabus_items(self.course_id)

        # 当前章节的 pending 项
        chapter_pending = [s for s in self.syllabus_items
                          if s["chapter_index"] == self.chapter_index
                          and s["status"] == "pending"
                          and s["id"] not in self.session_mastered_ids]

        # 当前章节的 in_progress 项（来自数据库）
        conn = db.get_conn()
        try:
            chapter_in_progress_rows = conn.execute(
                "SELECT id FROM syllabus_items WHERE course_id=? AND chapter_index=? AND status='in_progress'",
                (self.course_id, self.chapter_index)
            ).fetchall()
        finally:
            conn.close()
        chapter_in_progress = [r["id"] for r in chapter_in_progress_rows]

        # 规则3: 连续thinking→升级in_progress→mastered
        if self.consecutive_thinking >= 2 and chapter_in_progress:
            target_id = chapter_in_progress[0]
            if target_id not in self.session_mastered_ids:
                db.update_syllabus_item(target_id, "mastered")
                self.session_mastered_ids.add(target_id)
                self.consecutive_thinking = 0

        # 规则2: 轮次>=3且thinking→pending→in_progress
        if self.current_round >= 3 and chapter_pending:
            target = chapter_pending[0]
            db.update_syllabus_item(target["id"], "in_progress")
            self.session_mastered_ids.add(target["id"])

        # 兜底: 只要对话超过3轮就标记1条pending→in_progress
        if self.current_round >= 3 and chapter_pending:
            # 检查是否已经有 in_progress 的项
            chapter_in_progress_after = db.get_syllabus_items(self.course_id)
            has_in_progress = any(
                s["chapter_index"] == self.chapter_index and s["status"] == "in_progress"
                for s in chapter_in_progress_after
            )
            if not has_in_progress:
                # 刷新pending列表
                fresh_pending = [s for s in chapter_in_progress_after
                               if s["chapter_index"] == self.chapter_index
                               and s["status"] == "pending"]
                if fresh_pending:
                    db.update_syllabus_item(fresh_pending[0]["id"], "in_progress")
                    self.session_mastered_ids.add(fresh_pending[0]["id"])

        # 再次刷新缓存
        self.syllabus_items = db.get_syllabus_items(self.course_id)

    async def handle_user_input(self, user_text: str) -> AsyncGenerator[str, None]:
        """处理用户输入，运行EVAL → ACTION循环"""
        self.current_round += 1
        self.total_rounds += 1

        # 保存用户消息
        self.messages.append({"role": "user", "content": user_text})
        db.add_message(self.session_id, "user", user_text, "USER_INPUT")

        # 记录对话轮次事件
        db.add_learning_event(self.course_id, "dialogue_round", {
            "chapter": self.chapter_index,
            "round": self.current_round,
        })

        # 心流检测
        self._check_flow_state(user_text)

        # EVAL：评估用户回答（带异常保护）
        try:
            eval_result = await self._eval(user_text)
            status = eval_result.get("status", "thinking")
            # 规则1: LLM评估的 mastered_items 自动标记
            mastered_ids = eval_result.get("mastered_items", [])
            if mastered_ids:
                for mid in mastered_ids:
                    if mid not in self.session_mastered_ids and len(self.session_mastered_ids) < 2:
                        self.session_mastered_ids.add(mid)
                        db.update_syllabus_item(mid, "mastered")
        except Exception:
            # EVAL失败时使用默认值，对话继续
            status = "thinking"
            mastered_ids = []

        # 跟踪thinking状态
        if status == "thinking":
            self.consecutive_thinking += 1
        else:
            self.consecutive_thinking = 0

        # 执行SY-03规则 + 兜底
        self._auto_mark_syllabus()

        # ACTION：根据评估结果分支
        if status == "thinking":
            self.consecutive_stuck = 0
            # 研读模式：检测是否触发辩论
            if self.depth == "deep" and await self._check_debate_trigger(user_text):
                async for chunk in self._start_debate():
                    yield chunk
            else:
                async for chunk in self._probe():
                    yield chunk
        elif status == "stuck":
            self.consecutive_stuck += 1
            if self.consecutive_stuck >= 3:
                async for chunk in self._end_session("连续卡壳3次"):
                    yield chunk
            else:
                async for chunk in self._explain():
                    yield chunk
        elif status == "off_track":
            async for chunk in self._guide():
                yield chunk

        # 检测是否应该结束
        # v1.3 P1-任务7: 在结束前加一道核心触达保险
        # 抽到 end_of_session_check(),handle_user_input 和 record_quick_master 共用,
        # 防止 quick_mastered 路径绕过核心触达保护。
        async for chunk in self.end_of_session_check():
            yield chunk

    async def end_of_session_check(self) -> AsyncGenerator[str, None]:
        """v1.3 P1-任务7: 结束前核心触达保险。

        触发条件:`not is_flow_state` 且 `current_round >= min_rounds`。
        - 若还没碰过 importance≥4 知识点,强制 PROBE 一次(只触发一次,防止无限循环)
        - 强制 probe 后还是没碰核心 → 正常结束(避免死循环)
        - 已碰核心 → 正常结束

        handle_user_input 和 record_quick_master 都应在 _probe() 之后调用此方法,
        保证 quick_mastered 路径不会绕过核心触达保护。
        """
        if not self.is_flow_state and self.current_round >= self._get_min_rounds():
            if not self._has_touched_core() and not self._core_probe_attempted:
                # 还没碰核心 → 强制 PROBE
                self._core_probe_attempted = True
                force_prompt = self._force_core_probe()
                if force_prompt:
                    self.messages.append({"role": "user", "content": force_prompt})
                    async for chunk in self._probe():
                        yield chunk
                    # 强制 probe 完一轮后再判断
                    if not self._has_touched_core():
                        # 还是没触达 → 正常结束(不再强制,避免死循环)
                        async for chunk in self._end_session("达到最小时长（核心未触达）"):
                            yield chunk
                else:
                    # 没 pending 项可问 → 正常结束
                    async for chunk in self._end_session("达到最小时长"):
                        yield chunk
            else:
                async for chunk in self._end_session("达到最小时长"):
                    yield chunk

    def record_quick_master(self, user_text: str) -> Optional[str]:
        """处理"我已掌握"按钮:记录用户消息 + 标记首个 pending syllabus 为 mastered。

        与 handle_user_input 的区别:跳过 LLM EVAL(用户已显式表明掌握),
        但保留:round 计数、消息入库、心流检测、syllabus 缓存刷新。

        不会自动触发 _probe()——调用方按节奏来,以便插入 TURN_DONE / PROBE 等不同 WS 消息。
        调用方应在 _probe() 之后调用 end_of_session_check(),确保核心触达保护不被绕过。

        返回被标记的 syllabus_id(供 app 层发 MASTERED_SKIPPED 事件),None 表示无 pending 可标记。
        """
        # 1. 轮次+1 + 消息入库 + 事件记录(与 handle_user_input 入口一致)
        self.current_round += 1
        self.total_rounds += 1
        self.messages.append({"role": "user", "content": user_text})
        db.add_message(self.session_id, "user", user_text, "USER_INPUT")
        db.add_learning_event(self.course_id, "dialogue_round", {
            "chapter": self.chapter_index,
            "round": self.current_round,
        })

        # 2. 心流检测
        self._check_flow_state(user_text)

        # 3. 标记首个 pending 项为 mastered
        chapter_pending = [
            s for s in self.syllabus_items
            if s["chapter_index"] == self.chapter_index
            and s["status"] == "pending"
        ]
        if not chapter_pending or chapter_pending[0]["id"] in self.session_mastered_ids:
            # 无 pending 或首条已在 session_mastered_ids,刷新缓存后返回 None
            self.syllabus_items = db.get_syllabus_items(self.course_id)
            return None

        mastered_id = chapter_pending[0]["id"]
        db.update_syllabus_item(mastered_id, "mastered")
        self.session_mastered_ids.add(mastered_id)

        # 4. 刷新缓存,后续 _probe() 会看到最新状态
        self.syllabus_items = db.get_syllabus_items(self.course_id)
        return mastered_id

    def _has_touched_core(self) -> bool:
        """v1.3 P1-任务7: 检查本章节是否已触达 importance ≥4 的 syllabus

        触达定义：状态变为 in_progress 或 mastered
        用于在 _get_min_rounds 边界强制 PROBE 一次,确保用户没白上这节课
        """
        chapter_items = [s for s in self.syllabus_items
                        if s["chapter_index"] == self.chapter_index]
        return any(
            s.get("importance", 0) >= 4
            and s["status"] in ("in_progress", "mastered")
            for s in chapter_items
        )

    def _force_core_probe(self) -> str:
        """v1.3 P1-任务7: 构造强制 PROBE 的 prompt,要求 LLM 必须问核心知识点"""
        # 取本章最高 importance 的 pending 项
        chapter_pending = sorted(
            [s for s in self.syllabus_items
             if s["chapter_index"] == self.chapter_index
             and s["status"] == "pending"],
            key=lambda x: x.get("importance", 0),
            reverse=True,
        )
        if not chapter_pending:
            return ""
        top = chapter_pending[0]
        return (
            f"⚠️ 重要提示：本节课即将结束，但你还没学过本章最重要的知识点。"
            f"请围绕以下核心知识点提问（必须问这一条，不能换其他）：\n"
            f"- [重要度{top.get('importance', 0)}/5] {top['description']}\n"
            f"问题必须聚焦该核心知识点，不要停留在基础概念。"
        )

    def _check_flow_state(self, user_text: str):
        """心流检测

        P3-③: 从单条消息判断改为综合判断
        - 当前消息有信号 → 直接进心流
        - 或最近 3 条用户消息平均长度 > 80 且问句密度 > 30% → 进心流
        """
        if self.current_round < FLOW_DETECTION["min_rounds"]:
            return

        # 当前消息判断
        current_signals = (
            len(user_text) > FLOW_DETECTION["reply_length_threshold"] and
            any(q in user_text for q in ["?", "为什么", "如何", "是不是"])
        )

        # 最近 3 条用户消息综合判断
        recent_user_msgs = [
            m for m in self.messages[-10:]
            if m.get("role") == "user"
        ][-3:]
        n = max(len(recent_user_msgs), 1)
        avg_len = sum(len(m.get("content", "")) for m in recent_user_msgs) / n
        question_density = sum(
            1 for m in recent_user_msgs
            if any(q in m.get("content", "") for q in ["?", "为什么", "如何"])
        ) / n

        if current_signals or (avg_len > 80 and question_density > 0.3):
            self.is_flow_state = True
            self.flow_extend_count += FLOW_DETECTION["auto_extend_rounds"]
        else:
            self.is_flow_state = False

    async def _eval(self, user_text: str) -> dict:
        """EVAL状态：评估用户回答"""
        self.state = self.EVAL

        # 获取当前章节的掌握项列表
        chapter_items = [s for s in self.syllabus_items if s["chapter_index"] == self.chapter_index]
        syllabus_list = "\n".join([
            f"- ID {s['id']}: {s['description']} (当前状态: {s['status']})"
            for s in chapter_items
        ])

        # 构建评估消息，传入 syllabus_items 列表
        eval_messages = [
            {"role": "system", "content": f"""你是一个教学评估助手。评估学生的回答，并输出JSON。

评估标准：
- "thinking"：学生在认真思考，回答有逻辑、有深度
- "stuck"：学生卡住了，回答不完整或明显不知道
- "off_track"：学生偏离了主题或理解有误

当前章节的掌握项清单：
{syllabus_list}

请判断学生本次回答是否掌握了其中某些项。如果学生已经理解并能正确解释某条掌握项所描述的内容，将该条目的ID加入mastered_items数组。
谨慎判断——只有当学生确实展现了对该知识点的掌握时才标记，不要猜测。

只输出JSON，格式：
{{"status": "thinking|stuck|off_track", "mastered_items": [1, 3]}}"""},
            {"role": "user", "content": f"学生的回答是：{user_text}"},
        ]

        result = await llm.chat_json(eval_messages, temperature=0.3)

        status = result.get("status", "thinking")
        mastered_items = result.get("mastered_items", [])

        db.add_message(self.session_id, "system",
                       json.dumps({"status": status, "mastered_items": mastered_items}, ensure_ascii=False),
                       self.EVAL)

        return result

    async def _explain(self) -> AsyncGenerator[str, None]:
        """EXPLAIN模式：讲解"""
        self.state = self.EXPLAIN

        depth_cfg = DEPTH_CONFIG.get(self.depth, DEPTH_CONFIG["standard"])
        detail_instruction = depth_cfg["explain_detail"]

        self.messages.append({
            "role": "user",
            "content": f"学生卡住了。请用通俗易懂的方式讲解这个知识点。{detail_instruction}不要直接给答案，而是通过引导帮助理解。",
        })

        content_parts = []
        async for chunk in llm.chat_stream(self.messages, temperature=0.5):
            content_parts.append(chunk)
            yield chunk

        full_content = "".join(content_parts)
        self.messages.append({"role": "assistant", "content": full_content})
        db.add_message(self.session_id, "assistant", full_content, self.EXPLAIN)

        # 讲解后回到PROBE
        async for chunk in self._probe():
            yield chunk

    async def _guide(self) -> AsyncGenerator[str, None]:
        """GUIDE模式：温柔拉回"""
        self.state = self.GUIDE

        self.messages.append({
            "role": "user",
            "content": "学生有点偏离主题了。请温柔地把他拉回正轨，先肯定他的思考，然后重新聚焦到当前章节的核心概念上。",
        })

        content_parts = []
        async for chunk in llm.chat_stream(self.messages, temperature=0.5):
            content_parts.append(chunk)
            yield chunk

        full_content = "".join(content_parts)
        self.messages.append({"role": "assistant", "content": full_content})
        db.add_message(self.session_id, "assistant", full_content, self.GUIDE)

        # 拉回后回到PROBE
        async for chunk in self._probe():
            yield chunk

    async def _end_session(self, reason: str) -> AsyncGenerator[str, None]:
        """END状态：结束对话"""
        self.state = self.END
        db.end_session(self.session_id, self.total_rounds)

        # 更新掌握进度
        self._update_mastery_progress()

        yield f"\n\n---\n\n📚 **本节课学习结束**（{reason}）\n\n"

        # 课后闭环改为 fire-and-forget：SESSION_END 立即发给前端，
        # 不等 LLM 调用跑完，改善用户感知等待时间。
        # sm 对象本身在 active_sessions 中，任务跑完前不会被 GC。
        import asyncio
        asyncio.create_task(self._after_class_routines())

    def _update_mastery_progress(self):
        """更新课程的掌握进度"""
        syllabus = db.get_syllabus_items(self.course_id)
        by_chapter = {}
        for s in syllabus:
            ch_idx = s["chapter_index"]
            if ch_idx not in by_chapter:
                by_chapter[ch_idx] = {"total": 0, "mastered": 0}
            by_chapter[ch_idx]["total"] += 1
            if s["status"] == "mastered":
                by_chapter[ch_idx]["mastered"] += 1

        progress_data = {
            "total": len(syllabus),
            "mastered": [s["id"] for s in syllabus if s["status"] == "mastered"],
            "in_progress": [s["id"] for s in syllabus if s["status"] == "in_progress"],
            "pending": [s["id"] for s in syllabus if s["status"] == "pending"],
            "by_chapter": by_chapter,
        }
        db.update_mastery_progress(self.course_id, progress_data)

    async def _check_debate_trigger(self, user_text: str) -> bool:
        """
        检测是否触发反方辩论
        条件：学生连续两次完全认同教师观点

        P2-④ 修复：先做否定词检测，含"不对/但是/我有不同看法"等不算认同
        之前 bug："对，你说的对，但是我有不同看法" 被误判为认同
        """
        # 第一步：否定词检测 — 含强否定词时直接不触发辩论
        negation_keywords = [
            "不对", "不同意", "但是", "可是", "然而",
            "我有不同看法", "不是这样的", "我反对", "未必",
        ]
        if any(kw in user_text for kw in negation_keywords):
            return False

        # 第二步：认同词计数
        agreement_keywords = ["是的", "对", "有道理", "没错", "你说得对", "确实", "同意", "明白了"]
        agreement_count = sum(1 for kw in agreement_keywords if kw in user_text)

        # 如果认同词汇超过2个，且当前轮次>2，触发辩论
        return agreement_count >= 2 and self.current_round > 2

    async def _start_debate(self) -> AsyncGenerator[str, None]:
        """切换到反方角色，提出对立观点"""
        self.state = self.DEBATE

        debate_prompt = f"""现在请你扮演与本章节观点对立的学者。
针对刚才讨论的内容，提出一个有力的反对观点。

要求：
- 必须基于教材内容，不能凭空捏造
- 观点要有学术依据，不能是情绪化反驳
- 最后要求学生思考：哪种观点更有说服力？为什么？"""

        self.messages.append({"role": "user", "content": debate_prompt})

        content_parts = []
        async for chunk in llm.chat_stream(self.messages, temperature=0.9):
            content_parts.append(chunk)
            yield chunk

        full_content = "".join(content_parts)
        self.messages.append({"role": "assistant", "content": full_content})
        db.add_message(self.session_id, "assistant", full_content, self.DEBATE)

    async def _generate_spiritual_notes(self) -> str:
        """
        学习结束后生成思辨笔记
        包含矛盾点列表、未解问题、延伸思考方向
        """
        messages = db.get_messages(self.session_id)
        user_msgs = [m for m in messages if m["role"] == "user"]
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]

        chapter_title = self.chapter_info.get("title", f"第{self.chapter_index + 1}章")

        prompt = f"""根据以下课堂对话，生成一份"思辨笔记"。

章节：{chapter_title}

学生回答：
{chr(10).join(f"- {m['content'][:200]}" for m in user_msgs[-10:])}

教师引导：
{chr(10).join(f"- {m['content'][:200]}" for m in assistant_msgs[-10:])}

笔记格式（只输出JSON）：
{{"core_contradictions": ["...", "..."], "unresolved_questions": ["...", "..."], "extension_directions": ["...", "..."], "personal_reflection": "..."}}"""

        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是思辨笔记生成助手。只返回JSON。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3)

            # 保存到数据库
            db.add_spiritual_note(
                course_id=self.course_id,
                session_id=self.session_id,
                chapter_index=self.chapter_index,
                core_contradictions=json.dumps(result.get("core_contradictions", []), ensure_ascii=False),
                unresolved_questions=json.dumps(result.get("unresolved_questions", []), ensure_ascii=False),
                extension_directions=json.dumps(result.get("extension_directions", []), ensure_ascii=False),
                personal_reflection=result.get("personal_reflection", ""),
                raw_content=json.dumps(result, ensure_ascii=False),
            )

            return result.get("personal_reflection", "")
        except Exception:
            return ""

    def _set_learning_target(self) -> dict:
        """
        根据模式和时长，动态设定学习目标
        """
        from config import MODE_CONTRACT_DEFAULTS

        reading_mode = self.course_info.get("reading_mode", "standard")
        mode_defaults = MODE_CONTRACT_DEFAULTS.get(reading_mode, MODE_CONTRACT_DEFAULTS["standard"])

        if reading_mode == "speed":
            # 速读：目标是掌握最重要的N个知识点
            remaining_items = [s for s in self.syllabus_items
                             if s["status"] != "mastered"]
            target_count = min(5, len(remaining_items))
        elif reading_mode == "standard":
            # 细读：目标是完成当前章节的所有掌握项
            remaining_items = [s for s in self.syllabus_items
                             if s["chapter_index"] == self.chapter_index
                             and s["status"] != "mastered"]
            target_count = len(remaining_items)
        else:
            # 研读：目标是深度理解+辩证分析
            target_count = "unlimited"

        return {
            "target_count": target_count,
            "goal": mode_defaults["goal"],
            "expected_output": mode_defaults["expected_output"],
        }

    async def _after_class_routines(self):
        """课后闭环：自动触发各项产出物（并行执行，缩短等待时间）"""
        errors = []

        # 1. 同步操作：先执行（数据库操作很快）
        # 课后兜底：至少标记1条掌握项
        try:
            self._auto_mark_syllabus()
            refresh = db.get_syllabus_items(self.course_id)
            in_progress_items = [s for s in refresh
                               if s["chapter_index"] == self.chapter_index
                               and s["status"] == "in_progress"
                               and s["id"] not in self.session_mastered_ids]
            if in_progress_items and len(self.session_mastered_ids) < 2:
                db.update_syllabus_item(in_progress_items[0]["id"], "mastered")
                self.session_mastered_ids.add(in_progress_items[0]["id"])
        except Exception as e:
            errors.append(f"掌握项兜底标记失败: {e}")

        # 2. 并行执行 LLM 调用（大幅缩短等待时间）
        import asyncio
        results = await asyncio.gather(
            self._update_profile(),
            self._update_affinity(),
            self._generate_group_chat(),
            self._generate_diary(),
            self._generate_summary(),
            # 研读模式：额外生成思辨笔记
            self._generate_spiritual_notes() if self.depth == "deep" else noop(),
            return_exceptions=True,
        )

        # 记录错误
        task_names = ["画像更新", "情感分更新", "群聊生成", "日记生成", "总结生成", "思辨笔记"]
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                errors.append(f"{task_names[i]}失败: {r}")

        if errors:
            import logging
            logging.warning(f"课后闭环异常（会话{self.session_id}）: {'; '.join(errors)}")

    async def _update_profile(self):
        """更新学习画像"""
        # 获取最近10条对话消息，分析强项/弱项
        messages = db.get_messages(self.session_id)
        user_msgs = [m for m in messages if m["role"] == "user"]

        if not user_msgs:
            return

        # 清理消息中的思考标签，兼容带思考模式的模型
        cleaned_msgs = [strip_thinking_tags(m.get("content", "")) for m in user_msgs[:5] if strip_thinking_tags(m.get("content", ""))]
        if not cleaned_msgs:
            return

        # 使用LLM分析
        analysis_prompt = f"""分析以下学生的课堂回答，总结出：
1. strengths（强项）：学生掌握得好的领域
2. weaknesses（弱项）：学生理解不够的领域
3. misunderstandings（误解）：学生理解有误的地方

每类最多3条，每条不超过20字。

学生的回答：
{chr(10).join(c[:200] for c in cleaned_msgs)}

返回JSON：
{{"strengths": [...], "weaknesses": [...], "misunderstandings": [...]}}"""

        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是学习分析专家。只返回JSON。"},
                {"role": "user", "content": analysis_prompt},
            ])
            for field in ("strengths", "weaknesses", "misunderstandings"):
                items = result.get(field, [])
                if items:
                    db.update_profile(self.course_id, field, items)
        except Exception:
            pass

    async def _update_affinity(self):
        """调整情感分"""
        # 基于轮次和用户参与度
        engagement = self.total_rounds / max(self._get_min_rounds(), 1)
        if engagement >= 1.5:
            delta = 3
            reason = "积极参与，深入思考"
        elif engagement >= 1.0:
            delta = 1
            reason = "认真完成课堂对话"
        elif engagement >= 0.5:
            delta = -1
            reason = "参与度较低"
        else:
            delta = -2
            reason = "课堂参与不积极"

        db.update_affinity(self.course_id, self.teacher_role_id, delta, reason)

    async def _generate_group_chat(self):
        """生成教师群聊（陪审团模式，固定3条）"""
        from config import GROUP_CHAT_DIMENSIONS, ROLES_META

        # 选择其他3位教师（不包括当前教师），随机选择
        import random
        other_teachers = [t for t in self.all_teachers if t != self.teacher_role_id]
        selected = random.sample(other_teachers, min(3, len(other_teachers)))

        messages = db.get_messages(self.session_id)
        user_msgs = [m for m in messages if m["role"] == "user"]
        if not user_msgs:
            return

        # 清理用户消息中的思考标签，兼容带思考模式的模型
        user_quote_raw = user_msgs[-1]["content"] if user_msgs else ""
        user_quote = strip_thinking_tags(user_quote_raw)[:200]

        for teacher_id in selected:
            dimension = GROUP_CHAT_DIMENSIONS.get(teacher_id, "一般点评")
            teacher_name = ROLES_META.get(teacher_id, {}).get("name", teacher_id)

            prompt = f"""你是{teacher_name}老师。请对学生的以下回答进行点评，从"{dimension}"角度出发。

要求：
- 必须引用学生的原话
- 不超过80字
- 不要说废话、空话、套话
- 不要说"哈哈"、"真棒"等无信息量的社交辞令

学生原话："{user_quote}"

返回JSON：
{{"message": "你的点评"}}"""

            try:
                result = await llm.chat_json([
                    {"role": "system", "content": f"你是{teacher_name}老师，你的点评风格是{dimension}。"},
                    {"role": "user", "content": prompt},
                ])
                msg = strip_thinking_tags(result.get("message", ""))
                if msg:
                    db.add_group_chat(self.course_id, self.session_id, teacher_id, msg, user_quote[:100])
            except Exception:
                pass

    async def _generate_diary(self):
        """生成学习日记"""
        messages = db.get_messages(self.session_id)
        chapter_title = self.chapter_info.get("title", f"第{self.chapter_index + 1}章")
        teacher_name = self.teacher_role_id

        # 清理消息中的思考标签，兼容带思考模式的模型
        cleaned_messages = []
        for m in messages[-6:]:
            cleaned_content = strip_thinking_tags(m.get("content", ""))
            if cleaned_content:
                cleaned_messages.append({"role": m.get("role", "user"), "content": cleaned_content[:200]})

        # 构建日记内容
        diary_prompt = f"""你是一个学习者。请以第一人称写一篇学习日记，回顾今天的学习经历。

课程章节：{chapter_title}
老师：{teacher_name}

今天的课堂记录（简略）：
{chr(10).join(f"{m['role']}: {m['content'][:100]}" for m in cleaned_messages)}

要求：
- 第一人称
- 包含对学习内容的感悟
- 包含对老师教学的评价
- 100-200字
- 语言自然，像真实的日记

返回JSON：
{{"title": "日记标题（不超过15字）", "content": "日记正文"}}"""

        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是日记写作助手。"},
                {"role": "user", "content": diary_prompt},
            ])
            title = strip_thinking_tags(result.get("title", ""))
            if not title:
                title = f"{chapter_title}学习心得"
            content = strip_thinking_tags(result.get("content", ""))
            if content:
                db.add_diary(self.course_id, self.session_id, title, content)
        except Exception:
            pass

    async def _generate_summary(self):
        """生成复习总结"""
        messages = db.get_messages(self.session_id)
        chapter_title = self.chapter_info.get("title", f"第{self.chapter_index + 1}章")

        # 清理消息中的思考标签，兼容带思考模式的模型
        cleaned_messages = []
        for m in messages[-8:]:
            cleaned_content = strip_thinking_tags(m.get("content", ""))
            if cleaned_content:
                cleaned_messages.append({"role": m.get("role", "user"), "content": cleaned_content[:200]})

        summary_prompt = f"""根据以下课堂对话，生成一份结构化Markdown复习总结。

章节：{chapter_title}

对话记录：
{chr(10).join(f"**{m['role']}**: {m['content'][:200]}" for m in cleaned_messages)}

复习总结格式（Markdown）：

## 📖 {chapter_title} 复习总结

### 🎯 核心知识点
- ...

### 💡 关键概念
- ...

### ❓ 易错提醒
- ...

### 📝 重点回顾
- ..."""

        try:
            content = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是复习总结生成助手。生成结构化Markdown复习总结。"},
                    {"role": "user", "content": summary_prompt},
                ],
                temperature=0.3,
                max_tokens=1000,
            )
            # 清理总结中的思考标签
            content = strip_thinking_tags(content)
            if content:
                db.add_summary(self.course_id, self.session_id, content)
        except Exception:
            pass
