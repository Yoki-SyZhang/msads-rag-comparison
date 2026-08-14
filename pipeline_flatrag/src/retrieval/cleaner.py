"""
Stage 2 HTML cleaner for UChicago MS-ADS RAG chatbot.
Converts raw HTML to clean structured text with section tracking,
link handling, and PII redaction.
"""

import copy
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup, NavigableString, Tag

sys.path.insert(0, str(Path(__file__).parents[1] / "scraping"))
from scraper import _NOISE_SELECTORS, url_to_filename  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# UChicago-site-specific noise selectors applied in addition to Stage 1 list.
# The mobile sidebar is a <div> (not <aside>) inside <main>, so it isn't
# caught by the generic "aside" selector already in _NOISE_SELECTORS.
_EXTRA_NOISE = [
    ".mobile-sidebar-container",
    ".mobile-sidebar",
    ".no-page-hero-spacer",
]

EXTERNAL_ACTION_DOMAINS = frozenset({
    "apply-psd.uchicago.edu",
    "financialaid.uchicago.edu",
    "grad.uchicago.edu",
    "internationalaffairs.uchicago.edu",
})

SAFE_EMAILS = frozenset({
    "applieddatascience-advising@uchicago.edu",
    "applieddatascience-admissions@uchicago.edu",
})

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4"})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "iframe", "svg", "button"})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_program_type(url: str) -> str:
    """Classify a page URL into one of three program_type values."""
    if "in-person-program" in url:
        return "in_person"
    if "online-program" in url:
        return "online"
    return "general"


def _url_page_title(url: str) -> str:
    """Derive a stable page title from the URL's last path segment."""
    slug = url.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").title()


def _is_external_action(href: str) -> bool:
    if not href or not href.startswith("http"):
        return False
    return urlparse(href).netloc in EXTERNAL_ACTION_DOMAINS


def redact_pii(text: str) -> str:
    """Replace emails (except SAFE_EMAILS) and phone numbers with [REDACTED]."""
    placeholders: dict[str, str] = {}

    def _sub_email(m: re.Match) -> str:
        email = m.group(0)
        key = f"\x00EMAIL{len(placeholders)}\x00"
        placeholders[key] = email
        return key

    text = _EMAIL_RE.sub(_sub_email, text)

    for key, email in placeholders.items():
        if email.lower() in SAFE_EMAILS:
            text = text.replace(key, email)
        else:
            text = text.replace(key, "[REDACTED]")

    text = _PHONE_RE.sub("[REDACTED]", text)
    return text


def _extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
        for sep in (" | ", " – ", " - "):
            if sep in title:
                return title.split(sep)[0].strip()
        return title
    return ""


def _heading_text(heading: Tag) -> str:
    """Extract heading text, stripping button/SVG/icon child noise."""
    h = copy.copy(heading)
    for tag in h.find_all(["button", "svg", "i"]):
        tag.decompose()
    for span in h.find_all("span", class_=re.compile(r"icon|arrow|sr.only|toggle", re.I)):
        span.decompose()
    return h.get_text(strip=True)


def _table_to_text(table: Tag) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(separator=" ", strip=True) for td in tr.find_all(["td", "th"])]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _accordion_content(li: Tag) -> Tag | None:
    """Return the direct-child content div of an accordion <li>.

    The site uses two class naming conventions:
      - accordion__content  (underscore) for inner question items
      - accordion-content   (hyphen)     for outer category items
    A deep soup.find() would mis-fire on nested accordions, so we scan
    only direct children.
    """
    target = {"accordion__content", "accordion-content"}
    for child in li.children:
        if isinstance(child, Tag) and child.name == "div":
            if target & set(child.get("class") or []):
                return child
    return None


def _list_to_text(lst: Tag) -> str:
    items = []
    for li in lst.find_all("li", recursive=False):
        items.append("• " + li.get_text(separator=" ", strip=True))
    return "\n".join(items)


