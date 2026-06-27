"""
问渠（Wenqu）v1.1 四级分章引擎 —— TOC-First EPUB 解析

支持 PDF/EPUB/MD/TXT/URL 文本提取和智能分章
EPUB 优先从目录文件（nav / toc.ncx）提取结构化章节，按条目切割正文
"""

import re
import io
import zipfile
import asyncio
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any
from urllib.parse import urlparse

import httpx
import aiofiles

from llm_client import llm

# ==================== EPUB TOC 提取 ====================

async def extract_toc_from_epub(file_path: str) -> tuple:
    """
    从 EPUB 提取结构化目录和 href→正文映射。
    返回 (toc_items, href_content_map)

    toc_items: [{"idx": 0, "title": "第一章", "href": "chap1.xhtml",
                  "children": [{"idx": 1, "title": "1.1", "href": ...}, ...]}, ...]
    href_content_map: {"chap1.xhtml": "纯文本正文..."}
    """
    from xml.etree import ElementTree as ET

    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            # 1. 读取 META-INF/container.xml 找到 .opf
            opf_path = _find_opf_path(z)
            if not opf_path:
                return [], {}

            # 2. 从 .opf 获取所有 manifest 条目（href → media-type）
            opf_content = z.read(opf_path).decode('utf-8', errors='ignore')
            opf_dir = str(Path(opf_path).parent) + "/" if Path(opf_path).parent else ""
            manifest = _parse_opf_manifest(opf_content, opf_dir)

            # 3. 优先从 nav 提取 TOC
            toc_items = _parse_nav_toc(z, manifest)

            # 4. 兜底：从 toc.ncx 提取
            if not toc_items:
                toc_items = _parse_ncx_toc(z, manifest)

            # 5. 三级兜底：从 spine 顺序 + 正文 h1/h2 推断
            if not toc_items:
                toc_items = _infer_toc_from_headings(z, manifest)

            # 6. 提取所有正文 href 对应的文本
            href_content_map = _extract_all_content(z, manifest)

            # 7.  resolve TOC item hrefs: nav 中的 href 可能是相对路径，
            #    需要解析为相对于 nav 文件或 opf 目录的完整路径
            if toc_items and href_content_map:
                toc_items = _resolve_toc_hrefs(toc_items, manifest)

            return toc_items, href_content_map
    except Exception as e:
        return [], {}


def _find_opf_path(z: zipfile.ZipFile) -> Optional[str]:
    """从 META-INF/container.xml 读取 .opf 路径"""
    try:
        container = z.read("META-INF/container.xml").decode('utf-8', errors='ignore')
        m = re.search(r'full-path="([^"]+)"', container)
        return m.group(1) if m else None
    except KeyError:
        return None


def _parse_opf_manifest(opf_content: str, opf_dir: str) -> dict:
    """解析 .opf 的 manifest，返回 {href: media_type}"""
    manifest = {}
    # 用正则提取（避免xml命名空间问题）
    # 适配两种顺序: href="..." media-type="..." 或 media-type="..." href="..."
    for m in re.finditer(r'<item[^>]*href="([^"]+)"[^>]*media-type="([^"]+)"', opf_content):
        href = m.group(1)
        if not href.startswith('/') and opf_dir and opf_dir not in ('', './'):
            href = opf_dir + href
        manifest[href] = m.group(2)

    # 补漏：如果上面的正则没匹配到（media-type 在 href 之前），尝试另一种顺序
    if not manifest:
        for m in re.finditer(r'<item[^>]*media-type="([^"]+)"[^>]*href="([^"]+)"', opf_content):
            href = m.group(2)
            if not href.startswith('/') and opf_dir and opf_dir not in ('', './'):
                href = opf_dir + href
            manifest[href] = m.group(1)

    # 也解析 spine 顺序
    spine_order = []
    for m in re.finditer(r'<itemref[^>]*idref="([^"]+)"', opf_content):
        spine_order.append(m.group(1))

    return {"items": manifest, "spine": spine_order}


