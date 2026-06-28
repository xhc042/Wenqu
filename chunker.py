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

from llm_client import llm, multi_llm

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
            title = item.get("title", "")
            # 跳过元数据章节（序言/前言/自序/附录等）
            if is_non_core_chapter(title):
                continue
            text = content_map.get(item["href"], "")
            if text:
                parts.append(f"### {title}\n\n{text}")

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
                    raw_title = " | ".join(short_titles)
                else:
                    raw_title = combined_title_parts[0]
            else:
                raw_title = combined_title_parts[0]

            # 清理标题，提升可读性
            title = _clean_chapter_title(raw_title, combined_text)

            # 跳过元数据章节（序言/前言/自序/附录等）
            if is_non_core_chapter(title):
                combined_text = ""
                combined_meta = None
                combined_title_parts = []
                return

            meta = combined_meta
            if reading_mode == "speed":
                preview = _sample_first_last(combined_text, 300)
                chapters.append((title, preview, meta))
            elif reading_mode == "deep":
                chapters.append((title, combined_text, meta))
            else:
                # 细读模式：使用均匀采样，保证全书代表性
                sampled_content = sample_chapter_fairly(combined_text, 5000)
                chapters.append((title, sampled_content, meta))

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


def _clean_chapter_title(title: str, content: str = "") -> str:
    """
    清理章节标题，提升可读性
    1. 去除无意义的前缀（如数字编号）
    2. 如果标题太短或无意义，从正文中提取
    3. 特殊处理"段落N"等暴力分章标题
    """
    if not title:
        return "未命名章节"

    # 清理HTML实体
    title = title.replace('&nbsp;', ' ').replace('&amp;', '&')

    # 检查标题是否太短或只有数字（无意义）
    import re
    # 匹配无意义的标题格式
    is_meaningless = (
        len(title.strip()) < 2 or  # 太短
        re.match(r'^[\d\s\.\-]+$', title.strip()) or  # 只有数字和符号
        re.match(r'^第\s*\d+\s*章?\s*$', title.strip()) or  # 只有"第X章"
        re.match(r'^(chapter|ch|section|sec)\s*\d+$', title.strip(), re.IGNORECASE) or  # 只有英文章节号
        re.match(r'^段落\s*\d+$', title.strip()) or  # "段落N"格式
        re.match(r'^(chunk|part|block)\s*\d+$', title.strip(), re.IGNORECASE)  # "chunk N"等
    )

    if is_meaningless and content:
        # 尝试从正文中提取第一句有意义的句子作为标题
        paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
        for para in paragraphs[:3]:  # 最多尝试前3段
            # 取第一句（不超过40字，给语录类内容更多空间）
            sentences = re.split(r'[。.!？\n]', para)
            for s in sentences:
                s = s.strip()
                if len(s) >= 8 and len(s) <= 40:
                    # 去除可能的引号和编号
                    s = re.sub(r'^["""\'"\d\.]+', '', s).strip()
                    if len(s) >= 5:  # 清理后至少还有5个字
                        return s
        return f"第{title.strip()}节" if title.strip() else "未命名章节"

    # 清理多余的空白
    title = re.sub(r'\s+', ' ', title.strip())
    return title if len(title) <= 50 else title[:47] + '...'


def _sample_first_last(text: str, max_per_sample: int = 300) -> str:
    """速读模式章节内容采样

    - 多段：取首段 + 尾段
    - 单段：退化为均匀采样（避免信息丢失）

    修复 P1-③：EPUB 整章塞进单个 <p> 的场景很多，原实现只取前 300 字，
    丢失 99% 内容。改为退化为 sample_chapter_fairly 均匀采样。
    """
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return text[:max_per_sample]

    if len(paragraphs) == 1:
        # 单段场景：用均匀采样保留头尾 + 中段
        # 目标 max_per_sample * 2 字（≈600 字）
        sampled = sample_chapter_fairly(text, target_chars=max_per_sample * 2)
        return sampled

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


# ==================== 速读模式：知识快照提取 ====================

import re as _re

