# 问渠 v1.2 — 书籍 Skill 包引入 产品说明书 (PRD)

**文档名称**：问渠 v1.2 — 书籍 Skill 层引入  
**版本**：v1.0  
**日期**：2026-07-01  
**视角**：reading-mentor (产品视角)

---

## 1. 问题陈述

当前问渠用户在导入一本书后，系统会进行一次性的文本提取和分章处理，然后根据选择的阅读模式(速读/标准/研读)生成不同的教学数据结构(snapshots/syllabus_items)。问题是：

- **知识资产没有显式沉淀**：分章后的结构化知识(框架/原则/技巧)只在当期对话中使用，无法作为可复用的"书籍Skill"被后续不同学习策略调用
- **模式切换不够灵活**：用户可能先用速读模式了解全书，想切换到研读模式深入探讨时，虽然章节数据已存在，但缺乏一个统一的"这本书我能做什么"的总览视图
- **缺少"学完这本书我能做什么"的技能地图**：syllabus_items 是教学化的"能..."描述，但用户看不到一本书整体能转化为哪些可复用的技能/框架

## 2. 产品目标

**核心目标**：在课程导入时，将书籍处理为一个"Skill 包"(Skill Package)，包含结构化的知识资产。用户可以用不同的阅读模式反复调用这个 Skill 包，实现"一本书 = 一个可复用的学习技能"。

**三个子目标**：
1. **一次处理，多次复用**：书籍提取+分章+结构化分析只做一次，生成 Skill 包持久化存储
2. **模式即策略**：速读/标准/研读不再是"不同的处理方式"，而是"对同一个 Skill 包的不同消费策略"
3. **技能可见**：用户能看到一本书转化成了哪些具体技能(框架/原则/技巧/反模式)，形成清晰的学习路径

## 3. 用户故事

| 角色 | 故事 | 价值 |
|---|---|---|
| 速读者 | "我想快速了解一本书的核心观点，但不想陷入细节" | Skill 包的 `core_frameworks` + `cheatsheet` 直接呈现全书精华 |
| 标准学习者 | "我想系统学习这本书，和 AI 教师对话掌握每个知识点" | Skill 包的 `chapters` + `glossary` 作为对话上下文，教师按需引用 |
| 研读者 | "我想深入探讨书中的某个框架，联系实际工作应用" | Skill 包的 `patterns` + `anti_patterns` + 教师深度引导 |
| 重复学习者 | "我半年前读过这本书，现在想换个角度再学一遍" | Skill 包永久保存，随时用新模式重新消费 |

## 4. 功能设计

### 4.1 Skill 包结构

在现有 courses/chapters/syllabus 之上，新增 **Skill 包** 作为书籍的结构化知识资产：

```
Skill Package (对应一门课程)
├── skill_manifest.json          # Skill 元数据(书名/作者/框架列表/章节索引)
├── core_frameworks.md           # 核心思维模型(对应 SKILL.md，~4000 tokens)
├── chapters/                    # 按需加载的章节文件
│   ├── ch01-*.md               # 每章 ~1000 tokens
│   ├── ch02-*.md
│   └── ...
├── glossary.md                  # 术语表(字母排序 + 章节引用)
├── patterns.md                  # 技巧/算法/设计模式
├── anti_patterns.md             # 反模式(避免什么 + 为什么)
└── cheatsheet.md                # 决策表 + 快速参考规则
```

**与现有数据的映射关系**：

| Skill 包文件 | 现有数据结构 | 说明 |
|---|---|---|
| `core_frameworks.md` | `global_highlights.key_points` + `course_profiles` | 合并为教师可引用的核心框架 |
| `chapters/chXX.md` | `chapters.content_slice` + `chapters.content_full` | 结构化为"章节摘要+关键要点" |
| `glossary.md` | `syllabus_items.description` | 从掌握项中提取术语定义 |
| `patterns.md` | `chapter_snapshots.keywords` + `global_highlights.relationships` | 从快照和关系图中提取模式 |
| `anti_patterns.md` | `course_profiles.weaknesses` + `misunderstandings` | 从学习画像中提取常见误区 |
| `cheatsheet.md` | 新生成 | 决策表和快速参考规则 |