def _html_to_text(html: str) -> str:
    """将 HTML 片段转为纯文本"""
    text = html
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</?p[^>]*>', '\n', text)
    text = re.sub(r'</?h[1-6][^>]*>', '\n### ', text)
    text = re.sub(r'<li[^>]*>', '\n- ', text)
    text = re.sub(r'</?li[^>]*>', '', text)
    text = re.sub(r'</?ul[^>]*>|</?ol[^>]*>', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    # HTML entities
    entities = {
        '&amp;': '&', '&lt;': '<', '&gt;': '>',
        '&quot;': '"', '&#39;': "'", '&apos;': "'",
        '&nbsp;': ' ', '&ndash;': '-', '&mdash;': '—',
    }
    for k, v in entities.items():
        text = text.replace(k, v)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def _parse_nav_toc(z: zipfile.ZipFile, manifest: dict) -> list:
    """从 EPUB3 的 nav 标签提取目录"""
    items = manifest.get("items", {})
    nav_href = None

    # 找 nav 文件（通常标记为 nav 或 toc）
    for href, mtype in items.items():
        if 'nav' in href.lower() or mtype == 'application/xhtml+xml+nav':
            nav_href = href
            break

    if not nav_href:
        # 遍历所有 xhtml 找 nav 元素
        for href in items:
            if href.endswith(('.xhtml', '.html')):
                try:
                    content = z.read(href).decode('utf-8', errors='ignore')
                    if '<nav' in content.lower():
                        nav_href = href
                        break
                except Exception:
                    continue

    if not nav_href:
        return []

    try:
        content = z.read(nav_href).decode('utf-8', errors='ignore')
        # 提取 <nav> 中的目录列表
        toc = _extract_nav_items(content)
        return toc
    except Exception:
        return []


def _extract_nav_items(html: str) -> list:
    """从 nav html 中提取目录项"""
    items = []

    # 首先查找 epub:type="toc" 的 nav 元素
    # 使用平衡标签方法找到正确的 <nav>...</nav> 边界
    def find_nav_section(text: str, start_pos: int) -> str:
        """从 start_pos 找到对应的 </nav>（平衡嵌套标签）"""
        nav_start = text.find('<nav', start_pos)
        if nav_start == -1:
            return ""
        # 找到同一个 nav 的闭合 </nav>
        depth = 1
        i = nav_start + 4
        while i < len(text) and depth > 0:
            open_tag = text.find('<nav', i)
            close_tag = text.find('</nav>', i)
            if close_tag == -1:
                return ""
            if open_tag != -1 and open_tag < close_tag:
                depth += 1
                i = open_tag + 4
            else:
                depth -= 1
                i = close_tag + 6
        return text[nav_start:i] if depth == 0 else ""

    # 尝试优先找 epub:type="toc"（必须在 <nav> 标签上，而非 <a> 内）
    nav_content = ""
    for nav_attr_match in re.finditer(r'<nav[^>]*epub:type="toc"', html):
        nav_content = find_nav_section(html, nav_attr_match.start())
        if nav_content:
            break

    # 兜底：找含链接最多的 <nav>
    if not nav_content:
        sections = []
        i = 0
        while True:
            ns = html.find('<nav', i)
            if ns == -1:
                break
            section = find_nav_section(html, ns)
            if section:
                sections.append(section)
            i = ns + 4 if not section else html.find('</nav>', ns) + 6

        # 选链接最多的
        best = max(sections, key=lambda s: len(re.findall(r'<a\s', s))) if sections else ""
        if best and len(re.findall(r'<a\s', best)) >= 2:
            nav_content = best

    if not nav_content:
        return []

    # 提取 <a href="...">title</a>
    seen_hrefs = set()
    idx = 0

    for a_match in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', nav_content, re.IGNORECASE):
        href = a_match.group(1).split('#')[0]  # 去掉锚点
        title = re.sub(r'<[^>]+>', '', a_match.group(2)).strip()

        if not title or not href:
            continue

        # 去重（某些 EPUB 在 nav 中重复列出）
        dedup_key = f"{href}::{title}"
        if dedup_key in seen_hrefs:
            continue
        seen_hrefs.add(dedup_key)

        # 检测层级（通过父级 li 的嵌套深度）
        before = html[:a_match.start()]
        li_depth = before.count('<ol') - before.count('</ol')
        li_depth = max(0, li_depth)

        items.append({
            "idx": idx,
            "title": title,
            "href": href,
            "level": li_depth,
            "parent_idx": -1,  # 稍后处理
        })
        idx += 1

    return _assign_parents(items)


def _resolve_toc_hrefs(toc_items: list, manifest: dict) -> list:
    """
    将 TOC 项中的 href 相对路径解析为 content_map 中的绝对路径。
    nav 中的 href 通常是相对路径（如 "Text/chap1.xhtml"），
    需要拼接 nav 所在目录或 opf 目录。
    """
    known_hrefs = set(manifest.get("items", {}).keys())
    if not known_hrefs:
        return toc_items

    for item in toc_items:
        raw_href = item.get("href", "")
        if not raw_href:
            continue
        # 如果已匹配则跳过
        if raw_href in known_hrefs:
            continue
        # 尝试拼接各种可能的相对路径前缀
        # 1. 如果 raw_href 不含 '/'，尝试所有前缀
        if "/" not in raw_href:
            for kh in known_hrefs:
                if kh.endswith("/" + raw_href) or kh.endswith("\\" + raw_href):
                    item["href"] = kh
                    break
        else:
            # 2. 尝试匹配最后一段文件名
            basename = raw_href.split("/")[-1]
            for kh in known_hrefs:
                if kh.endswith("/" + basename) or kh == basename:
                    item["href"] = kh
                    break

    return toc_items


def _assign_parents(items: list) -> list:
    """根据 level 字段填充 parent_idx"""
    if not items:
        return items
    stack = [(-1, -1)]  # (level, idx)
    for item in items:
        lvl = item["level"]
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        item["parent_idx"] = stack[-1][1] if stack else -1
        stack.append((lvl, item["idx"]))
    return items


def _parse_ncx_toc(z: zipfile.ZipFile, manifest: dict) -> list:
    """从 EPUB2 的 toc.ncx 提取目录（兼容命名空间前缀）"""
    items = manifest.get("items", {})
    ncx_href = None
    for href, mtype in items.items():
        if 'toc' in href.lower() and href.endswith('.ncx'):
            ncx_href = href
            break
    if not ncx_href:
        for href in items:
            if href.endswith('.ncx'):
                ncx_href = href
                break

    if not ncx_href:
        return []

    try:
        content = z.read(ncx_href).decode('utf-8', errors='ignore')
        toc = []
        idx = 0
        # 兼容有无命名空间前缀（如 ns0:navPoint, navPoint）
        seen_hrefs = set()
        for nav_match in re.finditer(
            r'<(?:[\w]+:)?navPoint[^>]*>.*?<(?:[\w]+:)?navLabel>.*?<(?:[\w]+:)?text>(.*?)</(?:[\w]+:)?text>.*?<(?:[\w]+:)?content[^>]*src="([^"]+)"',
            content, re.DOTALL | re.IGNORECASE
        ):
            href = nav_match.group(2).split('#')[0]
            title = nav_match.group(1).strip()
            if title and href not in seen_hrefs:
                seen_hrefs.add(href)
                toc.append({"idx": idx, "title": title, "href": href, "level": 0, "parent_idx": -1})
                idx += 1
        return toc
    except Exception:
        return []


def _infer_toc_from_headings(z: zipfile.ZipFile, manifest: dict) -> list:
    """兜底：从正文文件的 h1/h2 推断目录"""
    items = manifest.get("items", {})
    spine = manifest.get("spine", [])
    toc = []
    idx = 0
    seen_titles = set()

    # 按 spine 顺序处理
    spine_hrefs = []
    for idref in spine:
        for href, _ in items.items():
            if idref in href or idref in str(href):
                spine_hrefs.append(href)
                break

    if not spine_hrefs:
        spine_hrefs = sorted(h for h in items if h.endswith(('.xhtml', '.html')))

    for href in spine_hrefs:
        try:
            content = z.read(href).decode('utf-8', errors='ignore')
            # 取第一个 h1/h2
            h_match = re.search(r'<h([12])[^>]*>(.*?)</h\1>', content, re.IGNORECASE | re.DOTALL)
            if h_match:
                title = re.sub(r'<[^>]+>', '', h_match.group(2)).strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    toc.append({"idx": idx, "title": title, "href": href, "level": 0, "parent_idx": -1})
                    idx += 1
        except Exception:
            continue

    return toc


def _extract_all_content(z: zipfile.ZipFile, manifest: dict) -> dict:
    """提取 manifest 中所有正文 href 对应的纯文本"""
    items = manifest.get("items", {})
    content_map = {}

    for href, mtype in items.items():
        if not href.endswith(('.xhtml', '.html')):
            continue
        if 'nav' in href.lower() or 'toc' in href.lower() or 'cover' in href.lower():
            continue
        try:
            html_content = z.read(href).decode('utf-8', errors='ignore')
            text = _html_to_text(html_content)
            if text:
                content_map[href] = text
        except Exception:
            continue

    return content_map


async def extract_text_from_epub(file_path: str) -> str:
    """从EPUB提取完整文本（兼容旧接口）"""
    toc_items, content_map = await extract_toc_from_epub(file_path)

    # 按目录顺序拼接
    parts = []
    if toc_items and content_map:
        for item in toc_items:
            text = content_map.get(item["href"], "")
            if text:
                parts.append(f"### {item['title']}\n\n{text}")

    if parts:
        return '\n\n---\n\n'.join(parts)

    # 兜底：TOC-First 失败时回退到全量提取（兼容所有 EPUB）
    if not parts:
        import zipfile
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
                        text = _html_to_text(content)
                        if text:
                            texts.append(text)
                    except Exception:
                        continue
                if texts:
                    return '\n\n---\n\n'.join(texts)
        except Exception:
            pass

    return ""


async def extract_chapter_content_by_href(file_path: str, href: str) -> str:
    """懒加载：仅提取 EPUB 中单个 href 对应的正文"""
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            content = z.read(href).decode('utf-8', errors='ignore')
            return _html_to_text(content)
    except Exception:
        return ""


# ==================== 其他文本提取 ====================

async def extract_text_from_pdf(file_path: str) -> str:
    """从PDF提取文本"""
    return ""


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
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "lxml")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                body = soup.find("article") or soup.find("main") or soup.find("body")
                if body:
                    text = body.get_text(separator="\n", strip=True)
                    if len(text) > 100:
                        return text
                text = soup.get_text(separator="\n", strip=True)
                return text[:50000] if len(text) > 50000 else text
            except ImportError:
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