# 元数据章节模式：序言、前言、自序、目录、附录等不应该作为核心章节
NON_CORE_TITLE_PATTERNS = [
    _re.compile(r'^(推荐序|序言|前言|自序|后记|跋|致谢|目录|引言|引子|楔子|写在前面|编者按|内容简介|作者简介|书籍简介|图书简介|出版说明)'),
    _re.compile(r'^(附录|附录[一二三四五六七八九十]|附\s*[一二三四五六七八九十])'),
    _re.compile(r'^(参考文献|参考资料|推荐阅读|延伸阅读|书目|索引)'),
    _re.compile(r'^(序)$|^(序[一二三四五六七八九十])$'),
    # 版权相关（P3-③：导入时跳过版权页）
    _re.compile(r'^(版权声明|版权信息|版权页|声明|著作权)'),
    _re.compile(r'^(出版许可证|版号)'),
    # 数据引用/参考资料类（P3-③补充：结语、数据引用说明等辅助性章节）
    _re.compile(r'^(数据引用说明|资料来源说明|引用说明|数据来源|参考文献列表|参考文献目录)'),
    _re.compile(r'^(勘误表|修订说明|修订版说明|再版说明|增订说明|增补说明)'),
]

# P3-①: 关键词白名单（标题包含这些词也算元数据章节）
# 注意：未包含"引言"——避免误判正文章节"第一章 引言"等
NON_CORE_TITLE_KEYWORDS = {
    "序", "跋", "后记", "致谢", "鸣谢", "简介", "提要", "凡例",
    "出版说明", "写在前面", "编者按", "声明", "告白", "代序",
    "引子", "楔子", "前言", "序言", "自序", "卷首语",
    # P3-③：版权相关（仅"著作权"用关键词匹配，"版权"相关仅通过前缀模式跳过）
    "著作权",
    # P3-③补充：数据引用/参考资料类关键词
    # 仅加足够专精的词，避免误判正文章节"第一章 数据引用方法"等
    "勘误表", "修订说明", "再版说明", "增订说明",
}


def is_non_core_chapter(title: str) -> bool:
    """
    判断章节是否为元数据章节（序言/前言/自序/附录等）
    这些章节不应作为核心章节推荐

    P3-①: 在原前缀匹配基础上，叠加关键词白名单（"卷首语" / "代序" / "第N章 序论" 等）
    """
    if not title:
        return True
    title = title.strip()

    # 1. 现有前缀匹配
    for pat in NON_CORE_TITLE_PATTERNS:
        if pat.match(title):
            return True

    # 2. 关键词白名单
    for kw in NON_CORE_TITLE_KEYWORDS:
        if kw in title:
            return True

    return False


