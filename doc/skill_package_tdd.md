# 问渠 v1.2 — 书籍 Skill 包引入 开发说明书

**文档名称**：问渠 v1.2 — 书籍 Skill 层引入  
**版本**：v1.0  
**日期**：2026-07-01  
**视角**：developer + db-migrator + prompt-engineer

---

## 1. 架构变更总览

```
                    变更前                                    变更后
┌──────────────────────────────────┐       ┌──────────────────────────────────────────┐
│  课程导入 (courses)              │       │  课程导入 (courses)                      │
│    └─→ 分章 (chapters)          │       │    └─→ 分章 (chapters)                   │
│    └─→ 快照/掌握项               │       │    └─→ 快照/掌握项                       │
│                                       │       │    └─→ Skill 包生成 (新增)           │
│                                       │       │         └─→ skill_packages           │
│                                       │       │         └─→ skill_files              │
│                                       │       │         └─→ wenqu_data/skills/<slug>/│
└──────────────────────────────────┘       └──────────────────────────────────────────┘
```

**关键设计决策**：Skill 包是课程的**衍生资产**，不是独立的顶层实体。`skill_packages.course_id` 有 UNIQUE 约束，保证一书一包。

## 2. 数据库变更 (db-migrator 视角)

**文件**：`database.py`

### 2.1 新增表

在 `init_db()` 的 `executescript` 中追加：

```sql
-- Skill 包主表
CREATE TABLE IF NOT EXISTS skill_packages (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT,
    skill_slug TEXT NOT NULL,
    status TEXT DEFAULT 'generating',
    file_count INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

-- Skill 文件清单
CREATE TABLE IF NOT EXISTS skill_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_package_id TEXT NOT NULL,
    file_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (skill_package_id) REFERENCES skill_packages(id) ON DELETE CASCADE,
    UNIQUE(skill_package_id, file_name)
);
```

### 2.2 Schema 迁移

在 `_migrate_schema()` 中无需额外逻辑(新表用 `IF NOT EXISTS` 已处理)。

### 2.3 级联删除

`skill_files.skill_package_id` 设 `ON DELETE CASCADE`，删除课程时自动清理 Skill 包数据。

## 3. 后端实现 (developer 视角)

### 3.1 新增文件

```
wenqu/
├── skill_generator.py          # Skill 包生成引擎(新增)
├── routes/
│   └── skill_routes.py         # Skill API 路由(新增)
├── prompts/
│   └── skill_generation.md     # Skill 生成 prompt(新增)
└── tests/
    └── test_skill_generator.py # 测试(新增)
```

### 3.2 `skill_generator.py` — Skill 包生成引擎