# ==================== TOC-First 分章 ====================

async def smart_chunk_from_epub(file_path: str, reading_mode: str = "standard") -> List[Tuple[str, str, dict]]:
    """
    EPUB TOC-First 分章
    返回 [(title, content, meta_dict), ...]
    meta_dict 包含 href, idx, parent_idx, level, sort_order 等
    """
    toc_items, content_map = await extract_toc_from_epub(file_path)

    if not toc_items or not content_map:
        return []

    chapters = []
    prev_href = None
    combined_text = ""
    combined_meta = None
    combined_title_parts = []

    def flush_combined():
        """将累积的合并章节写入 chapters"""
        nonlocal combined_text, combined_meta, combined_title_parts
        if combined_meta is not None and combined_text:
            # 合并标题：用 " | " 连接短标题，去除副标题类
            if len(combined_title_parts) > 1:
                short_titles = [t for t in combined_title_parts if len(t) <= 20 and not t.startswith('ETF')]
                if short_titles:
                    title = " | ".join(short_titles)
                else:
                    title = combined_title_parts[0]
            else:
                title = combined_title_parts[0]

            meta = combined_meta
            if reading_mode == "speed":
                preview = _sample_first_last(combined_text, 300)
                chapters.append((title, preview, meta))
            elif reading_mode == "deep":
                chapters.append((title, combined_text, meta))
            else:
                chapters.append((title, combined_text[:5000], meta))

        combined_text = ""
        combined_meta = None
        combined_title_parts = []

    for item in toc_items:
        href = item["href"]
        text = content_map.get(href, "")

        # 同 href 则合并（同文件内有多个 H1/H2）
        if href == prev_href:
            combined_title_parts.append(item["title"])
            continue

        # 不同 href：刷出上一个
        flush_combined()

        meta = {
            "href": href,
            "idx": item["idx"],
            "parent_idx": item.get("parent_idx", -1),
            "level": item.get("level", 0),
            "sort_order": str(item["idx"]),
        }
        combined_text = text
        combined_meta = meta
        combined_title_parts = [item["title"]]
        prev_href = href

    # 刷出最后一个
    flush_combined()

    return chapters