async def extract_chapter_snapshot(text: str, title: str) -> dict:
    """
    用LLM提取每章知识快照
    返回扩展结构（含学习目标、重要性、前置依赖等）

    注意：使用 llm 单例（与对话学习一致），而不是 multi_llm.balanced
    这样快照生成能直接复用用户配置的主模型，不需要额外配置 balanced 层级
    """
    if not text or not text.strip():
        return {
            "keywords": [], "core_viewpoint": "",
            "learning_goal": "", "importance": 0, "difficulty": "未知"
        }

    is_meta = is_non_core_chapter(title)

    if is_meta:
        # 元数据章节：用更短的 prompt 并强制 importance 较低
        # P3-③：版权/声明类章节也属于元数据，不作为核心知识
        prompt = f"""你是读书导师。这是书的元数据章节（序言/前言/自序/推荐序/版权声明/附录等），它的作用是介绍背景而非传递核心知识。

章节：{title}
内容：{text[:1000]}

请生成：
1. **keywords** - 1-3个最相关的词（不要超过3个）
2. **core_viewpoint** - 1句话概括本章主题（不超过25字）
3. **learning_goal** - 用"能..."描述（必须是可以观察的具体动作，比如"能说出XXX的3个核心观点"或"能列出XXX的5个要点"）
4. **importance** - 重要性：1-2（这种章节通常1，不是核心内容）
5. **difficulty** - 难度：简单/中等/较难

返回严格JSON：
{{"keywords": ["词1"], "core_viewpoint": "...", "learning_goal": "能...", "importance": 1, "difficulty": "简单"}}"""
    else:
        prompt = f"""你是读书导师，为读者提炼本章的学习价值。

章节：{title}
内容：{text[:2000]}

请生成：
1. **keywords** - 3-5个关键词（中文术语，书籍核心概念）
2. **core_viewpoint** - 一句话核心观点（不超过30字）
3. **learning_goal** - 学习目标：用"能+具体动词"描述可观察的具体能力（不超过25字）
   - ✅ 好例子："能列出ETF的5种投资策略"、"能解释ETF的运作机制"、"能区分主动型与被动型ETF"
   - ❌ 坏例子："能理解ETF"、"能掌握ETF知识"、"能了解XXX"
4. **importance** - 重要性：1-5的整数（5=全书最核心，正文最关键章节）
5. **difficulty** - 难度：简单/中等/较难

返回严格JSON（不要任何其他内容）：
{{"keywords": ["关键词1", "关键词2"], "core_viewpoint": "一句话观点", "learning_goal": "能+具体动作", "importance": 3, "difficulty": "中等"}}"""

    # 重试机制：最多3次（使用 llm 单例，与对话学习保持一致）
    last_error = None
    for attempt in range(3):
        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是知识提炼专家。严格返回JSON，不要任何其他文字。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3)

            # 字段解析与验证
            keywords = result.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            keywords = [str(k) for k in keywords[:5] if k]  # 过滤空值

            core_viewpoint = str(result.get("core_viewpoint", ""))[:100]
            learning_goal = str(result.get("learning_goal", ""))[:80]

            importance = result.get("importance", 3)
            if not isinstance(importance, (int, float)):
                importance = 3
            importance = max(1, min(5, int(importance)))

            # 元数据章节（序言/前言/自序等）强制 importance <= 2
            if is_meta:
                importance = min(importance, 2)

            difficulty = str(result.get("difficulty", "中等"))
            if difficulty not in ("简单", "中等", "较难"):
                difficulty = "中等"

            # 修正 learning_goal：检测抽象词
            abstract_words = ['理解', '掌握', '了解', '知道', '熟悉']
            if any(w in learning_goal for w in abstract_words) and not is_meta:
                # 抽象词开头，加具体动作提示
                pass  # 不强行修改，让 prompt 引导

            # 必须有内容才算成功
            if keywords or core_viewpoint:
                return {
                    "keywords": keywords,
                    "core_viewpoint": core_viewpoint,
                    "learning_goal": learning_goal,
                    "importance": importance,
                    "difficulty": difficulty,
                }
            else:
                last_error = "LLM返回了空数据"
        except Exception as e:
            last_error = f"LLM调用失败: {e}"
            import logging
            logging.warning(f"快照生成第{attempt+1}次尝试失败 [{title[:20]}]: {e}")

    # 全部失败：使用文本回退（确保有内容而不是空）
    import logging
    logging.error(f"快照生成最终失败 [{title[:20]}]: {last_error}")

    # 从文本中提取前几个词作为关键词（兜底）
    fallback_keywords = _extract_keywords_fallback(text, title)
    
    # 改进的兜底内容：基于实际文本内容生成，不再是死板模板
    # 提取首段前100字作为内容摘要
    first_sentences = []
    for para in text.split('\n\n')[:3]:
        para = para.strip()
        if para:
            # 取前2-3个句子
            sentences = _re.split(r'[。.!！？\n]', para)
            for s in sentences[:3]:
                s = s.strip()
                if 10 <= len(s) <= 100:
                    first_sentences.append(s)
            if first_sentences:
                break
    
    content_summary = first_sentences[0][:80] if first_sentences else ""
    
    # 基于实际内容生成更有价值的核心观点和学习目标
    if content_summary:
        improved_core_viewpoint = content_summary[:50]
        improved_learning_goal = f"能理解{title}中关于{fallback_keywords[0] if fallback_keywords else '核心概念'}的主要内容"
    else:
        # 最后的兜底：尽量给出有意义的描述
        improved_core_viewpoint = f"探讨{title}相关主题"
        improved_learning_goal = f"能概述{title}的主要内容和观点"
    
    return {
        "keywords": fallback_keywords,
        "core_viewpoint": improved_core_viewpoint,
        "learning_goal": improved_learning_goal,
        "importance": 3,
        "difficulty": "中等",
        "_fallback": True,  # 标记为兜底数据
    }


