"""
问渠（Wenqu）v1.1 对话状态机模块

严格状态流转：INIT → SHARE → PROBE → WAIT_USER → EVAL → ACTION → (循环至PROBE) / END
严禁LLM自由决定对话流程
"""

import json
import asyncio
from datetime import datetime
from typing import Optional, AsyncGenerator, List

from llm_client import llm, build_system_prompt
from config import FLOW_DETECTION, DEPTH_CONFIG
import database as db


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
        """根据学习时长计算最小对话轮次"""
        mapping = {15: 3, 30: 6, 60: 12}
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

    async def _share(self) -> AsyncGenerator[str, None]:
        """SHARE状态：AI分享教材片段"""
        self.state = self.SHARE
        self.messages.append({
            "role": "user",
            "content": "请用自己的话复述这段教材的核心观点，以'书上有个很有趣的观点...'或'这段让我联想到...'开头。",
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

        self.messages.append({
            "role": "user",
            "content": f"请针对这段教材内容提出1个开放性问题，帮助用户深入思考。{weak_prompt}只能提1个问题，不要多问。",
        })

        content_parts = []
        async for chunk in llm.chat_stream(self.messages, temperature=0.7):
            content_parts.append(chunk)
            yield chunk

        full_content = "".join(content_parts)
        self.messages.append({"role": "assistant", "content": full_content})
        db.add_message(self.session_id, "assistant", full_content, self.PROBE)

        # 进入WAIT_USER
        self.state = self.WAIT_USER

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
        if not self.is_flow_state and self.current_round >= self._get_min_rounds():
            async for chunk in self._end_session("达到最小时长"):
                yield chunk

    def _check_flow_state(self, user_text: str):
        """心流检测"""
        if self.current_round >= FLOW_DETECTION["min_rounds"]:
            if len(user_text) > FLOW_DETECTION["reply_length_threshold"] and \
               ("?" in user_text or "为什么" in user_text or "如何" in user_text or "是不是" in user_text):
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

        yield f"\n\n---\n\n📚 **本节课学习结束**（{reason}）\n\n"

        # 异步触发课后闭环
        await self._after_class_routines()

    async def _after_class_routines(self):
        """课后闭环：自动触发各项产出物"""
        errors = []

        # 课后兜底：至少标记1条掌握项，让进度有变化
        try:
            self._auto_mark_syllabus()
            # 如果仍然没有 mastered 项，再强升一条 in_progress → mastered
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

        try:
            await self._update_profile()
        except Exception as e:
            errors.append(f"画像更新失败: {e}")
        try:
            await self._update_affinity()
        except Exception as e:
            errors.append(f"情感分更新失败: {e}")
        try:
            await self._generate_group_chat()
        except Exception as e:
            errors.append(f"群聊生成失败: {e}")
        try:
            await self._generate_diary()
        except Exception as e:
            errors.append(f"日记生成失败: {e}")
        try:
            await self._generate_summary()
        except Exception as e:
            errors.append(f"总结生成失败: {e}")

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

        # 使用LLM分析
        analysis_prompt = f"""分析以下学生的课堂回答，总结出：
1. strengths（强项）：学生掌握得好的领域
2. weaknesses（弱项）：学生理解不够的领域
3. misunderstandings（误解）：学生理解有误的地方

每类最多3条，每条不超过20字。

学生的回答：
{chr(10).join(m['content'][:200] for m in user_msgs[:5])}

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

        user_quote = user_msgs[-1]["content"][:100] if user_msgs else "（无用户发言）"

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
                msg = result.get("message", "")
                if msg:
                    db.add_group_chat(self.course_id, self.session_id, teacher_id, msg, user_quote)
            except Exception:
                pass

    async def _generate_diary(self):
        """生成学习日记"""
        messages = db.get_messages(self.session_id)
        chapter_title = self.chapter_info.get("title", f"第{self.chapter_index + 1}章")
        teacher_name = self.teacher_role_id

        # 构建日记内容
        diary_prompt = f"""你是一个学习者。请以第一人称写一篇学习日记，回顾今天的学习经历。

课程章节：{chapter_title}
老师：{teacher_name}

今天的课堂记录（简略）：
{chr(10).join(f"{m['role']}: {m['content'][:100]}" for m in messages[-6:])}

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
            title = result.get("title", f"{chapter_title}学习心得")
            content = result.get("content", "")
            if content:
                db.add_diary(self.course_id, self.session_id, title, content)
        except Exception:
            pass

    async def _generate_summary(self):
        """生成复习总结"""
        messages = db.get_messages(self.session_id)
        chapter_title = self.chapter_info.get("title", f"第{self.chapter_index + 1}章")

        summary_prompt = f"""根据以下课堂对话，生成一份结构化Markdown复习总结。

章节：{chapter_title}

对话记录：
{chr(10).join(f"**{m['role']}**: {m['content'][:200]}" for m in messages[-8:])}

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
            if content:
                db.add_summary(self.course_id, self.session_id, content)
        except Exception:
            pass