def _sample_first_last(text: str, max_per_sample: int = 300) -> str:
    """取文本的首段和尾段作为预览"""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return text[:max_per_sample]
    if len(paragraphs) == 1:
        return paragraphs[0][:max_per_sample]

    first = paragraphs[0][:max_per_sample]
    last = paragraphs[-1][:max_per_sample]
    return f"{first}\n\n...（中间内容略）...\n\n{last}"


# ==================== 三级回退分章（非 EPUB） ====================

CHAPTER_PATTERNS = [
    re.compile(r"^第[一二三四五六七八九十百千零\d]+[章编节讲课篇部卷][\s：:\.\．]*", re.MULTILINE),
    re.compile(r"^Chapter\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\d+\.\d+\s+", re.MULTILINE),
    re.compile(r"^Part\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Lesson\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Section\s+\d+[\s:：\.\．]*", re.IGNORECASE | re.MULTILINE),
]

MD_HEADING = re.compile(r"^#{2,4}\s+(.+)", re.MULTILINE)
HTML_HEADING = re.compile(r"<h[234][^>]*>(.+?)</h[234]>", re.IGNORECASE | re.DOTALL)


async def _level1_llm_chunk(text: str) -> Optional[List[Tuple[str, str, dict]]]:
    """第一级：LLM智能识别章节"""
    prompt = f"""你是一个教材分章助手。请分析以下教材文本，识别出所有章节。

返回JSON格式：
{{"chapters": [{{"title": "章节标题", "content": "该章节的完整文本内容"}}]}}

要求：
1. 识别"第X章"、"Chapter X"、"1.1"等章节标题
2. 如果文本没有明显分章，返回单章节
3. 保持内容的完整性，不要截断

教材文本：
{text[:8000]}
"""
    try:
        result = await llm.chat_json([
            {"role": "system", "content": "你是专业的教材分章助手。只返回JSON。"},
            {"role": "user", "content": prompt},
        ])
        chapters = result.get("chapters", [])
        if chapters and len(chapters) > 1:
            return [(ch["title"], ch["content"], {"idx": i, "level": 0, "parent_idx": -1, "sort_order": str(i)})
                    for i, ch in enumerate(chapters)]
        return None
    except Exception:
        return None


