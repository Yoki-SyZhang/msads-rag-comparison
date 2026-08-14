---
name: sitemap-mapping
description: Use when the user asks you to map, explore, or inventory the link structure of a website — typically before web scraping, RAG corpus construction, or content audits. Triggers include "map the site structure", "explore the sublinks", "produce a sitemap", "what pages are under this URL". Do NOT use for single-page extraction, API discovery, or technical SEO audits (robots.txt parsing, schema validation).
model: opus
---

# Sitemap Mapping

## Core Principle: Depth by Click-Reachability

Depth is defined by user-navigation reachability, not URL path nesting. A URL at `/a/b/c/d/` may be shallower than a URL at `/x/` if the former is sidebar-promoted from the root.

### Depth Definitions

- **D0**: The root URL.
- **D1**: URLs reachable by clicking a link on D0 — from D0's main content OR via the Sidebar Promotion Rule below.
- **D2**: URLs reachable from any D1 page, excluding URLs already in D0/D1.
- **D3**: URLs reachable from D2 pages, excluding everything above. At D3, include only pages with substantive unique content; skip nav-only pages, listings, and redundant aggregations.

### Sidebar Promotion Rule

A URL is sidebar-promoted to the same depth as its siblings (rather than classified by nesting) if and only if ALL of these hold:

1. **Mutual-membership persistent navigation among siblings**: The URL appears in a navigation element (e.g., sidebar) that also appears — with identical or near-identical link set, order, and styling — on every OTHER page the navigation element lists. The navigation must be mutually reachable among all the sibling URLs it names: if it lists A/B/C/D, then A's page contains it, B's page contains it, C's page contains it, D's page contains it. The nominal parent page (one level up) is NOT required to contain this navigation — sidebars commonly appear only on a program's sub-pages, not on the program's landing page.
2. **In-scope target**: The URL falls within the user-defined scope (same domain, same path prefix, etc.). Out-of-scope links in the same sidebar are handled under "Footers and Header Menus."
3. **Not in main content of the nominal parent**: The URL is not already present as a link in the main content area of the page it would otherwise be nested under.

All three → promoted; in the URL Tree, marked with a dashed connector (`┄`). If a URL is both in the sidebar and in the main content of the parent, it is a normal child (solid connector).

**Descent**: Promotion affects only the promoted URL's own depth. Its outgoing links are evaluated normally for D2/D3.

**Note on incomplete navigation**: If the sidebar lists URLs that some member pages don't actually contain (e.g., 3 of 5 member pages show it, the other 2 don't), the mutual-membership condition fails. In that case, do NOT promote — classify the URLs by ordinary reachability and note the inconsistency in Concerns.

### Breadcrumbs as a Sanity Check

Use breadcrumbs internally to cross-check depth assignments. If click-reachability depth is deeper than breadcrumb depth, you likely missed a direct link — re-verify. Do not output breadcrumbs as a separate section.

## Verification Requirements

Before declaring a redirect, alias, or duplicate:
- Perform an HTTP HEAD or GET request **with redirect-following disabled** (in `requests`: `allow_redirects=False`; in `httpx`: `follow_redirects=False`). Inspect the status code and Location header to distinguish between:
  - **Redirect** (301/302/307/308 with Location header): the URL is an alias; the target URL is canonical
  - **Independent page** (200): the URL is its own page; compare actual content against suspected duplicate
- Never infer redirects from URL patterns alone, and never from a followed-redirect fetch (which silently masks the redirect)
- For suspected duplicates that are NOT redirects, compare actual content (hash or substantial text overlap), not just URL paths

## Reusing a Previous Sitemap

If a prior sitemap exists: preserve substantive findings (duplicates, stale content, image-embedded data warnings), but re-verify each with a live check. If new evidence contradicts a prior finding, note both and the resolution in Concerns.

## Output Format

Produce these sections in order:

### 1. Metadata Header

```
# [Site Name] Sitemap

Mapped: YYYY-MM-DD
Scope: [domain(s)], depth 0–N, [scope description]
Method: [manual fetch, crawler, etc.]
```

### 2. URL Tree

Rules:
- Full URLs only (scheme + domain), never relative paths
- Prefix each with `[D0]`, `[D1]`, etc.
- Downward links only; no back-references
- Sidebar-promoted URLs use tilde connectors (`~`); normal children use solid (`├`, `└`)
- If a URL is reachable from multiple parents, list once under its canonical parent; note alternative parents in a footnote

Example:

```
[D0] https://example.com/programs/main/
│
├── [D1] https://example.com/programs/main/curriculum/
│   ├── [D2] https://example.com/programs/main/curriculum/courses/
│   └── [D2] https://example.com/programs/main/curriculum/schedule/
│
├── [D1] https://example.com/programs/main/faculty/
│
├~~ [D1] https://example.com/programs/main/faqs/        (sidebar-promoted)
├~~ [D1] https://example.com/apply/                     (sidebar-promoted)
```

### 3. Flat Depth Listing

Plain enumeration of unique URLs at each depth. No hierarchy. Serves as a quick scraper reference.

### 4. Footers and Header Menus

Site-wide footer blocks and top-level header menus only. Sidebars are NOT listed here — they're in the URL Tree via promotion.

For each cluster:
- Name (e.g., "Global footer")
- URLs it contains
- Pages it appears on (or "all pages")
- Flag any that are also in-scope content pages (should be rare; investigate if so)

### 5. Out-of-Scope Reachable Pages

URLs found but not mapped further. One line per URL pattern with reason. Typical exclusions: external domains, ephemeral content (news/events/blog), redundant aggregations, login/form-only pages (capture entry URL for reference), downloadable files.

### 6. Page Details Table

Columns: `#`, `Depth`, `URL`, `Title`, `Content Type`, `Est. Words`, `Rendering`. One row per in-scope page.

- `Content Type`: e.g., "program overview", "faculty bios", "FAQ"
- `Est. Words`: main content area only, exclude nav/footer
- `Rendering`: "Static HTML", "JS-rendered", "Static HTML (accordion)", etc.

### 7. Concerns

Numbered list. Each: title, description, evidence (specific URLs/behavior), recommendation.

Common categories: duplicate content, URL aliases/redirects, stale content, paginated/lazy-loaded content, image-embedded data, interactive widgets, linked non-HTML assets.

### 8. Recommended Scrape List

Priority-sorted table: `Priority`, `URL`, `Notes`.

Priority values: **Must** (core), **Should** (supplementary), **Optional** (marginal), **Dedup-check** (scrape but compare), **Skip** (excluded, give reason).

## Anti-Patterns

- URL-path nesting as depth
- Relative paths anywhere
- Repeating sidebar links under every page that contains the sidebar (use promotion instead)
- Exceeding requested max depth without asking
- Fetching full page content during mapping — only enough to identify title, content type, rough word count
- Inferring redirects without HTTP verification
- Conflating sidebars, footers, and breadcrumbs — each has a distinct handling
