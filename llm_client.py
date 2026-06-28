"""
问渠（Wenqu）v1.1 LLM API客户端模块
支持OpenAI兼容接口的流式和非流式调用
支持多模型层级（fast / balanced / flagship）调度
"""

import json
import asyncio
from typing import AsyncGenerator, Optional, List, Dict, Any

import httpx

from config import get_llm_config, get_model_tier_config, DEPTH_CONFIG


class LLMClient:
    """LLM API客户端（单模型）"""

    def __init__(self, tier: str = "balanced"):
        cfg = get_model_tier_config(tier) if tier != "default" else get_llm_config()
        self.api_key = cfg["api_key"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.timeout = cfg.get("timeout", 60)
        self.tier = tier

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_stream(
        self,
        messages: List[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """流式对话"""
        if not self.api_key:
            yield "⚠️ 请先配置LLM API密钥。在设置中填入你的API Key。"
            return

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": True,
                    },
                ) as resp:
                    if resp.status_code != 200:
                        await resp.aread()
                        yield f"⚠️ API错误 ({resp.status_code})"
                        return

                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
            except httpx.TimeoutException:
                yield "⏳ API请求超时了"
            except Exception:
                yield "⚠️ 网络好像有点问题"

    async def chat(
        self,
        messages: List[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        response_format: Optional[dict] = None,
    ) -> str:
        """非流式对话，返回完整响应"""
        if not self.api_key:
            return "⚠️ 请先配置LLM API密钥。"

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
                if resp.status_code != 200:
                    return "⚠️ API错误"
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception:
                return "⚠️ 网络好像有点问题"

    async def chat_json(self, messages: List[dict], temperature: float = 0.3) -> dict:
        """非流式对话，返回JSON对象"""
        text = await self.chat(
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        try:
            return json.loads(text)
        except (json.JSONDecodeError, KeyError):
            return {"status": "thinking", "mastered_items": []}

    async def generate_title(self, content: str) -> str:
        """为日记/内容生成标题"""
        messages = [
            {"role": "system", "content": "你是一个标题生成器。根据以下内容，生成一个简洁有吸引力的中文标题（不超过15个字）。只输出标题本身。"},
            {"role": "user", "content": content[:500]},
        ]
        return (await self.chat(messages, temperature=0.5, max_tokens=50)).strip()

    async def test_connection(self, base_url: str, model: str, api_key: str) -> dict:
        """测试连接，不修改单例状态"""
        if not api_key:
            return {"success": False, "message": "API Key 不能为空"}

        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 5,
            "temperature": 0,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            try:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                if resp.status_code == 200:
                    return {"success": True, "message": "连接成功 ✅"}
                elif resp.status_code == 401:
                    return {"success": False, "message": "API Key 无效 (401)"}
                elif resp.status_code == 404:
                    return {"success": False, "message": f"模型 '{model}' 不存在或接口地址错误 (404)"}
                else:
                    return {"success": False, "message": f"服务器返回错误 ({resp.status_code})"}
            except httpx.TimeoutException:
                return {"success": False, "message": "连接超时，请检查 base_url 是否正确"}
            except httpx.ConnectError:
                return {"success": False, "message": "无法连接服务器，请检查 base_url 或网络"}
            except Exception as e:
                return {"success": False, "message": f"连接失败: {str(e)[:100]}"}


# ==================== 多模型分级调度 ====================

class MultiModelClient:
    """多模型分级客户端：按 tier 自动分发到不同模型"""

    def __init__(self):
        self._instances: Dict[str, LLMClient] = {}

    def _get_client(self, tier: str) -> LLMClient:
        """获取或创建指定层级的客户端实例"""
        if tier not in self._instances:
            self._instances[tier] = LLMClient(tier=tier)
        return self._instances[tier]

    async def chat(self, tier: str, messages: List[dict],
                   temperature: float = 0.7, max_tokens: int = 2048,
                   response_format: Optional[dict] = None) -> str:
        """按层级发送非流式对话"""
        return await self._get_client(tier).chat(
            messages, temperature, max_tokens, response_format
        )

    async def chat_json(self, tier: str, messages: List[dict],
                        temperature: float = 0.3) -> dict:
        """按层级发送 JSON 对话"""
        return await self._get_client(tier).chat_json(messages, temperature)

    async def chat_stream(self, tier: str, messages: List[dict],
                          temperature: float = 0.7, max_tokens: int = 2048
                          ) -> AsyncGenerator[str, None]:
        """按层级发送流式对话"""
        async for token in self._get_client(tier).chat_stream(
            messages, temperature, max_tokens
        ):
            yield token

    def get_default_tier(self) -> str:
        """获取当前主配置建议的默认层级"""
        cfg = get_llm_config()
        model_lower = cfg.get("model", "").lower()
        # 自动推断层级
        if any(k in model_lower for k in ["haiku", "mini", "flash", "gemini-1.5-flash"]):
            return "fast"
        elif any(k in model_lower for k in ["sonnet", "gpt-4o", "gemini-2.0", "qwen-max", "deepseek-chat"]):
            return "balanced"
        elif any(k in model_lower for k in ["opus", "o1", "o3", "claude-3.5", "gemini-2.5"]):
            return "flagship"
        return "balanced"

    def refresh_tier(self, tier: str):
        """刷新指定层级的客户端（配置变更后调用）"""
        if tier in self._instances:
            del self._instances[tier]

    def refresh_all(self):
        """刷新所有层级客户端"""
        self._instances.clear()


# 全局单例
llm = LLMClient(tier="default")
multi_llm = MultiModelClient()


def get_depth_prompt(depth: str) -> dict:
    """获取认知深度配置"""
    return DEPTH_CONFIG.get(depth, DEPTH_CONFIG["standard"])


def build_system_prompt(role_id: str, depth: str, sliders: dict, course_title: str, chapter_title: str, previous_summaries: list) -> str:
    """
    构建完整的System Prompt
    从角色文件中读取角色设定，注入深度配置和滑块参数
    """
    from pathlib import Path
    from config import PROMPTS_DIR

    prompt_path = PROMPTS_DIR / f"{role_id}.md"
    role_prompt = f"你是教师角色 {role_id}。"
    if prompt_path.exists():
        role_prompt = prompt_path.read_text(encoding="utf-8")

    depth_cfg = get_depth_prompt(depth)

    slider_notes = []
    if sliders.get("strictness", 0) > 0:
        slider_notes.append("风格偏严谨：严格要求逻辑严密性，指出思维漏洞")
    elif sliders.get("strictness", 0) < 0:
        slider_notes.append("风格偏发散：鼓励自由联想，接受非常规思路")

    if sliders.get("encouragement", 0) > 0:
        slider_notes.append("风格偏鼓励：多给予正向反馈，用温和方式指出不足")
    elif sliders.get("encouragement", 0) < 0:
        slider_notes.append("风格偏严厉：直截了当指出问题，不回避批评")

    if sliders.get("verbosity", 0) > 0:
        slider_notes.append("风格偏详实：详细解释，举例丰富")
    elif sliders.get("verbosity", 0) < 0:
        slider_notes.append("风格偏简练：言简意赅，不说废话")

    prev_summary_text = ""
    if previous_summaries:
        prev_summary_text = "\n前序章节要点：\n" + "\n".join(previous_summaries[:3])

    # 研读模式：添加辩证分析指令
    dialectical_instruction = ""
    if depth == "deep":
        dialectical_instruction = f"""
## 辩证分析指令（研读模式专用）

作为{role_id}老师，你在教学中必须做到：

1. **跨章对比**：每次提问时，至少引用一个前序章节的观点进行对比
2. **隐含前提揭示**：指出作者未明确说明的假设，并质疑其合理性
3. **应用场景检验**：引导学生思考"这个理论在什么情况下会失效"
4. **反向思考**：定期要求学生从对立角度分析同一问题

前序章节摘要（供对比使用）：
{chr(10).join(f"- {s['title']}: {s['summary']}" for s in previous_summaries[:5]) if previous_summaries else "(暂无前序章节)"}
"""

    system_text = f"""{role_prompt}

## 当前课程信息
- 课程：{course_title}
- 当前章节：{chapter_title}
{prev_summary_text}

## 认知深度配置
- SHARE复述长度限制：{"不限" if depth_cfg["share_max_length"] == 0 else f"不超过{depth_cfg['share_max_length']}字"}
- PROBE复杂度：{depth_cfg['probe_complexity']}
- EXPLAIN详细度：{depth_cfg['explain_detail']}
{dialectical_instruction}

## 风格调节参数
{"；".join(slider_notes) if slider_notes else "按默认风格"}

## 状态机约束（必须遵守）
你将在以下状态中工作，由系统控制状态流转：

1. **SHARE** - 分享教材片段：用自己的话复述当前章节内容，必须以"书上有个很有趣的观点..."或"这段让我联想到..."开头。
2. **PROBE** - 提出1个开放性问题：针对教材内容提出恰好1个问题，关联用户薄弱点。
3. **EXPLAIN** - 讲解模式：当用户卡住时，用通俗易懂的方式解释概念。
4. **GUIDE** - 引导拉回：当用户偏离主题时，温柔拉回。
5. **EVAL** - 评估模式：由系统独立调用，不需你在此处输出

重要规则：
- 永远不要直接给出问题的完整答案
- 用追问引导用户自己发现答案
- 每次只提1个问题，不要连发多问
- 提问要关联用户之前的回答和已知薄弱点
- 禁止让用户"请阅读第X页"

<!-- 系统提示词 - 问渠 v1.1 -->
"""
    return system_text


def build_deep_dialectical_prompt(role_id: str, course_title: str, chapter_title: str, previous_summaries: list) -> str:
    """
    为研读模式构建专门的辩证分析提示词（用于扩展LLM的系统指令）
    """
    dialectical_instruction = f"""
## 辩证分析指令（研读模式专用）

作为{role_id}老师，你在教学中必须做到：

1. **跨章对比**：每次提问时，至少引用一个前序章节的观点进行对比
2. **隐含前提揭示**：指出作者未明确说明的假设，并质疑其合理性
3. **应用场景检验**：引导学生思考"这个理论在什么情况下会失效"
4. **反向思考**：定期要求学生从对立角度分析同一问题

前序章节摘要（供对比使用）：
{chr(10).join(f"- {s['title']}: {s['summary']}" for s in previous_summaries[:5]) if previous_summaries else "(暂无前序章节)"}

批判性思维角度：
- 作者假设是否合理？
- 不同章节之间是否存在矛盾？
- 这个理论的反面观点是什么？
- 在实际应用中可能遇到什么局限？
"""
    return dialectical_instruction