def _level2_markdown_chunk(text: str, md_heading_re: re.Pattern) -> Optional[List[Tuple[str, str, dict]]]:
    """第二级：按 Markdown/HTML 标题分章"""
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
            chapters.append((title, content, {"idx": i, "level": 0, "parent_idx": -1, "sort_order": str(i)}))
    return chapters if len(chapters) >= 1 else None


def _level2_html_chunk(text: str) -> Optional[List[Tuple[str, str, dict]]]:
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
            chapters.append((title, content_text, {"idx": i, "level": 0, "parent_idx": -1, "sort_order": str(i)}))
    return chapters if len(chapters) >= 1 else None


def _level3_force_chunk(text: str, chunk_size: int = 2000) -> List[Tuple[str, str, dict]]:
    """第三级：暴力按token数切段"""
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
            chapters.append((f"段落 {chunk_index}", content,
                             {"idx": chunk_index - 1, "level": 0, "parent_idx": -1, "sort_order": str(chunk_index - 1)}))
            current_chunk = [para]
            current_len = para_len
            chunk_index += 1
        else:
            current_chunk.append(para)
            current_len += para_len
    if current_chunk:
        content = "\n\n".join(current_chunk)
        chapters.append((f"段落 {chunk_index}", content,
                         {"idx": chunk_index - 1, "level": 0, "parent_idx": -1, "sort_order": str(chunk_index - 1)}))
    return chapters


