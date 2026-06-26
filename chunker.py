"""
问渠（Wenqu）v1.1 三级回退分章引擎

支持PDF/EPUB/MD/TXT/URL文本提取和智能分章
"""

import re
import io
import asyncio
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import aiofiles

from llm_client import llm


async def extract_text_from_pdf(file_path: str) -> str:
    """从PDF提取文本"""
    try:
        return ""
    except Exception as e:
        return f"⚠️ 暂不支持PDF导入：{str(e)}"


async def extract_text_from_epub(file_path: str) -> str:
    """从EPUB提取文本，保留HTML结构以便更好分章"""
    import zipfile
    import re as _re
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            texts = []
            html_files = sorted([
                n for n in z.namelist()
                if n.endswith(('.xhtml', '.html'))
                and 'nav' not in n.lower()
                and 'toc' not in n.lower()
                and 'cover' not in n.lower()
            ])
            for hf in html_files:
                try:
                    content = z.read(hf).decode('utf-8', errors='ignore')
                    content = _re.sub(r'<br\s*/?>', '\n', content)
                    content = _re.sub(r'</?p[^>]*>', '\n', content)
                    content = _re.sub(r'</?h[1-6][^>]*>', '\n### ', content)
                    content = _re.sub(r'<li[^>]*>', '\n- ', content)
                    content = _re.sub(r'</?li[^>]*>', '', content)
                    content = _re.sub(r'</?ul[^>]*>|</?ol[^>]*>', '', content)
                    content = _re.sub(r'<[^>]+>', '', content)
                    entities = {
                        '&amp;': '&', '&lt;': '<', '&gt;': '>',
                        '&quot;': '"', '&#39;': "'", '&apos;': "'",
                        '&nbsp;': ' ', '&ndash;': '-', '&mdash;': '—',
                    }
                    for k, v in entities.items():
                        content = content.replace(k, v)
                    text = _re.sub(r'\n{3,}', '\n\n', content).strip()
                    if text:
                        texts.append(text)
                except Exception:
                    continue
            if texts:
                return '\n\n---\n\n'.join(texts)
            return "⚠️ EPUB中未找到有效内容"
    except Exception as e:
        return f"⚠️ EPUB解析失败：{str(e)}"

async def extract_text_from_md(file_path: str) -> str:
    """从Markdown文件读取"""
    async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
        return await f.read()


async def extract_text_from_txt(file_path: str) -> str:
    """从TXT文件读取"""
    async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
        return await f.read()


async def extract_text_from_url(url: str) -> str:
    """从URL抓取正文"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            resp.raise_for_status()
            html = resp.text
            # 尝试用BeautifulSoup提取正文
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                # 移除脚本和样式
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                body = soup.find("article") or soup.find("main") or soup.find("body")
                if body:
                    text = body.get_text(separator="\n", strip=True)
                    if len(text) > 100:
                        return text
                # 兜底：全部文本
                text = soup.get_text(separator="\n", strip=True)
                return text[:50000] if len(text) > 50000 else text
            except ImportError:
                # 没有BeautifulSoup时的简单提取
                from markdownify import markdownify as md
                text = md(html, heading_style="ATX")
                return text[:50000] if len(text) > 50000 else text
    except Exception as e:
        return f"⚠️ URL抓取失败：{str(e)}"


async def extract_text(file_path: str, source_type: str) -> str:
    """统一的文本提取入口"""
    if source_type == "pdf":
        return await extract_text_from_pdf(file_path)
    elif source_type == "epub":
        return await extract_text_from_epub(file_path)
    elif source_type == "md":
        return await extract_text_from_md(file_path)
    elif source_type == "txt":
        return await extract_text_from_txt(file_path)
    elif source_type == "url":
        return await extract_text_from_url(file_path)
    else:
        return ""


# ==================== 三级回退分章 ====================

# 常见章节标题正则
CHAPTER_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千零\d]+[章编节讲课篇部卷][\s：:\.\．]*", re.MULTILINE),
    re.compile(r"^Chapter\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\d+\.\d+\s+", re.MULTILINE),  # 1.1 标题
    re.compile(r"^Part\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Lesson\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Section\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
]

# Markdown标题正则
MD_HEADING = re.compile(r"^#{2,4}\s+(.+)", re.MULTILINE)

# HTML标题正则
HTML_HEADING = re.compile(r"<h[234][^>]*>(.+?)</h[234]>", re.IGNORECASE | re.DOTALL)


async def _level1_llm_chunk(text: str) -> Optional[List[Tuple[str, str]]]:
    """第一级：LLM智能识别章节"""
    prompt = f"""你是一个教材分章助手。请分析以下教材文本，识别出所有章节。

返回JSON格式：
{{"chapters": [{{"title": "章节标题", "content": "该章节的完整文本内容"}}]}}