async def extract_chapter_snapshots_batch(
    chapters: List[Tuple[int, str, str]],
    concurrency: int = 3,
) -> Dict[int, dict]:
    """
    并发生成多个章节的快照（修复 P0-②）

    50 章串行 ≈ 150s → 并发 3 ≈ 50s
    - 输入：[(chapter_idx, title, content), ...]
    - 输出：{chapter_idx: snapshot}
    - 失败隔离：单章失败不影响其他章
    - 顺序保证：gather 返回 (idx, snap) 元组，按 idx 索引
    """
    import logging
    logger = logging.getLogger(__name__)

    sem = asyncio.Semaphore(concurrency)

    async def _gen(idx: int, title: str, content: str):
        async with sem:
            try:
                snap = await extract_chapter_snapshot(content, title)
                return idx, snap
            except Exception as e:
                logger.warning(f"批量快照生成失败 [{title[:20]}]: {e}")
                return idx, None

    tasks = [_gen(idx, title, content) for idx, title, content in chapters]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out: Dict[int, dict] = {}
    for r in results:
        if isinstance(r, Exception):
            logger.warning(f"[speed] gather 异常: {r}")
            continue
        idx, snap = r
        if snap:
            out[idx] = snap
    return out


def _extract_keywords_fallback(text: str, title: str, max_count: int = 5) -> list:
    """
    兜底关键词提取：不调用LLM，用基础NLP方法
    """
    if not text:
        return [title] if title else []

    import re
    # 简单提取：标题+首段中的实词
    first_para = text[:200]
    # 提取2-4字的中文实词
    words = re.findall(r'[\u4e00-\u9fa5]{2,4}', first_para)
    # 去重保持顺序
    seen = set()
    result = []
    for w in words:
        if w not in seen and len(w) >= 2:
            seen.add(w)
            result.append(w)
        if len(result) >= max_count - 1:
            break
    if title and title not in seen:
        result.insert(0, title)
    return result[:max_count]