async def smart_chunk(text: str, source_type: str = "", file_path: str = "", reading_mode: str = "standard") -> List[Tuple[str, str, dict]]:
    """
    增强版分章引擎

    对 EPUB：走 TOC-First 路径，直接按目录条目分章
    对非 EPUB：保持三级回退

    返回 [(title, content, meta_dict), ...]
    meta_dict: {"idx", "parent_idx", "level", "sort_order", "href"?}
    """
    if not text or len(text.strip()) < 20:
        return [("全文", text or "（空内容）", {"idx": 0, "level": 0, "parent_idx": -1, "sort_order": "0"})]

    # EPUB 走 TOC-First 路径
    if source_type == "epub" and file_path:
        epubs = await smart_chunk_from_epub(file_path, reading_mode)
        if epubs:
            return epubs

    # 非 EPUB 三级回退
    chapters = await _level1_llm_chunk(text)
    if chapters and len(chapters) > 1:
        return chapters

    chapters = _level2_markdown_chunk(text, MD_HEADING)
    if chapters and len(chapters) > 1:
        return chapters

    chapters = _level2_html_chunk(text)
    if chapters and len(chapters) > 1:
        return chapters

    for pattern in CHAPTER_PATTERNS:
        matches = list(pattern.finditer(text))
        if len(matches) > 1:
            chs = []
            for i, match in enumerate(matches):
                title = match.group(0).strip()
                start = match.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
                content = text[start:end].strip()
                chs.append((title, content[:5000], {"idx": i, "level": 0, "parent_idx": -1, "sort_order": str(i)}))
            return chs

    return _level3_force_chunk(text)


# ==================== 掌握项生成（支持分级） ====================

async def generate_syllabus_items(course_id: str, chapters: list) -> list:
    """
    为每个章节生成 1~3 条掌握项
    返回 [(chapter_index, description), ...]
    """
    all_items = []
    for idx, item in enumerate(chapters):
        if isinstance(item, tuple) and len(item) >= 2:
            ch_title, ch_content = item[0], item[1]
        elif isinstance(item, dict):
            ch_title = item.get("title", "")
            ch_content = item.get("content_slice", item.get("content", ""))
        else:
            continue

        prompt = f"""根据以下教材章节内容，生成1-3条掌握项清单（检查学习者是否真正掌握了该章节的关键知识点）。

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
            for item_text in items:
                all_items.append((idx, item_text))
        except Exception:
            defaults = [
                f"能用自己的话复述{ch_title}的核心内容",
                f"能解释{ch_title}中的关键概念",
            ]
            for d in defaults:
                all_items.append((idx, d))

    return all_items


async def generate_course_summary(text: str) -> str:
    """生成课程摘要"""
    prompt = f"""根据以下教材内容，生成一段简洁的课程摘要（100字以内）：

{text[:2000]}"""
    return await llm.chat([
        {"role": "system", "content": "你是课程摘要生成专家。"},
        {"role": "user", "content": prompt},
    ], temperature=0.3, max_tokens=200)