要求：
1. 识别"第X章"、"Chapter X"、"1.1"等章节标题
2. 如果文本没有明显分章，返回单章节
3. 保持内容的完整性，不要截断

教材文本：
{text[:8000]}  # LLM输入长度限制
"""
    try:
        result = await llm.chat_json([
            {"role": "system", "content": "你是专业的教材分章助手。只返回JSON。"},
            {"role": "user", "content": prompt},
        ])
        chapters = result.get("chapters", [])
        if chapters and len(chapters) > 1:
            return [(ch["title"], ch["content"]) for ch in chapters]
        return None
    except Exception:
        return None


def _level2_markdown_chunk(text: str, md_heading_re: re.Pattern) -> Optional[List[Tuple[str, str]]]:
    """第二级：按Markdown/HTML标题分章"""
    matches = list(md_heading_re.finditer(text))
    if len(matches) < 2:
        return None

    chapters = []
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            chapters.append((title, content))

    return chapters if len(chapters) >= 1 else None


def _level2_html_chunk(text: str) -> Optional[List[Tuple[str, str]]]:
    """第二级备用：按HTML标题分章"""
    matches = list(HTML_HEADING.finditer(text))
    if len(matches) < 2:
        return None

    chapters = []
    for i, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            from bs4 import BeautifulSoup
            content_text = BeautifulSoup(content, "lxml").get_text(strip=True)
            chapters.append((title, content_text))

    return chapters if len(chapters) >= 1 else None


def _level3_force_chunk(text: str, chunk_size: int = 2000) -> List[Tuple[str, str]]:
    """第三级：暴力按token数切段"""
    # 按段落切分
    paragraphs = text.split("\n\n")
    chapters = []
    current_chunk = []
    current_len = 0
    chunk_index = 1

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_len = len(para)
        if current_len + para_len > chunk_size and current_chunk:
            content = "\n\n".join(current_chunk)
            chapters.append((f"段落 {chunk_index}", content))
            current_chunk = [para]
            current_len = para_len
            chunk_index += 1
        else:
            current_chunk.append(para)
            current_len += para_len

    if current_chunk:
        content = "\n\n".join(current_chunk)
        chapters.append((f"段落 {chunk_index}", content))

    return chapters


async def smart_chunk(text: str) -> List[Tuple[str, str]]:
    """
    三级回退分章
    返回 [(title, content), ...]
    """
    if not text or len(text.strip()) < 20:
        return [("全文", text or "（空内容）")]

    # 第一级：LLM智能识别
    chapters = await _level1_llm_chunk(text)
    if chapters and len(chapters) > 1:
        return chapters

    # 第二级：Markdown标题
    chapters = _level2_markdown_chunk(text, MD_HEADING)
    if chapters and len(chapters) > 1:
        return chapters

    # 第二级备用：HTML标题
    chapters = _level2_html_chunk(text)
    if chapters and len(chapters) > 1:
        return chapters

    # 第二级备用：章节关键词
    for pattern in CHAPTER_PATTERNS:
        matches = list(pattern.finditer(text))
        if len(matches) > 1:
            chapters = []
            for i, match in enumerate(matches):
                title = match.group(0).strip()
                start = match.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                content = text[start:end].strip()
                chapters.append((title, content[:5000]))
            return chapters

    # 第三级：暴力切段
    chapters = _level3_force_chunk(text)
    return chapters


async def generate_syllabus_items(course_id: str, chapters: list) -> list:
    """
    为每个章节生成3~5条掌握项
    返回 [(chapter_index, description), ...]
    """
    all_items = []
    for idx, (ch_title, ch_content) in enumerate(chapters):
        prompt = f"""根据以下教材章节内容，生成3-5条掌握项清单（检查学习者是否真正掌握了该章节的关键知识点）。

每条掌握项以"能..."开头，使用可验证的行为描述。

章节标题：{ch_title}
章节内容：
{ch_content[:1500]}

返回JSON格式：
{{"items": ["能用自己的话复述...", "能解释...", ...]}}
"""
        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是课程设计专家。返回JSON。"},
                {"role": "user", "content": prompt},
            ])
            items = result.get("items", [])
            for item in items:
                all_items.append((idx, item))
        except Exception:
            # 降级生成通用掌握项
            defaults = [
                f"能用自己的话复述{ch_title}的核心内容",
                f"能解释{ch_title}中的关键概念",
                f"能将{ch_title}的知识点与实际应用联系起来",
            ]
            for item in defaults:
                all_items.append((idx, item))

    return all_items


async def generate_course_summary(text: str) -> str:
    """生成课程摘要"""
    prompt = f"""根据以下教材内容，生成一段简洁的课程摘要（100字以内）：

{text[:2000]}"""
    return await llm.chat([
        {"role": "system", "content": "你是课程摘要生成专家。"},
        {"role": "user", "content": prompt},
    ], temperature=0.3, max_tokens=200)