async def generate_global_highlights(course_id: str, snapshots: list, chapter_titles: list) -> dict:
    """
    从所有章节快照中提炼全书核心知识点
    包含：核心知识点、章节依赖关系、推荐学习顺序
    """
    if not snapshots or not chapter_titles:
        return {
            "key_points": [], "chapter_priorities": [], "relationships": "",
            "chapter_dependencies": {}, "core_chapter_indices": []
        }

    # 构建章节摘要信息（含重要性）
    snapshot_text = "\n".join(
        f"第{i+1}章「{chapter_titles[i]}」 [重要性={s.get('importance', 3)}]: "
        f"关键词={s.get('keywords', [])}, 观点={s.get('core_viewpoint', '')}"
        for i, s in enumerate(snapshots)
    )

    # 标记元数据章节（序言/前言/自序等）
    meta_indices = [i for i, t in enumerate(chapter_titles) if is_non_core_chapter(t)]
    if meta_indices:
        meta_warning = (
            f"\n\n⚠️ 重要提示：以下章节是元数据章节（序言/前言/自序/推荐序/附录/目录等），"
            f"**绝对不能**作为核心章节或推荐学习章节：第{', '.join(str(i+1) for i in meta_indices)}章\n"
            f"核心章节必须来自正文章节（带'第N章'或'第N部分'等），不应包含序言类内容。\n"
        )
    else:
        meta_warning = ""

    # v1.3 P0-②: prompt 强化"本书独有"原则,避免生成放之四海皆准的泛化模板
    # 注意：如果快照为空或太少，简化 prompt 要求
    if len(snapshots) < 3:
        # 快照太少时，使用简化版 prompt
        prompt = f"""基于以下全书章节快照，请完成5个任务：

{snapshot_text}
{meta_warning}
任务1：提炼3-8个最重要的核心知识点（用"能+具体动作"描述，每个不超过25字）
任务2：推荐优先学习的章节顺序（**只能推荐正文章节**）
任务3：标注章节间的关键依赖关系
任务4：识别全书最核心的3-5个正文章节
任务5：用50字以内描述全书的核心逻辑关系

返回严格JSON（不要任何其他文字）：
{{
  "key_points": ["知识点1", "知识点2", ...],
  "chapter_priorities": ["第5章", "第8章", ...],
  "chapter_dependencies": {{"第8章": "第5章"}},
  "core_chapter_indices": ["第5章", "第8章", "第11章"],
  "relationships": "全书核心逻辑关系描述"
}}"""
    else:
        prompt = f"""基于以下全书章节快照，请完成5个任务：

{snapshot_text}
{meta_warning}
任务1：提炼5-10个最重要的核心知识点（**用"能+具体动作"描述，每个不超过25字，要具体有参考价值**）
   - ✅ 好例子："能列出ETF的5种投资策略"、"能解释ETF运作机制"、"能区分主动型与被动型ETF"
   - ❌ 坏例子："能理解ETF"、"能掌握ETF知识"、"能了解XXX"、"本章讲解XXX"
   - 核心知识点应该告诉读者学完后具体能做什么，而不是泛泛而谈
任务2：推荐优先学习的章节顺序（**只能推荐正文章节，不能是序言/前言/自序/附录**）
任务3：标注章节间的关键依赖关系（哪些正文章节必须先学）
任务4：识别全书最核心的4-6个正文章节（占全书价值80%），**绝对不能包含序言/前言/自序/附录等元数据章节**
任务5：用50字以内描述全书的核心逻辑关系

返回严格JSON（不要任何其他文字）：
{{
  "key_points": ["能+具体动作", "能+具体动作", ...],
  "chapter_priorities": ["第5章", "第8章", ...],
  "chapter_dependencies": {{"第8章": "第5章"}},
  "core_chapter_indices": ["第5章", "第8章", "第11章", "第14章"],
  "relationships": "全书核心逻辑关系描述"
}}"""

    last_error = None
    for attempt in range(3):
        try:
            result = await llm.chat_json([
                {"role": "system", "content": "你是知识地图构建专家。严格返回JSON，不要其他内容。"},
                {"role": "user", "content": prompt},
            ], temperature=0.3)

            key_points = result.get("key_points", [])
            chapter_priorities = result.get("chapter_priorities", [])
            chapter_dependencies = result.get("chapter_dependencies", {})
            core_chapter_indices = result.get("core_chapter_indices", [])
            relationships = str(result.get("relationships", ""))

            # 过滤掉元数据章节（序言/前言/自序/附录等）
            # 注意：core_chapter_indices 是形如"第N章"的字符串
            # P3-②: 扩展支持中文数字 + 英文 Chapter 5 / Chap. 5
            import re as _re_parse

            _CN_NUM = {
                '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
            }

            def _parse_chapter_num(s: str) -> int:
                """解析章节号，支持：
                - 第N章 / 第N部分（阿拉伯数字）
                - 第N章（中文数字，如"第五章"）
                - Chapter 5 / Chap. 5（英文）
                返回 0-based 索引，未匹配返回 -1
                """
                s = str(s)
                # 1. 阿拉伯数字
                m = _re_parse.search(r'第(\d+)[章部分]', s)
                if m:
                    return int(m.group(1)) - 1
                # 2. 中文数字（处理"十"和"十X"）
                m = _re_parse.search(r'第([一二三四五六七八九十]+)[章部分]', s)
                if m:
                    cn = m.group(1)
                    if cn == '十':
                        return 9  # "第十章" → index 9
                    if cn.startswith('十'):
                        return 9 + _CN_NUM.get(cn[1:], 0)  # 十一/十二/.../十九
                    if cn.endswith('十'):
                        # 二十/三十/.../九十
                        tens = _CN_NUM.get(cn[0], 0)
                        return tens * 10 - 1
                    return _CN_NUM.get(cn, -1) - 1
                # 3. 英文
                m = _re_parse.search(r'(?:Chapter|Chap\.?)\s*(\d+)', s, _re_parse.IGNORECASE)
                if m:
                    return int(m.group(1)) - 1
                return -1

            core_chapter_indices = [
                s for s in (core_chapter_indices or [])
                if isinstance(s, str)
                and 0 <= _parse_chapter_num(s) < len(chapter_titles)
                and not is_non_core_chapter(chapter_titles[_parse_chapter_num(s)])
            ]
            chapter_priorities = [
                s for s in (chapter_priorities or [])
                if isinstance(s, str)
                and 0 <= _parse_chapter_num(s) < len(chapter_titles)
            ]

            # 验证：只要有任意有效数据就算成功（更宽容的验证）
            # 允许 key_points 和 chapter_priorities 其中之一为空
            has_valid_data = (
                (key_points and isinstance(key_points, list) and len(key_points) > 0) or
                (chapter_priorities and isinstance(chapter_priorities, list) and len(chapter_priorities) > 0) or
                (core_chapter_indices and isinstance(core_chapter_indices, list) and len(core_chapter_indices) > 0)
            )
            
            if has_valid_data:
                # 兜底：如果 LLM 没返回核心章节，基于重要性计算
                if not core_chapter_indices or len(core_chapter_indices) == 0:
                    core_chapter_indices = _infer_core_chapters(snapshots, chapter_titles)
                
                # 兜底：如果 LLM 没返回知识点，用关键词生成
                if not key_points or len(key_points) == 0:
                    key_points = _generate_fallback_key_points(snapshots, chapter_titles)
                
                # 兜底：如果 LLM 没返回章节优先级，生成默认顺序
                if not chapter_priorities or len(chapter_priorities) == 0:
                    chapter_priorities = [f"第{i+1}章" for i in range(min(10, len(chapter_titles)))]

                return {
                    "key_points": key_points if isinstance(key_points, list) else [],
                    "chapter_priorities": chapter_priorities if isinstance(chapter_priorities, list) else [],
                    "chapter_dependencies": chapter_dependencies if isinstance(chapter_dependencies, dict) else {},
                    "core_chapter_indices": core_chapter_indices if isinstance(core_chapter_indices, list) else [],
                    "relationships": relationships[:200],
                }
            else:
                last_error = f"LLM返回空数据（key_points={len(key_points)}, chapter_priorities={len(chapter_priorities)}, core_chapters={len(core_chapter_indices)}）"
        except Exception as e:
            last_error = f"LLM调用失败: {e}"
            import logging
            logging.warning(f"全局精华生成第{attempt+1}次失败: {e}")

    # 全部失败：基于快照重要性自动计算（兜底）
    import logging
    logging.error(f"全局精华生成最终失败: {last_error}")
    return _build_highlights_fallback(snapshots, chapter_titles)