```python
"""
问渠 (Wenqu) v1.2 — Skill 包生成引擎

在课程分章完成后，异步生成结构化的 Skill 包文件。
参考 book-to-skill 的"提取结构，不做摘要"理念，但适配问渠的教学场景。
"""

import json
import os
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

import database as db
from llm_client import llm
from config import DATA_DIR

logger = logging.getLogger(__name__)

SKILLS_DIR = DATA_DIR / "skills"


async def generate_skill_package(course_id: str) -> str:
    """
    为课程生成 Skill 包，返回 skill_slug。
    
    流程：
    1. 创建 skill_packages 记录(status='generating')
    2. 并行生成 6 个 Skill 文件
    3. 更新 skill_packages(status='ready') + skill_files 记录
    """
    course = db.get_course(course_id)
    if not course:
        raise ValueError(f"Course {course_id} not found")
    
    chapters = db.get_chapters(course_id)
    syllabus = db.get_syllabus_items(course_id)
    snapshots = db.get_chapter_snapshots(course_id) if course["reading_mode"] == "speed" else []
    highlights = db.get_global_highlights(course_id)
    
    # 生成 slug: "<title-hash>"
    import hashlib
    title = course["title"] or "untitled"
    skill_slug = f"{title.lower().replace(' ', '-')[:30]}-{hashlib.md5(course_id.encode()).hexdigest()[:6]}"
    
    # 创建 skill_packages 记录
    skill_pkg_id = str(course_id)  # 复用 course_id 作为 skill_package_id
    db.create_skill_package(skill_pkg_id, course_id, title, skill_slug)
    
    # 创建 skill 目录
    skill_dir = SKILLS_DIR / skill_slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # 并行生成 6 个文件
    tasks = [
        generate_core_frameworks(course_id, title, highlights, syllabus, skill_dir),
        generate_chapter_summaries(course_id, chapters, skill_dir),
        generate_glossary(syllabus, chapters, skill_dir),
        generate_patterns(snapshots, highlights, chapters, skill_dir),
        generate_anti_patterns(syllabus, chapters, skill_dir),
        generate_cheatsheet(highlights, syllabus, skill_dir),
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 统计结果
    file_count = sum(1 for r in results if not isinstance(r, Exception) and r)
    total_tokens = sum(r["tokens"] for r in results if isinstance(r, dict) and r)
    
    # 更新 skill_packages
    db.update_skill_package_status(skill_pkg_id, "ready", file_count, total_tokens)
    
    # 注册 skill_files
    for r in results:
        if isinstance(r, dict) and r.get("file_name"):
            db.add_skill_file(skill_pkg_id, **r)
    
    logger.info(f"Skill package generated: {skill_slug} ({file_count} files, {total_tokens} tokens)")
    return skill_slug


async def generate_core_frameworks(
    course_id: str, title: str, highlights, syllabus, skill_dir: Path
) -> Dict[str, Any]:
    """生成 core_frameworks.md — 核心思维模型"""
    # 调用 LLM 从 highlights.key_points + syllabus 提炼框架
    prompt = _build_core_frameworks_prompt(title, highlights, syllabus)
    response = await llm.chat_json(prompt, system="提取本书的核心框架和思维模型")
    
    content = _format_core_frameworks(response, title)
    file_path = skill_dir / "core_frameworks.md"
    file_path.write_text(content, encoding="utf-8")
    
    return {
        "file_name": "core_frameworks.md",
        "file_path": str(file_path),
        "file_type": "framework",
        "token_count": len(content) // 4,  # 粗略估算
    }


async def generate_chapter_summaries(
    course_id: str, chapters, skill_dir: Path
) -> List[Dict[str, Any]]:
    """生成 chapters/chXX.md — 每章一个文件"""
    results = []
    chapters_dir = skill_dir / "chapters"
    chapters_dir.mkdir(exist_ok=True)
    
    for idx, chapter in enumerate(chapters):
        # 调用 LLM 生成结构化章节摘要(800-1200 tokens)
        prompt = _build_chapter_summary_prompt(chapter)
        response = await llm.chat_json(prompt, system="生成结构化章节摘要")
        
        content = _format_chapter_summary(response, idx, chapter["title"])
        file_name = f"ch{idx+1:02d}-{_slugify(chapter['title'][:20])}.md"
        file_path = chapters_dir / file_name
        file_path.write_text(content, encoding="utf-8")
        
        results.append({
            "file_name": file_name,
            "file_path": str(file_path),
            "file_type": "chapter",
            "token_count": len(content) // 4,
        })
    
    return results


# ... 其他生成函数(generate_glossary, generate_patterns, 
#     generate_anti_patterns, generate_cheatsheet) 结构类似


def _build_core_frameworks_prompt(title, highlights, syllabus):
    """构建 core_frameworks 的 LLM prompt"""
    return f"""你是{title}的领域专家。请从以下内容中提取本书的核心框架和思维模型：

【全书精华】
{highlights}

【掌握项清单】
{syllabus}

请以以下格式输出 JSON：
{{
  "title": "{title}",
  "frameworks": [
    {{
      "name": "框架名称(保留原文术语)",
      "description": "一句话描述",
      "application": "何时使用/如何应用",
      "chapter_refs": [1, 3, 5]
    }}
  ],
  "principles": [...],
  "voice_calibration": "作者的思考方式和表达风格"
}}

要求：
1. 保留原文中的专有名词和术语
2. 每个框架要有明确的应用场景
3. 不要泛泛而谈，要具体到可操作的层面
"""


def _slugify(text):
    import re
    return re.sub(r'[^\w\s-]', '', text).replace(' ', '-').lower()
```

### 3.3 `routes/skill_routes.py` — Skill API 路由