### 4.2 新增数据库表

```sql
-- Skill 包主表
CREATE TABLE IF NOT EXISTS skill_packages (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT,
    skill_slug TEXT NOT NULL,       -- 唯一标识，如 "clean-code"
    status TEXT DEFAULT 'generating', -- generating / ready / failed
    file_count INTEGER DEFAULT 0,    -- 生成的 Skill 文件数量
    total_tokens INTEGER DEFAULT 0,  -- 所有文件的 token 总量
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses(id)
);

-- Skill 文件清单
CREATE TABLE IF NOT EXISTS skill_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_package_id TEXT NOT NULL,
    file_name TEXT NOT NULL,         -- 如 "core_frameworks.md"
    file_path TEXT NOT NULL,         -- wenqu_data/skills/<slug>/<file_name>
    file_type TEXT NOT NULL,         -- framework / chapter / glossary / pattern / anti_pattern / cheatsheet
    token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (skill_package_id) REFERENCES skill_packages(id) ON DELETE CASCADE,
    UNIQUE(skill_package_id, file_name)
);
```

### 4.3 新增 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/courses/{id}/skill/generate` | 异步生成 Skill 包(在课程分章完成后自动触发) |
| GET | `/api/courses/{id}/skill/status` | 查询 Skill 包生成状态 |
| GET | `/api/courses/{id}/skill/files` | 列出 Skill 包中的所有文件 |
| GET | `/api/courses/{id}/skill/{filename}` | 读取指定 Skill 文件内容 |
| GET | `/api/courses/{id}/skill/overview` | 获取 Skill 包总览(框架列表+章节索引+术语统计) |

### 4.4 前端新增

- **课程详情页新增"Skill 包"标签页**：展示 Skill 包结构(core_frameworks / chapters / glossary / patterns / cheatsheet)
- **Skill 文件阅读器**：支持浏览各 Skill 文件内容，可点击"用此内容开始对话"直接跳转到对应章节的对话
- **课程首页新增"这本书能学会什么"**：从 Skill 包中提取技能地图，展示核心框架/技巧/反模式清单

## 5. 用户体验流程

```
用户导入书籍
    ↓
系统提取文本 + 分章 (现有逻辑)
    ↓
系统生成 Skill 包 (新增逻辑，异步)
    ├─ 提取核心框架 → core_frameworks.md
    ├─ 结构化章节 → chapters/chXX.md
    ├─ 提取术语 → glossary.md
    ├─ 提取模式 → patterns.md
    ├─ 提取反模式 → anti_patterns.md
    └─ 生成速查表 → cheatsheet.md
    ↓
用户进入课程页，看到两个入口：
    ├─ 【Skill 包】→ 浏览这本书的结构化知识资产
    │     └─ 点击任意文件 → 可"用此内容开始对话"
    │
    └─ 【开始学习】→ 选择阅读模式
          ├─ 速读 → 用 core_frameworks + cheatsheet 快速概览
          ├─ 标准 → 用 chapters + glossary 系统学习
          └─ 研读 → 用 patterns + anti_patterns 深度探讨
```

## 6. 非功能性需求

- **Skill 包生成不阻塞课程创建**：课程创建后立即可用(走现有逻辑)，Skill 包后台异步生成
- **Skill 文件存储在本地文件系统**：`wenqu_data/skills/<skill_slug>/`，不入仓
- **token 估算**：一本 300 页的技术书，Skill 包总大小约 15-25K tokens，可被 LLM context 完整加载
- **向后兼容**：老课程没有 Skill 包，不影响现有功能

## 7. 成功指标

| 指标 | 基线 | 目标 |
|---|---|---|
| 课程→Skill 包转化率 | N/A | 80%+ 的课程成功生成 Skill 包 |
| 用户浏览 Skill 包比例 | 0% | 30%+ 的用户至少浏览一次 Skill 包 |
| 从 Skill 包入口发起对话比例 | 0% | 15%+ 的对话从 Skill 包文件"开始对话"入口触发 |
| 重复学习率(同一课程多模式) | 未知 | 提升 20%(对比实验组) |