def _generate_fallback_key_points(snapshots: list, chapter_titles: list, max_points: int = 10) -> list:
    """
    当 LLM 未返回知识点时，基于快照关键词生成兜底知识点
    """
    key_points = []
    seen_keywords = set()
    
    # 按重要性排序快照
    sorted_snapshots = sorted(
        enumerate(snapshots),
        key=lambda x: x[1].get("importance", 3),
        reverse=True
    )
    
    for idx, snapshot in sorted_snapshots:
        if len(key_points) >= max_points:
            break
        
        title = chapter_titles[idx] if idx < len(chapter_titles) else f"第{idx+1}章"
        if is_non_core_chapter(title):
            continue
            
        keywords = snapshot.get("keywords", [])
        viewpoint = snapshot.get("core_viewpoint", "")
        
        # 从关键词生成知识点
        for kw in keywords[:2]:
            if kw and kw not in seen_keywords:
                point = f"能描述{kw}的核心要点和应用场景"
                if point not in key_points:
                    key_points.append(point)
                    seen_keywords.add(kw)
                    if len(key_points) >= max_points:
                        break
        
        # 如果有好的观点，也加入
        if viewpoint and len(viewpoint) > 10 and not viewpoint.startswith("本章讲解") and not viewpoint.startswith("了解"):
            point = f"能理解{viewpoint[:30]}"
            if point not in key_points:
                key_points.append(point)
    
    # 如果还是没有知识点，给一个通用的
    if not key_points:
        key_points = ["能掌握本书核心概念并应用到实际场景中"]
    
    return key_points[:max_points]