```python
"""
问渠 (Wenqu) v1.2 — Skill 包 API 路由模块
"""

import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException

import database as db
from config import DATA_DIR
from skill_generator import generate_skill_package

logger = logging.getLogger(__name__)

SKILLS_DIR = DATA_DIR / "skills"


def register_skill_routes(app: FastAPI):
    """注册 Skill 相关 API 路由"""
    
    @app.post("/api/courses/{course_id}/skill/generate")
    async def api_generate_skill(course_id: str):
        """异步生成 Skill 包"""
        course = db.get_course(course_id)
        if not course:
            raise HTTPException(404, "课程不存在")
        
        # 如果已经在生成中，直接返回
        existing = db.get_skill_package(course_id)
        if existing and existing["status"] == "generating":
            return {"status": "generating", "skill_slug": existing["skill_slug"]}
        
        # 启动异步任务
        import asyncio
        task = asyncio.create_task(_background_generate_skill(course_id))
        return {"status": "started", "course_id": course_id}
    
    @app.get("/api/courses/{course_id}/skill/status")
    async def api_skill_status(course_id: str):
        """查询 Skill 包状态"""
        pkg = db.get_skill_package(course_id)
        if not pkg:
            raise HTTPException(404, "Skill 包不存在")
        return pkg
    
    @app.get("/api/courses/{course_id}/skill/files")
    async def api_skill_files(course_id: str):
        """列出 Skill 包中的所有文件"""
        files = db.get_skill_files(course_id)
        return {"files": files}
    
    @app.get("/api/courses/{course_id}/skill/{filename}")
    async def api_skill_file(course_id: str, filename: str):
        """读取指定 Skill 文件"""
        pkg = db.get_skill_package(course_id)
        if not pkg:
            raise HTTPException(404, "Skill 包不存在")
        
        file_path = SKILLS_DIR / pkg["skill_slug"] / filename
        if not file_path.exists():
            raise HTTPException(404, f"文件不存在: {filename}")
        
        content = file_path.read_text(encoding="utf-8")
        return {"filename": filename, "content": content}
    
    @app.get("/api/courses/{course_id}/skill/overview")
    async def api_skill_overview(course_id: str):
        """获取 Skill 包总览"""
        pkg = db.get_skill_package(course_id)
        if not pkg:
            raise HTTPException(404, "Skill 包不存在")
        
        files = db.get_skill_files(course_id)
        return {
            "skill_package": pkg,
            "files": files,
            "file_types": {f["file_type"]: f["file_name"] for f in files},
        }


async def _background_generate_skill(course_id: str):
    """后台异步生成 Skill 包"""
    try:
        await generate_skill_package(course_id)
    except Exception as e:
        logger.error(f"Skill 包生成失败: course_id={course_id}, error={e}")
        db.update_skill_package_status(course_id, "failed", 0, 0)
```

### 3.4 集成到现有课程创建流程

**文件**：`routes/course_routes.py` 或 `app.py` 中的 `run_chapter_generation`

在分章完成后(无论成功与否)，自动触发 Skill 包生成：

```python
# 在 run_chapter_generation() 的最后，章节保存完成后：

# 异步触发 Skill 包生成(不阻塞主流程)
import asyncio
asyncio.create_task(_trigger_skill_generation(course_id))


async def _trigger_skill_generation(course_id: str):
    """在课程分章完成后，延迟 5 秒触发 Skill 包生成"""
    await asyncio.sleep(5)
    try:
        from routes.skill_routes import api_generate_skill
        await api_generate_skill(course_id)
    except Exception as e:
        logger.warning(f"Skill 包生成触发失败: {e}")
```

### 3.5 Prompt 设计 (prompt-engineer 视角)

**文件**：`prompts/skill_generation.md`

Skill 生成的 prompt 需要遵循问渠已有的 8 个教师角色体系，每个角色对 Skill 的理解角度不同：

| 角色 | Skill 生成侧重 |
|---|---|
| march7 (活泼助手) | 框架名称要有趣、易记 |
| keqing (效率至上) | cheatsheet 要实用、可快速查阅 |
| ganyu (严谨学者) | glossary 要准确、有章节引用 |
| socrates (苏格拉底) | 反模式要引导批判性思考 |
| linmo (文学导师) | voice_calibration 要捕捉作者风格 |
| yunyi (创意教练) | patterns 要鼓励创造性应用 |
| zhiwei (实战专家) | 每个框架都要有应用场景 |
| yunxiu (深度思考) | core_frameworks 要有哲学深度 |

**默认使用 linmo 风格生成**，因为 Skill 包是"知识资产"，需要平衡准确性和可读性。

## 4. 前端实现

**文件**：`static/index.html` + `static/js/` (如有分离)

### 4.1 新增 Skill 包标签页

在课程详情页新增"Skill 包"标签：