def _extract_js_object(text: str, start: int) -> str | None:
    """Return the complete JSON object starting at text[start] using brace counting.
    Handles arbitrary nesting; returns None if text[start] is not '{'.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return None


_SCRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.DOTALL | re.I)
_DATAVIZ_VAR_RE = re.compile(r"datavizData_(\d+)\s*=\s*")


def _parse_dataviz_scripts(html: str) -> dict[str, dict]:
    """Extract datavizData_* chart data embedded as JS variables in <script> blocks.

    Returns {dataviz_id_str: {"title": str, "text": str}} for each parseable chart.
    Handles donut charts (list chartData), bar charts (dict with series/categories),
    and geomaps (dict with mapData).
    """
    result: dict[str, dict] = {}
    for script_match in _SCRIPT_RE.finditer(html):
        content = script_match.group(1)
        for m in _DATAVIZ_VAR_RE.finditer(content):
            var_id = m.group(1)
            json_str = _extract_js_object(content, m.end())
            if not json_str:
                continue
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            title: str = data.get("title") or ""
            chart_data = data.get("chartData", {})
            is_pct = data.get("isPercentage") == "1"
            lines: list[str] = []

            if isinstance(chart_data, list):
                # Donut chart: [{"name": "...", "value": ...}, ...]
                suffix = "%" if is_pct else ""
                for item in chart_data:
                    lines.append(f"{item['name']}: {item['value']}{suffix}")

            elif isinstance(chart_data, dict):
                categories = chart_data.get("categories", [])
                series = chart_data.get("series", [])
                map_data = chart_data.get("mapData", [])

                if categories and series:
                    # Bar chart
                    y_label = chart_data.get("yAxisLabel", "")
                    suffix = "%" if (is_pct or "percent" in y_label.lower()) else ""
                    for s in series:
                        for cat, val in zip(categories, s.get("data", [])):
                            lines.append(f"{cat}: {val}{suffix}")

                elif map_data:
                    # Geomap — values are already percentage points
                    if not title:
                        title = "Student Geographic Origin"
                    for item in map_data:
                        lines.append(f"{item['name']}: {item['value']}%")

            if lines and title:
                result[var_id] = {
                    "title": title,
                    "text": f"{title}: {', '.join(lines)}",
                }
    return result


def _inline_links(node: Tag) -> None:
    """Replace <a> tags in-place: external action → [text](href), else → text only."""
    for a in node.find_all("a"):
        href = a.get("href", "")
        link_text = a.get_text(strip=True)
        if not link_text:
            a.decompose()
            continue
        if _is_external_action(href):
            a.replace_with(f"[{link_text}]({href})")
        else:
            a.replace_with(link_text)


# ---------------------------------------------------------------------------
# Shared accordion output builder
# ---------------------------------------------------------------------------

def _accordion_to_sections(
    items: list[tuple[list[str], str, str]],
    overview_data: dict | None = None,
    overview_section: str = "Overview",
    program_type: str = "general",
) -> list[dict]:
    """Build section dicts from parsed accordion items + optional JSON overview.

    items: list of (hierarchy_parts, text, content_type)
      hierarchy_parts — list of heading labels from outermost to innermost,
                        e.g. ["In-Person Program", "1-Year Program", "Machine Learning I"]
    overview_data: if provided, emitted first as a JSON accordion_overview chunk.
    """
    sections: list[dict] = []

    if overview_data:
        sections.append({
            "section":            overview_section,
            "section_breadcrumb": overview_section,
            "text":               overview_data,
            "content_type":       "accordion_overview",
            "program_type":       program_type,
        })

    for hierarchy_parts, text, content_type in items:
        section_name = hierarchy_parts[-1]
        breadcrumb   = " > ".join(p for p in hierarchy_parts if p)
        sections.append({
            "section":            section_name,
            "section_breadcrumb": breadcrumb,
            "text":               text,
            "content_type":       content_type,
            "program_type":       program_type,
        })

    return sections


# ---------------------------------------------------------------------------
# Generic two-level accordion extractor (div.list-accordion)
# ---------------------------------------------------------------------------

def _extract_list_accordion(
    acc_div: Tag,
    heading_stack: list[tuple[int, str]],
    page_title: str,
    program_type: str,
) -> list[dict]:
    """Handle div.list-accordion > ul.accordion > li.accordion-item > ... > li.accordion__item.

    Produces one accordion_overview chunk (JSON table-of-contents) and one
    detail chunk per inner accordion item (li.accordion__item).
    Only invoked when both levels of accordion are present.
    """
    overview_data: dict[str, list[str]] = {}
    items: list[tuple[list[str], str, str]] = []

    heading_bc = " > ".join(t for _, t in heading_stack)

    for tab_li in acc_div.select("li.accordion-item"):
        tab_name_el = tab_li.select_one("a.accordion-title")
        if not tab_name_el:
            continue
        tab_name = tab_name_el.get_text(strip=True)
        course_names: list[str] = []

        for item_li in tab_li.select("li.accordion__item"):
            title_el = item_li.select_one("a.accordion-title")
            body_el  = item_li.select_one(".accordion__content .textblock")
            if not title_el:
                continue

            item_title = title_el.get_text(strip=True)
            item_body  = body_el.get_text(" ", strip=True) if body_el else ""
            item_body  = redact_pii(item_body)
            course_names.append(item_title)

            hierarchy = ([heading_bc] if heading_bc else []) + [tab_name, item_title]
            items.append((hierarchy, item_body, "text"))

        if course_names:
            overview_data[tab_name] = course_names

    overview_sec = f"{page_title} - Accordion Overview"
    return _accordion_to_sections(
        items,
        overview_data=overview_data or None,
        overview_section=overview_sec,
        program_type=program_type,
    )


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

def extract_sections(
    html: str,
    url: str,
    program_type: str = "general",
) -> tuple[list[dict], str]:
    """
    Parse HTML and return (sections, page_title).

    sections = [{
        "section": str,
        "section_breadcrumb": str,
        "text": str,
        "content_type": str,
        "program_type": str,
    }, ...]

    Each section corresponds to content under one heading. Text blocks
    from tables/lists get their own section entries with appropriate
    content_type. All texts have PII redacted.
    """
    soup = BeautifulSoup(html, "lxml")

    # Exclude the generic "header" selector: the site-wide nav is already covered
    # by ".site-header", and the content header (header.page-header inside
    # div.main-content) contains the page h1 and intro paragraph we want to keep.
    _selectors = [s for s in _NOISE_SELECTORS if s != "header"] + _EXTRA_NOISE
    for sel in _selectors:
        for el in soup.select(sel):
            el.decompose()

    dataviz_lookup = _parse_dataviz_scripts(html)

    main = (
        soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.find(id=re.compile(r"^main$|^primary$|^content$|^main-content$"))
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"entry-content|post-content|site-main|content-area"))
        or soup.find("body")
    )
    if main is None:
        return [], ""

    page_title = _url_page_title(url)
    is_time_sensitive = "events-deadlines" in url

    current_section: str = page_title or "Introduction"
    # heading_stack tracks (level, text) for breadcrumb construction.
    # Mutated in _walk() via list append/pop — no nonlocal needed.
    heading_stack: list[tuple[int, str]] = []
    _accordion_depth: list[int] = [0]
    sections: list[dict] = []
    pending: list[str] = []

    def _breadcrumb() -> str:
        return " > ".join(t for _, t in heading_stack)

    def _flush() -> None:
        text = " ".join(pending).strip()
        pending.clear()
        if len(text.split()) >= 5:
            bc = _breadcrumb() if _accordion_depth[0] > 0 else current_section
            sections.append({
                "section":            current_section,
                "section_breadcrumb": bc,
                "text":               text,
                "content_type":       "time_sensitive" if is_time_sensitive else "text",
                "program_type":       program_type,
            })

    def _walk(parent: Tag) -> None:
        nonlocal current_section
        for child in parent.children:
            if isinstance(child, NavigableString):
                t = str(child).strip()
                if t:
                    pending.append(t)
                continue

            tag = child.name
            if tag is None or tag in _SKIP_TAGS:
                continue

            if tag in _HEADING_TAGS:
                _flush()
                level = int(tag[1])
                text  = _heading_text(child)
                # Pop all entries at or below this level, then push current
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, text))
                current_section = text

            elif tag == "table":
                _flush()
                text = _table_to_text(child)
                if text:
                    bc = _breadcrumb() if _accordion_depth[0] > 0 else current_section
                    sections.append({
                        "section":            current_section,
                        "section_breadcrumb": bc,
                        "text":               text,
                        "content_type":       "time_sensitive" if is_time_sensitive else "table",
                        "program_type":       program_type,
                    })

            elif tag in ("ul", "ol"):
                # If the list contains accordion items, walk into it so each
                # item gets its own section; otherwise flatten to bullets.
                lis = child.find_all("li", recursive=False)
                is_accordion = any(
                    "accordion__item" in (li.get("class") or [])
                    or "accordion-item" in (li.get("class") or [])
                    for li in lis
                )
                if is_accordion:
                    _walk(child)
                else:
                    _flush()
                    text = _list_to_text(child)
                    if text:
                        bc = _breadcrumb() if _accordion_depth[0] > 0 else current_section
                        sections.append({
                            "section":            current_section,
                            "section_breadcrumb": bc,
                            "text":               text,
                            "content_type":       "time_sensitive" if is_time_sensitive else "list",
                            "program_type":       program_type,
                        })

            elif tag == "figcaption":
                _flush()
                text = child.get_text(separator=" ", strip=True)
                if text:
                    bc = _breadcrumb() if _accordion_depth[0] > 0 else current_section
                    sections.append({
                        "section":            current_section,
                        "section_breadcrumb": bc,
                        "text":               text,
                        "content_type":       "caption",
                        "program_type":       program_type,
                    })

            elif tag == "p":
                _inline_links(child)
                text = child.get_text(separator=" ", strip=True)
                if text:
                    pending.append(text)

            elif tag == "li":
                classes = child.get("class") or []
                if "accordion__item" in classes or "accordion-item" in classes:
                    # Accordion item: treat the title link as a section heading,
                    # then walk the content div for the answer body.
                    # Use _accordion_content() to find the DIRECT-child content div;
                    # deep search would mis-fire on nested two-level accordions.
                    _flush()
                    title_a = child.find("a", class_="accordion-title")
                    if title_a:
                        item_text = title_a.get_text(strip=True)
                        level = (heading_stack[-1][0] + 1) if heading_stack else 3
                        while heading_stack and heading_stack[-1][0] >= level:
                            heading_stack.pop()
                        heading_stack.append((level, item_text))
                        current_section = item_text
                    _accordion_depth[0] += 1
                    content = _accordion_content(child)
                    if content:
                        _walk(content)
                    else:
                        _walk(child)
                    _accordion_depth[0] -= 1
                    # Pop the item-level entry so sibling items don't accumulate
                    if heading_stack and heading_stack[-1][0] >= ((heading_stack[-2][0] + 1) if len(heading_stack) >= 2 else 3):
                        heading_stack.pop()
                else:
                    _walk(child)

            elif tag in ("div", "section", "article", "blockquote", "details",
                         "summary", "main", "dd", "dt"):
                dataviz_id = child.get("data-dataviz-id")
                classes = child.get("class") or []

                if dataviz_id and dataviz_id in dataviz_lookup:
                    _flush()
                    chart = dataviz_lookup[dataviz_id]
                    parent_bc = _breadcrumb()
                    section_bc = f"{parent_bc} > {chart['title']}" if parent_bc else chart["title"]
                    sections.append({
                        "section":            chart["title"],
                        "section_breadcrumb": section_bc,
                        "text":               chart["text"],
                        "content_type":       "time_sensitive" if is_time_sensitive else "table",
                        "program_type":       program_type,
                    })

                elif tag == "div" and "list-accordion" in classes:
                    # Two-level course accordion: generate overview + per-item chunks
                    has_outer = bool(child.find("li", class_="accordion-item"))
                    has_inner = bool(child.find("li", class_="accordion__item"))
                    if has_outer and has_inner:
                        _flush()
                        new_secs = _extract_list_accordion(
                            child, [], page_title, program_type
                        )
                        sections.extend(new_secs)
                    else:
                        _walk(child)

                else:
                    _walk(child)

            else:
                _inline_links(child)
                text = child.get_text(separator=" ", strip=True)
                if text:
                    pending.append(text)

    _walk(main)
    _flush()

    return sections, page_title


# ---------------------------------------------------------------------------
# Course Progressions — specialized cleaner
# ---------------------------------------------------------------------------

def _clean_course_progressions(raw: dict) -> dict:
    """Specialized cleaner for the Course Progressions page.

    The page uses a 3-tab layout (Full-Time / Part-Time / 2-Year Full-Time)
    with quarters → course-groups → individual courses. The generic _walk()
    loses tab+quarter context and lets grading spans bleed into section names.
    This cleaner traverses the DOM hierarchy explicitly.

    Now produces:
    - One accordion_overview chunk: {tab: {quarter: [course_label, ...]}}
    - One detail chunk per course with full breadcrumb
    """
    html = raw.get("html", "")
    url = raw.get("url", "")
    program_type = _derive_program_type(url)
    soup = BeautifulSoup(html, "lxml")

    _selectors = [s for s in _NOISE_SELECTORS if s != "header"] + _EXTRA_NOISE
    for sel in _selectors:
        for el in soup.select(sel):
            el.decompose()

    page_title = _url_page_title(url)

    outer_accordion = soup.find("ul", attrs={"data-responsive-accordion-tabs": True})
    if not outer_accordion:
        secs, _ = extract_sections(html, url, program_type=program_type)
        return {
            "url": url,
            "page_title": page_title,
            "page_class": raw.get("page_class", ""),
            "url_aliases": raw.get("url_aliases", []),
            "scraped_at": raw.get("fetched_at", ""),
            "program_type": program_type,
            "sections": secs,
        }

    # overview_data[tab][quarter] = [course_label, ...]
    overview_data: dict[str, dict[str, list[str]]] = {}
    # items: (hierarchy_parts, text, content_type)
    items: list[tuple[list[str], str, str]] = []

    for tab_li in outer_accordion.find_all("li", recursive=False):
        if "accordion-item" not in (tab_li.get("class") or []):
            continue
        title_a = tab_li.find("a", class_="accordion-title")
        tab_name = title_a.get_text(strip=True) if title_a else "Schedule"
        overview_data[tab_name] = {}

        tab_content = _accordion_content(tab_li)
        if not tab_content:
            continue

        for quarter_div in tab_content.find_all("div", class_="quarter"):
            qt_div = quarter_div.find("div", class_="quarter__title")
            quarter_label = ""
            quarter_duration = ""
            if qt_div:
                strong = qt_div.find("strong")
                if strong:
                    quarter_label = strong.get_text(strip=True)
                span = qt_div.find("span")
                if span:
                    quarter_duration = span.get_text(strip=True).lstrip("•").strip()

            if quarter_label not in overview_data[tab_name]:
                overview_data[tab_name][quarter_label] = []

            for cg in quarter_div.find_all("div", class_="course-group"):
                ind = cg.find("div", class_="course-group__indicator")
                category = ind.get_text(strip=True) if ind else ""

                for course_li in cg.find_all("li", class_="accordion__item"):
                    title_link = course_li.find("a", class_="accordion-title")
                    grading = ""
                    course_name = ""
                    if title_link:
                        tl = copy.copy(title_link)
                        grading_span = tl.find("span", class_="grading")
                        if grading_span:
                            grading = grading_span.get_text(strip=True)
                            grading_span.decompose()
                        course_name = tl.get_text(strip=True)

                    content_div = course_li.find("div", class_="accordion__content")
                    description = content_div.get_text(separator=" ", strip=True) if content_div else ""

                    meta_parts = [p for p in [category, grading, quarter_duration] if p]
                    seen: set[str] = set()
                    meta_parts = [p for p in meta_parts if not (p in seen or seen.add(p))]
                    meta = ", ".join(meta_parts)
                    header = f"{course_name} [{meta}]" if meta else course_name
                    text = f"{header}\n{description}".strip() if description else header
                    text = redact_pii(text)

                    # Overview entry uses the compact header (name + meta)
                    if course_name:
                        overview_data[tab_name][quarter_label].append(header)

                    if len(text.split()) >= 5:
                        hierarchy = ["Course Progressions", tab_name, quarter_label, course_name]
                        items.append((hierarchy, text, "text"))

    sections = _accordion_to_sections(
        items,
        overview_data=overview_data,
        overview_section=f"{page_title} - Accordion Overview",
        program_type=program_type,
    )

    return {
        "url": url,
        "page_title": page_title,
        "page_class": raw.get("page_class", ""),
        "url_aliases": raw.get("url_aliases", []),
        "scraped_at": raw.get("fetched_at", ""),
        "program_type": program_type,
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Instructors & Staff — specialized cleaner
# ---------------------------------------------------------------------------

def _clean_instructors_staff(raw: dict) -> dict:
    """Specialized cleaner for the Faculty, Instructors, Staff page.

    The page uses a gridder widget: ul.gridder holds person cards, and each
    div.gridder-content[id] holds that person's bio. The generic _walk()
    concatenates all 52 bios into a single text block, making per-person
    retrieval impossible.

    Strategy:
    1. Decompose div.people from the soup; run generic extract_sections() on
       the remaining HTML to capture the intro text cleanly.
    2. From ul.gridder li items build id → (name, group) maps and
       overview_data: {group: [name, ...]} for JSON accordion_overview.
    3. Emit one bio chunk per div.gridder-content using _accordion_to_sections().
       The old plain-text group summary chunks are replaced by the JSON overview.
    """
    html = raw.get("html", "")
    url = raw.get("url", "")
    program_type = _derive_program_type(url)
    soup = BeautifulSoup(html, "lxml")

    _selectors = [s for s in _NOISE_SELECTORS if s != "header"] + _EXTRA_NOISE
    for sel in _selectors:
        for el in soup.select(sel):
            el.decompose()

    page_title = _url_page_title(url)

    people_div = soup.find("div", class_="people")

    # Maps built from ul.gridder li items
    id_to_name: dict[str, str] = {}
    id_to_group: dict[str, str] = {}
    # overview_data[group] = [name, ...] — replaces old plain-text group summaries
    overview_data: dict[str, list[str]] = {}

    if people_div:
        current_group = "Faculty, Instructors"
        for child in people_div.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "h3" and "group-title" in (child.get("class") or []):
                current_group = child.get_text(strip=True)
            elif child.name == "ul" and "gridder" in (child.get("class") or []):
                if current_group not in overview_data:
                    overview_data[current_group] = []
                for li in child.find_all("li", class_="gridder-list"):
                    data_id = (li.get("data-griddercontent") or "").lstrip("#")
                    name_el = li.find("h1", class_="card__name")
                    name = name_el.get_text(strip=True) if name_el else ""
                    if data_id and name:
                        id_to_name[data_id] = name
                        id_to_group[data_id] = current_group
                        overview_data[current_group].append(name)

        # Remove people div so generic cleaner captures only the intro text
        people_div.decompose()

    # Intro text via generic cleaner on the modified soup (no div.people)
    intro_sections, _ = extract_sections(str(soup), url, program_type=program_type)

    # Per-person bio chunks (re-parse original HTML to get full people_div)
    soup2 = BeautifulSoup(html, "lxml")
    _selectors2 = [s for s in _NOISE_SELECTORS if s != "header"] + _EXTRA_NOISE
    for sel in _selectors2:
        for el in soup2.select(sel):
            el.decompose()

    items: list[tuple[list[str], str, str]] = []
    people_div2 = soup2.find("div", class_="people")
    if people_div2:
        for bio_div in people_div2.find_all("div", class_="gridder-content"):
            person_id = bio_div.get("id", "")
            person_name = id_to_name.get(person_id, "")
            group_name = id_to_group.get(person_id, "Faculty, Instructors")

            desc_div = bio_div.find("div", class_="content-detail__description")
            text_src = desc_div if desc_div else bio_div
            text = text_src.get_text(separator=" ", strip=True)
            text = redact_pii(text)

            if len(text.split()) >= 5:
                hierarchy = ["Faculty & Staff", group_name, person_name] if person_name else ["Faculty & Staff", group_name]
                items.append((hierarchy, text, "text"))

    accordion_sections = _accordion_to_sections(
        items,
        overview_data=overview_data or None,
        overview_section=f"{page_title} - Accordion Overview",
        program_type=program_type,
    )

    return {
        "url": url,
        "page_title": page_title,
        "page_class": raw.get("page_class", ""),
        "url_aliases": raw.get("url_aliases", []),
        "scraped_at": raw.get("fetched_at", ""),
        "program_type": program_type,
        "sections": intro_sections + accordion_sections,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_page(raw: dict) -> dict:
    """Convert a raw JSON record to a cleaned record with sections."""
    url = raw.get("url", "")
    program_type = _derive_program_type(url)

    if "course-progressions" in url:
        return _clean_course_progressions(raw)
    if "instructors-staff" in url:
        return _clean_instructors_staff(raw)

    html = raw.get("html", "")
    sections, page_title = extract_sections(html, url, program_type=program_type)
    return {
        "url": url,
        "page_title": page_title,
        "page_class": raw.get("page_class", ""),
        "url_aliases": raw.get("url_aliases", []),
        "scraped_at": raw.get("fetched_at", ""),
        "program_type": program_type,
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# CLI entry point — clean all raw files to data/cleaned/
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    base = Path(__file__).resolve().parents[2]
    raw_dir = base / "data" / "raw"
    cleaned_dir = base / "data" / "cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)

    files = [f for f in raw_dir.glob("*.json") if f.name != "scrape_errors.jsonl"]
    logging.info(f"Cleaning {len(files)} raw files → {cleaned_dir}")

    for f in sorted(files):
        raw = json.loads(f.read_text(encoding="utf-8"))
        cleaned = clean_page(raw)
        out = cleaned_dir / f.name
        out.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Done. {len(files)} files cleaned → {cleaned_dir}")