def _infer_core_chapters(snapshots: list, chapter_titles: list = None, top_n: int = 5) -> list:
    """
    兜底：基于快照的 importance 字段识别核心章节
    排除元数据章节（序言/前言/自序等）
    """
    if chapter_titles is None:
        chapter_titles = [""] * len(snapshots)

    # 过滤掉元数据章节
    valid_indices = [
        i for i, title in enumerate(chapter_titles)
        if not is_non_core_chapter(title)
    ]

    if not valid_indices:
        # 没有正文章节时回退到所有章节
        valid_indices = list(range(len(snapshots)))

    # 按重要性排序
    sorted_indices = sorted(
        valid_indices,
        key=lambda i: snapshots[i].get("importance", 3),
        reverse=True
    )

    return [f"第{i+1}章" for i in sorted_indices[:top_n]]


def _build_highlights_fallback(snapshots: list, chapter_titles: list) -> dict:
    """
    兜底：LLM完全失败时，基于快照数据手动构建精华
    改进：生成更有参考价值的内容，不再用死板模板
    """
    # 收集所有非空关键词（排除元数据章节）
    all_keywords = []
    core_viewpoints = []
    for i, s in enumerate(snapshots):
        if i < len(chapter_titles) and is_non_core_chapter(chapter_titles[i]):
            continue
        for kw in s.get("keywords", []):
            if kw and kw not in all_keywords:
                all_keywords.append(kw)
        # 收集有实际内容的核心观点
        cv = s.get("core_viewpoint", "")
        if cv and not cv.startswith("本章讲解") and not cv.startswith("了解") and len(cv) > 10:
            core_viewpoints.append(cv)

    # 基于 importance 排序找核心章节（自动过滤元数据）
    core_indices = _infer_core_chapters(snapshots, chapter_titles, top_n=5)
    all_priorities = [f"第{i+1}章" for i in range(len(snapshots))]

    # 生成更有价值的核心知识点
    key_points = []
    if all_keywords:
        # 用关键词生成更具体的知识点描述
        for kw in all_keywords[:6]:
            key_points.append(f"能描述{kw}的核心要点和应用场景")
    
    # 如果有好的核心观点，也加入知识点
    for vp in core_viewpoints[:3]:
        if len(vp) > 10 and vp not in key_points:
            key_points.append(f"能理解{vp[:30]}")

    # 如果没有有价值的知识点，给一个通用的但有指导性的
    if not key_points:
        key_points = ["能掌握本书核心概念并应用到实际场景中"]

    return {
        "key_points": key_points[:10],
        "chapter_priorities": all_priorities,
        "chapter_dependencies": {},
        "core_chapter_indices": core_indices,
        "relationships": "由于系统暂时无法深度分析，请按章节顺序学习",
        "_fallback": True,
    }


# ==================== 细读模式：均匀采样 ====================

def sample_chapter_fairly(text: str, target_chars: int = 5000) -> str:
    """
    按章节均匀采样，保证全书代表性
    不是只取前5000字，而是每段取固定比例
    """
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return text[:target_chars]

    total_chars = sum(len(p) for p in paragraphs)
    if total_chars <= target_chars:
        return '\n\n'.join(paragraphs)

    # 按比例分配每段的采样字符数
    ratio = target_chars / total_chars
    sampled = []
    current_chars = 0

    for para in paragraphs:
        if current_chars >= target_chars:
            break
        # 按比例采样该段落
        sampled_chars = min(len(para), int(len(para) * ratio) + 500)
        sampled.append(para[:sampled_chars])
        current_chars += sampled_chars

    return '\n\n'.join(sampled)


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


