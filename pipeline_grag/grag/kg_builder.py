"""
Builds a DOM-aware knowledge graph from raw page HTML.

Strips noise elements, locates main content, and parses sections, accordions,
tabs, tables, and people directories into structured graph nodes and retrievable
text chunks. Returns a graph (nodes + edges) and a flat list of chunks.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

from .text import clean_multiline_text, clean_text, chunk_words


NOISE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "svg",
    "header",
    "footer",
    "nav",
    "form",
    "iframe",
    ".site-header",
    ".site-footer",
    ".mega-menu-wrap",
    ".breadcrumb",
    ".breadcrumbs",
    ".social-links",
    ".widget",
    ".sidebar",
    ".skip-link",
    ".screen-reader-text",
    ".show-for-sr",
]

COMPONENT_MARKERS = [
    "accordion",
    "tab",
    "tabs",
    "tabpanel",
    "table",
    "faq",
    "course",
]

BLOCK_TAGS = {
    "p",
    "li",
    "div",
    "section",
    "article",
    "aside",
    "blockquote",
    "figcaption",
    "td",
    "th",
}

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


@dataclass
class GraphDoc:
    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, str]] = field(default_factory=list)
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    seen_nodes: set = field(default_factory=set)
    seen_edges: set = field(default_factory=set)
    chunk_texts: set = field(default_factory=set)

    def add_node(self, node_id: str, node_type: str, label: str, **props: Any) -> None:
        if node_id in self.seen_nodes:
            return
        self.seen_nodes.add(node_id)
        item = {"id": node_id, "type": node_type, "label": clean_text(label)}
        item.update({k: v for k, v in props.items() if v not in (None, "", [])})
        self.nodes.append(item)

    def add_edge(self, source: str, rel: str, target: str) -> None:
        key = (source, rel, target)
        if key in self.seen_edges:
            return
        self.seen_edges.add(key)
        self.edges.append({"source": source, "relation": rel, "target": target})

    def add_chunk(
        self,
        parent_id: str,
        text: str,
        page_id: str,
        url: str,
        page_title: str,
        path: List[str],
        source_type: str,
        min_chars: int = 30,
    ) -> None:
        text = clean_multiline_text(text)
        if len(text) < min_chars:
            return
        for piece in chunk_words(text):
            dedupe_key = (parent_id, piece.lower())
            if dedupe_key in self.chunk_texts:
                continue
            self.chunk_texts.add(dedupe_key)
            digest = hashlib.sha1(f"{parent_id}|{piece}".encode("utf-8")).hexdigest()[:16]
            chunk_id = f"chunk:{digest}"
            self.add_node(
                chunk_id,
                "Chunk",
                piece[:80],
                text=piece,
                url=url,
                page_title=page_title,
                path=path,
                source_type=source_type,
            )
            self.add_edge(parent_id, "HAS_CONTENT", chunk_id)
            self.chunks.append(
                {
                    "id": chunk_id,
                    "text": piece,
                    "url": url,
                    "page_id": page_id,
                    "page_title": page_title,
                    "path": path,
                    "source_type": source_type,
                }
            )


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def class_text(tag: Tag) -> str:
    return " ".join(tag.get("class", [])).lower()


def has_exact_class(tag: Tag, cls: str) -> bool:
    return cls in {str(item).lower() for item in tag.get("class", [])}


def has_class_part(tag: Tag, part: str) -> bool:
    return part in class_text(tag)


def is_accordion(tag: Tag) -> bool:
    return tag.name in {"ul", "div", "section"} and has_exact_class(tag, "accordion")


def is_responsive_accordion_tabs(tag: Tag) -> bool:
    return tag.name in {"ul", "div", "section"} and bool(tag.get("data-responsive-accordion-tabs"))


def is_accordion_item(tag: Tag) -> bool:
    return tag.name in {"li", "div", "section"} and (
        has_exact_class(tag, "accordion-item") or has_exact_class(tag, "accordion__item")
    )


def is_tab_group(tag: Tag) -> bool:
    role = (tag.get("role") or "").lower()
    return role == "tablist" or has_exact_class(tag, "tabs")


def is_table(tag: Tag) -> bool:
    return tag.name == "table"


def is_people_directory(tag: Tag) -> bool:
    return tag.name in {"div", "section"} and has_exact_class(tag, "people")


def direct_text(tag: Tag) -> str:
    parts = [str(x) for x in tag.find_all(string=True, recursive=False)]
    return clean_text(" ".join(parts))


def own_text_without_nested_components(tag: Tag) -> str:
    clone = BeautifulSoup(str(tag), "lxml")
    root = clone.find(tag.name)
    if root is None:
        return ""
    for nested in root.select(
        ".accordion, .accordion-item, .accordion__item, [role='tablist'], [role='tab'], [role='tabpanel'], table"
    ):
        nested.decompose()
    return structured_text(root)


def inline_text(tag: Tag) -> str:
    return clean_text(tag.get_text(" ", strip=True))


def li_text_without_nested_lists(tag: Tag) -> str:
    clone = BeautifulSoup(str(tag), "lxml")
    root = clone.find(tag.name)
    if root is None:
        return ""
    for nested in root.find_all(["ul", "ol"]):
        nested.decompose()
    return inline_text(root)


def structured_text(tag: Tag) -> str:
    if tag.name in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "figcaption"}:
        return inline_text(tag)
    if tag.name in {"ul", "ol"}:
        ordered = tag.name == "ol"
        list_lines = []
        for index, item in enumerate(tag.find_all("li", recursive=False), start=1):
            text = li_text_without_nested_lists(item)
            if text:
                marker = f"{index}. " if ordered else "- "
                list_lines.append(f"{marker}{text}")
        return clean_multiline_text("\n".join(list_lines))

    lines: List[str] = []

    def walk(node: Tag, depth: int = 0) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                text = clean_text(str(child))
                if text:
                    lines.append(text)
                continue
            if not isinstance(child, Tag):
                continue
            if child.name in {"script", "style", "noscript", "svg", "button"}:
                continue
            if child.name in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "figcaption"}:
                text = inline_text(child)
                if text:
                    lines.append(text)
                continue
            if child.name in {"ul", "ol"}:
                ordered = child.name == "ol"
                for index, item in enumerate(child.find_all("li", recursive=False), start=1):
                    text = li_text_without_nested_lists(item)
                    if text:
                        marker = f"{index}. " if ordered else "- "
                        lines.append(f"{'  ' * depth}{marker}{text}")
                    for nested_list in item.find_all(["ul", "ol"], recursive=False):
                        walk(nested_list, depth + 1)
                continue
            walk(child, depth)

    walk(tag)
    return clean_multiline_text("\n".join(lines))


def first_direct_heading(tag: Tag) -> Optional[Tag]:
    for child in tag.find_all(recursive=False):
        if isinstance(child, Tag) and child.name in HEADING_TAGS:
            return child
    return None


def direct_heading_children(tag: Tag) -> List[Tag]:
    return [
        child
        for child in tag.find_all(recursive=False)
        if isinstance(child, Tag) and child.name in HEADING_TAGS and clean_text(child.get_text(" ", strip=True))
    ]


def is_short_intro_paragraph(tag: Tag) -> bool:
    if tag.name != "p":
        return False
    text = inline_text(tag)
    if not text:
        return False
    return len(text) <= 120 and (text.endswith(":") or len(text.split()) <= 8)


def text_without(tag: Tag, selector: str) -> str:
    clone = BeautifulSoup(str(tag), "lxml")
    root = clone.find(tag.name)
    if root is None:
        return ""
    for item in root.select(selector):
        item.decompose()
    return clean_text(root.get_text(" ", strip=True))


def meaningful_children(tag: Tag) -> Iterable[Tag]:
    for child in tag.find_all(recursive=False):
        if not isinstance(child, Tag):
            continue
        if child.name in {"br", "hr", "img", "picture", "source"}:
            continue
        yield child


def page_main(soup: BeautifulSoup) -> Tag:
    return (
        soup.select_one("main")
        or soup.select_one(".main-content")
        or soup.select_one("#main")
        or soup.body
        or soup
    )


def title_from_soup(soup: BeautifulSoup, fallback: str) -> str:
    og_title = soup.select_one("meta[property='og:title']")
    if og_title and og_title.get("content"):
        return clean_text(og_title["content"].replace("| DSI", ""))
    if soup.title:
        return clean_text(soup.title.get_text(" ", strip=True).replace("| DSI", ""))
    h1 = soup.select_one("main h1, .main-content h1, h1")
    if h1:
        return clean_text(h1.get_text(" ", strip=True))
    return fallback


def page_header_content(soup: BeautifulSoup) -> Tuple[str, str]:
    header = soup.select_one(".page-header__content")
    if header is None:
        return "", ""

    heading = header.find(HEADING_TAGS)
    heading_text = clean_text(heading.get_text(" ", strip=True)) if heading else ""

    clone = BeautifulSoup(str(header), "lxml")
    root = clone.select_one(".page-header__content")
    if root is None:
        return heading_text, ""
    for item in root.find_all(HEADING_TAGS):
        item.decompose()
    body_text = clean_text(root.get_text(" ", strip=True))
    if not body_text:
        body_text = heading_text
    return heading_text, body_text


def parse_page_header(
    graph: GraphDoc,
    soup: BeautifulSoup,
    page_id: str,
    url: str,
    page_title: str,
) -> None:
    heading_text, body_text = page_header_content(soup)
    if not heading_text and not body_text:
        return

    label = "Page Header Content"
    header_id = stable_id("section", page_id, "page_header_content")
    path = [page_title, label]
    graph.add_node(
        header_id,
        "Section",
        label,
        heading=heading_text,
        url=url,
        page_title=page_title,
        path=path,
        source_type="page_header_content",
    )
    graph.add_edge(page_id, "HAS_SECTION", header_id)
    graph.add_chunk(header_id, body_text, page_id, url, page_title, path, "page_header_content", min_chars=1)


def iter_dataviz_payloads(soup: BeautifulSoup) -> Iterable[Dict[str, Any]]:
    pattern = re.compile(r"var\s+(datavizData_\d+)\s*=\s*(\{.*?\});", re.DOTALL)
    for script in soup.find_all("script"):
        script_text = script.string or script.get_text()
        if "datavizData_" not in script_text:
            continue
        for match in pattern.finditer(script_text):
            variable_name = match.group(1)
            try:
                payload = json.loads(match.group(2))
            except json.JSONDecodeError:
                continue
            payload["_variable_name"] = variable_name
            yield payload


def format_dataviz_payload(payload: Dict[str, Any]) -> Tuple[str, str]:
    title = clean_text(payload.get("title") or "")
    chart_data = payload.get("chartData") or {}
    debug = payload.get("_debug") or {}
    chart_type = clean_text(debug.get("chart_type") or "chart")
    is_percentage = str(payload.get("isPercentage") or "") == "1"
    suffix = "%" if is_percentage else ""

    if isinstance(chart_data, list):
        lines = [f"{clean_text(item.get('name', ''))}: {item.get('value')}{suffix}" for item in chart_data]
    elif isinstance(chart_data, dict) and "mapData" in chart_data:
        label = title or "Student Geographic Distribution"
        lines = [f"{clean_text(item.get('name', ''))}: {item.get('value')}" for item in chart_data.get("mapData", [])]
        title = label
    elif isinstance(chart_data, dict) and "categories" in chart_data:
        categories = chart_data.get("categories") or []
        series = chart_data.get("series") or []
        data = series[0].get("data", []) if series else []
        lines = [f"{clean_text(str(category))}: {value}{suffix}" for category, value in zip(categories, data)]
    else:
        lines = [json.dumps(chart_data, ensure_ascii=False)]

    label = title or f"Dataviz {payload.get('_variable_name', '')}".strip()
    text = clean_multiline_text(f"{label}\nChart type: {chart_type}\n" + "\n".join(f"- {line}" for line in lines if line))
    return label, text


def parse_dataviz(
    graph: GraphDoc,
    soup: BeautifulSoup,
    page_id: str,
    url: str,
    page_title: str,
) -> None:
    payloads = list(iter_dataviz_payloads(soup))
    if not payloads:
        return

    group_id = stable_id("section", page_id, "embedded_dataviz")
    group_path = [page_title, "Embedded Data Visualizations"]
    graph.add_node(
        group_id,
        "Section",
        "Embedded Data Visualizations",
        url=url,
        page_title=page_title,
        path=group_path,
        source_type="embedded_dataviz",
    )
    graph.add_edge(page_id, "HAS_SECTION", group_id)

    for ordinal, payload in enumerate(payloads, start=1):
        label, text = format_dataviz_payload(payload)
        chart_id = stable_id("diagram", page_id, payload.get("_variable_name", str(ordinal)), label)
        chart_path = group_path + [label]
        graph.add_node(
            chart_id,
            "Diagram",
            label,
            chart_variable=payload.get("_variable_name"),
            url=url,
            page_title=page_title,
            path=chart_path,
            source_type="embedded_dataviz",
        )
        graph.add_edge(group_id, "HAS_DIAGRAM", chart_id)
        graph.add_chunk(chart_id, text, page_id, url, page_title, chart_path, "diagram_data")


def parse_table(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> None:
    label = clean_text(tag.find("caption").get_text(" ", strip=True)) if tag.find("caption") else f"Table {ordinal}"
    table_id = stable_id("table", page_id, parent_id, label, str(ordinal))
    graph.add_node(table_id, "Table", label, url=url, page_title=page_title, path=path + [label])
    graph.add_edge(parent_id, "HAS_TABLE", table_id)

    rows = []
    for row_i, tr in enumerate(tag.find_all("tr")):
        cells = [clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if not cells:
            continue
        row_id = stable_id("row", table_id, str(row_i), " | ".join(cells))
        graph.add_node(row_id, "TableRow", f"Row {row_i + 1}", cells=cells, path=path + [label])
        graph.add_edge(table_id, "HAS_ROW", row_id)
        rows.append(" | ".join(cells))
    if rows:
        graph.add_chunk(table_id, "\n".join(rows), page_id, url, page_title, path + [label], "table")


def parse_accordion(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> None:
    label = "Accordion"
    accordion_id = stable_id("accordion", page_id, parent_id, str(ordinal), clean_text(tag.get_text(" ", strip=True))[:80])
    graph.add_node(accordion_id, "Accordion", label, url=url, page_title=page_title, path=path)
    graph.add_edge(parent_id, "HAS_ACCORDION", accordion_id)

    items = [child for child in meaningful_children(tag) if is_accordion_item(child)]
    if not items:
        parse_container(graph, tag, accordion_id, page_id, url, page_title, path + [label])
        return

    item_titles = []
    for item in items:
        title_tag = item.find(class_="accordion-title") or first_direct_heading(item)
        title = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else ""
        if title:
            item_titles.append(title)
    if len(item_titles) >= 2:
        graph.add_chunk(
            accordion_id,
            "This accordion contains: " + "; ".join(item_titles),
            page_id,
            url,
            page_title,
            path + [label],
            "accordion_index",
        )

    for item_i, item in enumerate(items):
        title_tag = item.find(class_="accordion-title") or first_direct_heading(item)
        title = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else clean_text(item.get_text(" ", strip=True))[:80]
        if not title:
            title = f"Accordion Item {item_i + 1}"
        item_id = stable_id("accordion_item", accordion_id, str(item_i), title)
        item_path = path + [title]
        graph.add_node(item_id, "AccordionItem", title, url=url, page_title=page_title, path=item_path)
        graph.add_edge(accordion_id, "HAS_ACCORDION_ITEM", item_id)

        content = item.find(class_="accordion-content") or item.find(class_="accordion__content")
        if content:
            parse_container(graph, content, item_id, page_id, url, page_title, item_path)
        else:
            text = own_text_without_nested_components(item)
            if title and text.startswith(title):
                text = text[len(title) :].strip()
            graph.add_chunk(item_id, text, page_id, url, page_title, item_path, "accordion_item")


def parse_progression_tabs(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> None:
    group_id = stable_id("progression_tabs", page_id, parent_id, str(ordinal), clean_text(tag.get_text(" ", strip=True))[:80])
    graph.add_node(group_id, "ProgressionTabs", "Course Progression Tabs", url=url, page_title=page_title, path=path)
    graph.add_edge(parent_id, "HAS_TAB_GROUP", group_id)

    tab_items = [child for child in meaningful_children(tag) if is_accordion_item(child)]
    for tab_i, tab_item in enumerate(tab_items):
        title_tag = tab_item.find(class_="accordion-title")
        title = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else f"Schedule {tab_i + 1}"
        tab_id = stable_id("progression_tab", group_id, str(tab_i), title)
        tab_path = path + [title]
        graph.add_node(tab_id, "ProgressionTab", title, url=url, page_title=page_title, path=tab_path)
        graph.add_edge(group_id, "HAS_TAB", tab_id)

        content = tab_item.find(class_="accordion-content", recursive=False) or tab_item.find(class_="accordion__content", recursive=False)
        if content is None:
            continue
        quarters = content.select(":scope > .quarter")
        if not quarters:
            parse_container(graph, content, tab_id, page_id, url, page_title, tab_path)
            continue

        quarter_labels = []
        for quarter_i, quarter in enumerate(quarters):
            quarter_node = parse_quarter(graph, quarter, tab_id, page_id, url, page_title, tab_path, quarter_i)
            if quarter_node:
                quarter_labels.append(quarter_node["label"])
        if quarter_labels:
            graph.add_chunk(
                tab_id,
                f"{title} contains: " + "; ".join(quarter_labels),
                page_id,
                url,
                page_title,
                tab_path,
                "progression_tab_index",
            )


def parse_quarter(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> Optional[Dict[str, str]]:
    title_tag = tag.select_one(".quarter__title")
    label = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else f"Quarter {ordinal + 1}"
    quarter_id = stable_id("quarter", parent_id, str(ordinal), label)
    quarter_path = path + [label]
    graph.add_node(quarter_id, "Quarter", label, url=url, page_title=page_title, path=quarter_path)
    graph.add_edge(parent_id, "HAS_QUARTER", quarter_id)

    groups = tag.select(":scope > .quarter__groups > .course-group")
    summaries = []
    for group_i, group in enumerate(groups):
        summary = parse_course_group(graph, group, quarter_id, page_id, url, page_title, quarter_path, group_i)
        if summary:
            summaries.append(summary)
    if summaries:
        graph.add_chunk(
            quarter_id,
            f"{label} includes: " + "; ".join(summaries),
            page_id,
            url,
            page_title,
            quarter_path,
            "quarter_index",
        )
    return {"id": quarter_id, "label": label}


def parse_course_group(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> Optional[str]:
    indicator = tag.select_one(".course-group__indicator")
    label = clean_text(indicator.get_text(" ", strip=True)) if indicator else f"Course Group {ordinal + 1}"
    group_id = stable_id("course_group", parent_id, str(ordinal), label, clean_text(tag.get_text(" ", strip=True))[:80])
    group_path = path + [label]
    graph.add_node(group_id, "CourseGroup", label, url=url, page_title=page_title, path=group_path)
    graph.add_edge(parent_id, "HAS_COURSE_GROUP", group_id)

    courses = []
    course_items = tag.select(".courses > ul.accordion > li.accordion__item, .courses > ul.accordion > li.accordion-item")
    for course_i, course_item in enumerate(course_items):
        course_title = parse_course_item(graph, course_item, group_id, page_id, url, page_title, group_path, course_i)
        if course_title:
            courses.append(course_title)
    if courses:
        graph.add_chunk(
            group_id,
            f"{label} courses: " + "; ".join(courses),
            page_id,
            url,
            page_title,
            group_path,
            "course_group_index",
        )
        return f"{label}: " + ", ".join(courses)
    return None


def parse_course_item(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> Optional[str]:
    title_tag = tag.find(class_="accordion-title")
    if title_tag:
        grading_tag = title_tag.find(class_="grading")
        grading = clean_text(grading_tag.get_text(" ", strip=True)) if grading_tag else ""
        title = text_without(title_tag, ".grading")
    else:
        grading = ""
        title = clean_text(tag.get_text(" ", strip=True))[:80]
    if not title:
        return None
    course_id = stable_id("course_item", parent_id, str(ordinal), title, grading)
    course_path = path + [title]
    graph.add_node(
        course_id,
        "CourseItem",
        title,
        grading=grading,
        url=url,
        page_title=page_title,
        path=course_path,
    )
    graph.add_edge(parent_id, "HAS_COURSE", course_id)

    content = tag.find(class_="accordion__content") or tag.find(class_="accordion-content")
    if content:
        description = clean_text(content.get_text(" ", strip=True))
        if grading:
            description = f"{title} ({grading}): {description}"
        else:
            description = f"{title}: {description}"
        graph.add_chunk(course_id, description, page_id, url, page_title, course_path, "course_description")
    return f"{title}" + (f" ({grading})" if grading else "")


def parse_people_directory(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> None:
    current_group_id = parent_id
    current_group_label = ""
    current_group_path = path

    for child_i, child in enumerate(meaningful_children(tag), start=1):
        if child.name in HEADING_TAGS or has_exact_class(child, "group-title"):
            label = clean_text(child.get_text(" ", strip=True))
            if not label:
                continue
            current_group_id = stable_id("people_group", page_id, parent_id, str(child_i), label)
            current_group_label = label
            current_group_path = path + [label]
            graph.add_node(
                current_group_id,
                "PeopleGroup",
                label,
                url=url,
                page_title=page_title,
                path=current_group_path,
            )
            graph.add_edge(parent_id, "HAS_PEOPLE_GROUP", current_group_id)
            continue

        if child.name == "ul" and has_exact_class(child, "gridder"):
            parse_people_grid(
                graph,
                child,
                tag,
                current_group_id,
                page_id,
                url,
                page_title,
                current_group_path,
                current_group_label,
            )


def parse_people_grid(
    graph: GraphDoc,
    grid: Tag,
    people_root: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    group_label: str,
) -> None:
    for person_i, item in enumerate(grid.find_all("li", class_="gridder-list", recursive=False)):
        target = (item.get("data-griddercontent") or "").strip()
        detail_id = target[1:] if target.startswith("#") else target
        card = item.select_one(".card--person") or item.find("a")
        if card is None:
            continue
        name_tag = card.select_one(".card__name")
        name = clean_text(name_tag.get_text(" ", strip=True)) if name_tag else clean_text(card.get_text(" ", strip=True))[:80]
        if not name:
            continue
        position_tag = card.select_one(".card__position")
        position = clean_text(position_tag.get_text(" ", strip=True)) if position_tag else ""
        link = card.get("href")
        image = card.select_one("img")
        image_src = image.get("src") if image else None
        person_id = stable_id("person", parent_id, str(person_i), name, detail_id)
        person_path = path + [name]
        graph.add_node(
            person_id,
            "Person",
            name,
            group=group_label,
            position=position,
            profile_url=link,
            image_url=image_src,
            detail_id=detail_id,
            url=url,
            page_title=page_title,
            path=person_path,
        )
        graph.add_edge(parent_id, "HAS_PERSON", person_id)
        if position:
            graph.add_chunk(
                person_id,
                f"{name} ({group_label}): {position}" if group_label else f"{name}: {position}",
                page_id,
                url,
                page_title,
                person_path,
                "person_position",
            )

        detail = people_root.find(id=detail_id) if detail_id else None
        if detail:
            parse_person_detail(graph, detail, person_id, page_id, url, page_title, person_path, name)


def parse_person_detail(
    graph: GraphDoc,
    detail: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    name: str,
) -> None:
    content = detail.select_one(".content-detail") or detail
    detail_path = path + ["Bio"]
    paragraphs = content.find_all(["p", "li"], recursive=True)
    added = False
    for para in paragraphs:
        if para.find_parent(["ul", "ol"]) and para.name != "li":
            continue
        text = clean_text(para.get_text(" ", strip=True))
        if len(text) < 30:
            continue
        graph.add_chunk(parent_id, text, page_id, url, page_title, detail_path, "person_bio")
        added = True
    if not added:
        text = clean_text(content.get_text(" ", strip=True))
        if text:
            graph.add_chunk(parent_id, text, page_id, url, page_title, detail_path, "person_bio")


def parse_tab_group(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> None:
    group_id = stable_id("tab_group", page_id, parent_id, str(ordinal), clean_text(tag.get_text(" ", strip=True))[:80])
    graph.add_node(group_id, "TabGroup", f"Tab Group {ordinal}", url=url, page_title=page_title, path=path)
    graph.add_edge(parent_id, "HAS_TAB_GROUP", group_id)

    tabs = tag.find_all(attrs={"role": "tab"})
    panels = tag.find_all(attrs={"role": "tabpanel"})
    for tab_i, tab in enumerate(tabs):
        title = clean_text(tab.get_text(" ", strip=True)) or f"Tab {tab_i + 1}"
        tab_id = stable_id("tab", group_id, str(tab_i), title)
        tab_path = path + [title]
        graph.add_node(tab_id, "Tab", title, url=url, page_title=page_title, path=tab_path)
        graph.add_edge(group_id, "HAS_TAB", tab_id)
        controls = tab.get("aria-controls")
        panel = None
        if controls:
            panel = tag.find(id=controls)
        if panel is None and tab_i < len(panels):
            panel = panels[tab_i]
        if panel is not None:
            parse_container(graph, panel, tab_id, page_id, url, page_title, tab_path)


def parse_section_like(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> Optional[str]:
    heading = first_direct_heading(tag)
    if heading is None:
        return None
    label = clean_text(heading.get_text(" ", strip=True))
    if not label:
        return None
    section_id = stable_id("section", page_id, parent_id, str(ordinal), label)
    section_path = path + [label]
    graph.add_node(
        section_id,
        "Section",
        label,
        heading_tag=heading.name,
        url=url,
        page_title=page_title,
        path=section_path,
    )
    graph.add_edge(parent_id, "HAS_SECTION", section_id)

    for child in meaningful_children(tag):
        if child is heading:
            continue
        parse_element(graph, child, section_id, page_id, url, page_title, section_path, ordinal)
    return section_id


def parse_heading_grouped_container(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
) -> bool:
    headings = direct_heading_children(tag)
    if len(headings) < 2:
        return False

    current_parent = parent_id
    current_path = path
    saw_heading = False
    for ordinal, child in enumerate(meaningful_children(tag), start=1):
        if child.name in HEADING_TAGS:
            label = clean_text(child.get_text(" ", strip=True))
            if not label:
                continue
            section_id = stable_id("section", page_id, parent_id, str(ordinal), label)
            section_path = path + [label]
            graph.add_node(
                section_id,
                "Section",
                label,
                heading_tag=child.name,
                url=url,
                page_title=page_title,
                path=section_path,
            )
            graph.add_edge(parent_id, "HAS_SECTION", section_id)
            current_parent = section_id
            current_path = section_path
            saw_heading = True
            continue
        if saw_heading:
            parse_element(graph, child, current_parent, page_id, url, page_title, current_path, ordinal)
        else:
            parse_element(graph, child, parent_id, page_id, url, page_title, path, ordinal)
    return True


def parse_element(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
    ordinal: int,
) -> None:
    if is_people_directory(tag):
        parse_people_directory(graph, tag, parent_id, page_id, url, page_title, path, ordinal)
        return
    if is_responsive_accordion_tabs(tag):
        parse_progression_tabs(graph, tag, parent_id, page_id, url, page_title, path, ordinal)
        return
    if is_accordion(tag):
        parse_accordion(graph, tag, parent_id, page_id, url, page_title, path, ordinal)
        return
    if is_tab_group(tag):
        parse_tab_group(graph, tag, parent_id, page_id, url, page_title, path, ordinal)
        return
    if is_table(tag):
        parse_table(graph, tag, parent_id, page_id, url, page_title, path, ordinal)
        return
    if tag.name in {"ul", "ol"}:
        graph.add_chunk(parent_id, structured_text(tag), page_id, url, page_title, path, tag.name)
        return
    if tag.name in HEADING_TAGS:
        label = clean_text(tag.get_text(" ", strip=True))
        if label:
            section_id = stable_id("section", page_id, parent_id, str(ordinal), label)
            graph.add_node(section_id, "Section", label, heading_tag=tag.name, url=url, page_title=page_title, path=path + [label])
            graph.add_edge(parent_id, "HAS_SECTION", section_id)
        return

    if tag.name in {"section", "article", "div", "li"}:
        if parse_heading_grouped_container(graph, tag, parent_id, page_id, url, page_title, path):
            return
        section_id = parse_section_like(graph, tag, parent_id, page_id, url, page_title, path, ordinal)
        if section_id:
            return

    componentish = any(marker in class_text(tag) for marker in COMPONENT_MARKERS)
    children = list(meaningful_children(tag))
    if children and (tag.name not in BLOCK_TAGS or componentish or len(clean_text(direct_text(tag))) < 30):
        parse_container(graph, tag, parent_id, page_id, url, page_title, path)
        return

    text = own_text_without_nested_components(tag)
    graph.add_chunk(parent_id, text, page_id, url, page_title, path, tag.name or "text")


def parse_container(
    graph: GraphDoc,
    tag: Tag,
    parent_id: str,
    page_id: str,
    url: str,
    page_title: str,
    path: List[str],
) -> None:
    direct = clean_text(direct_text(tag))
    if len(direct) >= 40:
        graph.add_chunk(parent_id, direct, page_id, url, page_title, path, "direct_text")
    children = list(meaningful_children(tag))
    skip_indexes = set()
    for index, child in enumerate(children):
        if index in skip_indexes:
            continue
        next_child = children[index + 1] if index + 1 < len(children) else None
        if (
            isinstance(next_child, Tag)
            and is_short_intro_paragraph(child)
            and next_child.name in {"ul", "ol"}
        ):
            merged_text = clean_multiline_text(f"{inline_text(child)}\n{structured_text(next_child)}")
            graph.add_chunk(parent_id, merged_text, page_id, url, page_title, path, f"p_plus_{next_child.name}")
            skip_indexes.add(index + 1)
            continue
        parse_element(graph, child, parent_id, page_id, url, page_title, path, index + 1)


def build_graph(raw_dir: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    graph = GraphDoc()
    root_id = "program:msads"
    graph.add_node(root_id, "Program", "MS in Applied Data Science", acronym="MSADS")

    for raw_path in sorted(raw_dir.glob("*.json")):
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        html = data.get("html") or ""
        soup = BeautifulSoup(html, "lxml")
        url = data.get("url") or raw_path.stem
        page_title = title_from_soup(soup, raw_path.stem.replace("__", " / "))
        page_id = stable_id("page", url)
        graph.add_node(
            page_id,
            "Page",
            page_title,
            url=url,
            raw_file=str(raw_path.as_posix()),
            page_class=data.get("page_class"),
            fetched_at=data.get("fetched_at"),
            aliases=data.get("url_aliases", []),
        )
        graph.add_edge(root_id, "HAS_PAGE", page_id)
        parse_page_header(graph, soup, page_id, url, page_title)
        parse_dataviz(graph, soup, page_id, url, page_title)
        for selector in NOISE_SELECTORS:
            for noise in soup.select(selector):
                noise.decompose()
        main = page_main(soup)
        parse_container(graph, main, page_id, page_id, url, page_title, [page_title])

    return {"nodes": graph.nodes, "edges": graph.edges}, graph.chunks