```html
<!-- 课程详情页新增 Skill 包标签 -->
<div class="course-tabs">
    <button class="tab active" data-tab="chat">对话</button>
    <button class="tab" data-tab="chapters">章节</button>
    <button class="tab" data-tab="skill">Skill 包</button>  <!-- 新增 -->
</div>

<div id="skill-panel" class="tab-panel" style="display:none;">
    <div class="skill-overview">
        <h3>📦 这本书的 Skill 包</h3>
        <div id="skill-status">正在生成...</div>
        <div id="skill-files" class="skill-file-list"></div>
    </div>
</div>
```

### 4.2 Skill 文件浏览器

```javascript
// 加载 Skill 包文件列表
async function loadSkillFiles(courseId) {
    const res = await fetch(`/api/courses/${courseId}/skill/files`);
    const data = await res.json();
    
    const container = document.getElementById('skill-files');
    container.innerHTML = data.files.map(f => `
        <div class="skill-file-item" data-type="${f.file_type}" data-file="${f.file_name}">
            <span class="file-icon">${getFileIcon(f.file_type)}</span>
            <span class="file-name">${f.file_name}</span>
            <span class="file-size">${formatTokens(f.token_count)}</span>
            <button class="btn-use-skill" onclick="startDialogueWithSkill('${f.file_name}')">
                用此内容开始对话
            </button>
        </div>
    `).join('');
}

// 从 Skill 文件发起对话
async function startDialogueWithSkill(fileName) {
    // 读取文件内容
    const res = await fetch(`/api/courses/${courseId}/skill/${fileName}`);
    const data = await res.json();
    
    // 将文件内容作为对话的初始上下文
    // 这会让 AI 教师基于这个 Skill 文件开始苏格拉底对话
    window.location.href = `/course/${courseId}?skill_context=${encodeURIComponent(fileName)}`;
}
```

### 4.3 技能地图可视化

在课程首页展示"这本书能学会什么"：

```html
<div class="skill-map">
    <h3>🎯 这本书能学会什么</h3>
    <div class="framework-tags">
        <!-- 从 core_frameworks.md 提取框架名称 -->
        <span class="tag">设计模式</span>
        <span class="tag">领域驱动设计</span>
        <span class="tag">微服务架构</span>
        <!-- ... -->
    </div>
    <div class="pattern-list">
        <!-- 从 patterns.md 提取技巧列表 -->
    </div>
    <div class="anti-pattern-list">
        <!-- 从 anti_patterns.md 提取反模式 -->
    </div>
</div>
```

## 5. 测试计划

**文件**：`tests/test_skill_generator.py`

| 测试场景 | 类型 | 说明 |
|---|---|---|
| `test_generate_skill_package_creates_dir` | P0 | 验证 Skill 目录创建 |
| `test_generate_core_frameworks_json` | P0 | 验证 core_frameworks 的 LLM 响应解析 |
| `test_generate_chapter_summaries_batch` | P0 | 验证多章节并行生成 |
| `test_skill_slug_uniqueness` | P0 | 验证同一本书不会生成重复 slug |
| `test_skill_package_status_flow` | P1 | 验证 generating → ready/failed 状态流转 |
| `test_skill_file_api_read` | P1 | 验证 API 读取 Skill 文件 |
| `test_skill_generation_llm_failure` | P1 | 验证 LLM 失败时降级处理 |
| `test_skill_overview_aggregation` | P2 | 验证 Skill 包总览数据聚合 |
| `test_existing_course_without_skill` | P2 | 验证老课程(no Skill 包)不影响使用 |

## 6. 实施步骤

| 阶段 | 内容 | 依赖 |
|---|---|---|
| **Phase 1** | 数据库 schema 变更 + `skill_generator.py` 核心逻辑 | 无 |
| **Phase 2** | `skill_routes.py` API + 集成到课程创建流程 | Phase 1 |
| **Phase 3** | 前端 Skill 包标签页 + 文件浏览器 | Phase 2 |
| **Phase 4** | 技能地图可视化 + "用此内容开始对话" | Phase 3 |
| **Phase 5** | 测试补全 + 性能优化 | Phase 4 |

## 7. 风险和缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| LLM 调用次数翻倍 | 成本增加 | Skill 包生成可做批处理(每天固定时间)，且生成结果持久化可复用 |
| Skill 包生成耗时过长 | 用户体验 | 完全异步，课程创建不受影响；前端显示"生成中"状态 |
| 非技术类书籍 Skill 质量差 | 价值低 | 在 prompt 中加入"根据书籍类型调整输出"逻辑，小说/散文等自动跳过 Skill 生成 |
| 存储膨胀 | 磁盘占用 | Skill 包总大小约 15-25K tokens/本，约 60-100KB，可接受 |