async def generate_speed_read_syllabus(course_id: str, chapters: list, snapshots: list) -> list:
    """
    速读模式：只生成全书最重要的20%知识点（精华掌握项）
    基于知识快照和全局精华，精选最核心的掌握项
    返回 [(chapter_index, description), ...]

    [DEPRECATED] 自 v1.1.2 起被 app._run_speed_mode_postprocess 中的 key_points 路径替代
    保留此函数仅为向后兼容，v1.3.0 将彻底删除
    新实现见 app.py::_run_speed_mode_postprocess
    """
    import warnings
    warnings.warn(
        "generate_speed_read_syllabus 已废弃，请改用 app._run_speed_mode_postprocess "
        "(内部直接用 global_highlights.key_points)",
        DeprecationWarning,
        stacklevel=2,
    )
    if not chapters or not snapshots:
        return []

    # 构建章节索引到快照的映射
    snapshot_map = {s.get("chapter_index", i): s for i, s in enumerate(snapshots)}

    # 收集所有章节的核心信息
    chapter_info = []
    for i, item in enumerate(chapters):
        if isinstance(item, tuple) and len(item) >= 2:
            ch_title, ch_content = item[0], item[1]
        else:
            continue

        snapshot = snapshot_map.get(i, {})
        keywords = snapshot.get("keywords", [])
        core_viewpoint = snapshot.get("core_viewpoint", "")

        chapter_info.append({
            "chapter_index": i,
            "title": ch_title,
            "keywords": keywords,
            "core_viewpoint": core_viewpoint,
            "content_preview": ch_content[:500] if ch_content else "",
        })

    # 估算全书总知识点数量，目标是只提取20%
    # 假设每章平均3个知识点，全书约 len(chapters) * 3 个
    total_estimated = len(chapters) * 3
    target_count = max(5, min(15, int(total_estimated * 0.2)))  # 5-15个之间

    # 构建 prompt，让 LLM 精选最重要的知识点
    chapter_summary = "\n".join([
        f"第{c['chapter_index'] + 1}章「{c['title']}」：关键词={c['keywords']}，核心观点={c['core_viewpoint']}"
        for c in chapter_info[:20]  # 最多处理20章
    ])

    prompt = f"""你是课程设计专家。请从以下全书章节中，精选**不超过 {target_count} 个**最重要的知识点，生成精华掌握项清单。

要求：
1. 每条掌握项以"能..."开头
2. 只选择全书最核心的概念和原理（最重要的 20%）
3. 优先选择跨章节关联的知识点
4. 避免重复，选择真正有区分度的知识点
5. 如果章节内容不足以支撑 {target_count} 条，宁少勿滥

全书章节概览：
{chapter_summary}

返回JSON格式：
{{"items": [
    {{"chapter_index": 0, "description": "能用自己的话解释XXX概念"}},
    ...
]}}"""

    try:
        result = await llm.chat_json([
            {"role": "system", "content": "你是课程设计专家，擅长提炼核心知识点。只返回JSON。"},
            {"role": "user", "content": prompt},
        ])

        items = result.get("items", [])
        all_items = []
        for item in items:
            ch_idx = item.get("chapter_index", 0)
            desc = item.get("description", "")
            if desc:
                all_items.append((ch_idx, desc))

        return all_items
    except Exception as e:
        print(f"速读模式掌握项生成失败: {e}")
        return []


async def generate_course_summary(text: str) -> str:
    """生成课程摘要"""
    prompt = f"""根据以下教材内容，生成一段简洁的课程摘要（100字以内）：

{text[:2000]}"""
    return await llm.chat([
        {"role": "system", "content": "你是课程摘要生成专家。"},
        {"role": "user", "content": prompt},
    ], temperature=0.3, max_tokens=200)
