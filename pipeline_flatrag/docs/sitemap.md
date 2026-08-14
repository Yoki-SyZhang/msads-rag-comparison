# MS in Applied Data Science — Site Sitemap

Mapped: 2026-04-18  
Revised: 2026-04-19 — (1) Events & Deadlines, Tuition/Fees/Aid, Our Students, Explore MS-ADS Campus, Career Outcomes promoted to D1; (2) HTTP HEAD verification: in-person-program and online-program canonical URLs corrected to short form (`/education/masters-programs/in-person-program/` and `…/online-program/`); long-form `ms-in-applied-data-science/` variants are 301 aliases.  
Scope: datascience.uchicago.edu, depth 0–2 program pages + D3 capstone research pages  
Method: Manual fetch via WebFetch (static HTML, no JS rendering); one request per page, extracting title, links, sidebar, breadcrumb, and estimated word count only — no full-content retrieval.
Revised: 2026-04-25 — Manually modified ## 1 to ## 6 according to manual inspection.
---

## 1. URL Tree

```
[D0] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/in-person-program/
│   │     (canonical 200; /ms-in-applied-data-science/in-person-program/ → 301 here — see Concern #1)
│   └── [D2] https://datascience.uchicago.edu/people/greg-green/
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/online-program/
│   │     (canonical 200; /ms-in-applied-data-science/online-program/ → 301 here — see Concern #1)
│   └── [D2] https://datascience.uchicago.edu/people/arnab-bose-phd/
│             (also reachable from events-deadlines D1 main content)
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/capstone-projects/
│   │     (alias: /capstone-projects/ — D0 links to short form; see Concern #1)
│   └── [D2] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/capstone-project-archive/
│             (sidebar sub-item, directly clickable from program sidebar)
│         └── [D3] https://datascience.uchicago.edu/research/[capstone-slug]/  (×39 MS-ADS capstone projects — see Concern #6)
│                   Full URL list in §2 D3 Flat Depth Listing.
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/course-progressions/
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/how-to-apply/
│   │     (alias: /how-to-apply/ — D0 links to short form; see Concern #1)
│   ├── [D2] https://datascience.uchicago.edu/people/patrick-vonesh/
│   ├── [D2] https://datascience.uchicago.edu/people/jose-alvarado/
│   └── [D2] https://datascience.uchicago.edu/people/matthew-harris-ridker/
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/events-deadlines/
│         (promoted from D2; sidebar-listed page, one click from any program page) ¹
│
├── [D1] https://datascience.uchicago.edu/education/tuition-fees-aid/
│         (promoted from D2; sidebar-listed page; also linked from in-person-program, online-program, faqs main content) ¹
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/our-students/
│         (promoted from D2; sidebar-listed page) ¹
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/instructors-staff/
│   │     (alias: /about-us/ — D0 links to short form; see Concern #1)
│   └── [D2] https://datascience.uchicago.edu/people/[name]/  (×50 individual profiles — see Page Details §6)
│             Representative entries listed in §3 Flat Depth Listing.
│
├── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/faqs/
│
├── [D1] https://datascience.uchicago.edu/explore-the-ms-ads-campus/
│         (promoted from D2; sidebar-listed page) ¹
│
└── [D1] https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/career-outcomes/
          (promoted from D2; sidebar-listed page) ¹
```

**Footnotes:**

¹ Events & Deadlines, Tuition/Fees/Aid, Our Students, Explore the MS-ADS Campus, and Career Outcomes all appear as named entries in the persistent program sidebar present on every D1+ page. Although the sidebar is absent from D0 itself (preventing strict Sidebar Promotion Rule application — see Concern #6), these pages are treated as D1 because they are first-class program navigation destinations from a user perspective: directly selectable from the sidebar on any program page, not buried under a specific sub-page's content. The crawl originally discovered them via D1 main-content links; the promoted classification reflects user-navigation depth, not crawl-discovery path.

---

## 2. Flat Depth Listing

### D0

1. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/

### D1

2. https://datascience.uchicago.edu/education/masters-programs/in-person-program/
3. https://datascience.uchicago.edu/education/masters-programs/online-program/
4. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/capstone-projects/
5. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/course-progressions/
6. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/how-to-apply/
7. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/events-deadlines/
8. https://datascience.uchicago.edu/education/tuition-fees-aid/
9. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/our-students/
10. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/instructors-staff/
11. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/faqs/
12. https://datascience.uchicago.edu/explore-the-ms-ads-campus/
13. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/career-outcomes/

### D2

14. https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/capstone-project-archive/
15. https://datascience.uchicago.edu/people/greg-green/
16. https://datascience.uchicago.edu/people/arnab-bose-phd/
17. https://datascience.uchicago.edu/people/patrick-vonesh/
18. https://datascience.uchicago.edu/people/jose-alvarado/
19. https://datascience.uchicago.edu/people/matthew-harris-ridker/
    — Plus ~47 additional faculty/staff profiles from instructors-staff D1: —
20. https://datascience.uchicago.edu/people/francisco-azeredo-phd/
21. https://datascience.uchicago.edu/people/anil-d-chaturvedi-phd/
22. https://datascience.uchicago.edu/people/nick-kadochnikov/
23. https://datascience.uchicago.edu/people/ming-long-lam-phd/
24. https://datascience.uchicago.edu/people/roger-moore-mba/
25. https://datascience.uchicago.edu/people/utku-pamuksuz-phd/
26. https://datascience.uchicago.edu/people/donald-patchell-mse-mba/
27. https://datascience.uchicago.edu/people/jonathan-williams-ms/
28. https://datascience.uchicago.edu/people/shaddy-abado-phd-2/
29. https://datascience.uchicago.edu/people/gizem-agar-phd/
30. https://datascience.uchicago.edu/people/mike-anderson/
31. https://datascience.uchicago.edu/people/stephen-barry/
32. https://datascience.uchicago.edu/people/shree-bharadwaj-ms/
33. https://datascience.uchicago.edu/people/sanjay-boddhu-phd/
34. https://datascience.uchicago.edu/people/fouad-bousetouane-phd/
35. https://datascience.uchicago.edu/people/shahbaz-chaudhary-ma/
36. https://datascience.uchicago.edu/people/sebastien-donadio-phd/
37. https://datascience.uchicago.edu/people/ignas-grabauskas/
38. https://datascience.uchicago.edu/people/batu-gundogdu-phd/
39. https://datascience.uchicago.edu/people/justin-kurland-phd/
40. https://datascience.uchicago.edu/people/jeong-yoon-lee-phd/
41. https://datascience.uchicago.edu/people/john-navarro/
42. https://datascience.uchicago.edu/people/danny-ng-phd/
43. https://datascience.uchicago.edu/people/ashish-pujari/
44. https://datascience.uchicago.edu/people/jenny-schmidt/
45. https://datascience.uchicago.edu/people/jeanette-shutay-phd/
46. https://datascience.uchicago.edu/people/fan-yang-phd/
47. https://datascience.uchicago.edu/people/dmitri-sidorov/
48. https://datascience.uchicago.edu/people/igor-yakushin-phd/
49. https://datascience.uchicago.edu/people/taylor-alexander/
50. https://datascience.uchicago.edu/people/briana-allen/
51. https://datascience.uchicago.edu/people/josie-badillo-sittig/
52. https://datascience.uchicago.edu/people/zach-brown/
53. https://datascience.uchicago.edu/people/kendall-cox/
54. https://datascience.uchicago.edu/people/nick-diantonio-m-ed/
55. https://datascience.uchicago.edu/people/lauren-isaacman-darga/
56. https://datascience.uchicago.edu/people/henry-igunbor/
57. https://datascience.uchicago.edu/people/emma-kerr/
58. https://datascience.uchicago.edu/people/samantha-kruse/
59. https://datascience.uchicago.edu/people/lauren-milewski-msed/
60. https://datascience.uchicago.edu/people/alison-ossyra/
61. https://datascience.uchicago.edu/people/dujuan-smith/
62. https://datascience.uchicago.edu/people/daniel-truesdale/
63. https://datascience.uchicago.edu/people/taylor-wallace/
64. https://datascience.uchicago.edu/people/jennifer-wei-mem/
65. https://datascience.uchicago.edu/people/samantha-widemon/
66. https://datascience.uchicago.edu/people/ben-wiebers/

### D3

67. https://datascience.uchicago.edu/research/argonne-rezzy-ai-support-chatbot-for-ev-charger-reservation-app-evrez/
68. https://datascience.uchicago.edu/research/inference-analytics-automated-icd-10-code-prediction-for-healthcare-reports-using-large-language-models/
69. https://datascience.uchicago.edu/research/research-gpt-for-healthcare-mcp-driven-multi-agent-rag-enhanced-llm-system-for-prostate-cancer-temporal-summarization/
70. https://datascience.uchicago.edu/research/global-iq-optimizing-roi-of-global-talent-mobility-through-predictive-modeling-and-optimization-for-corporate-programs-policies/
71. https://datascience.uchicago.edu/research/research-gpt-for-healthcare-applications-of-generative-ai-in-emergency-department-admission-evaluation/
72. https://datascience.uchicago.edu/research/chicago-white-sox-measuring-pitcher-deception-and-command-using-motion-tracking-data/
73. https://datascience.uchicago.edu/research/aetna-optimization-engine-creation-scenario-planning-and-sensitivity-analysis-tool/
74. https://datascience.uchicago.edu/research/latentview-analytics-visual-defect-detection/
75. https://datascience.uchicago.edu/research/here-technologies-vehicle-perception-data-vpd-and-here-lane-model-alignment/
76. https://datascience.uchicago.edu/research/ateema-ai-driven-media-kit/
77. https://datascience.uchicago.edu/research/tokio-marine-highland-flood-risk-rating-of-a-property-leveraging-digital-elevation-model/
78. https://datascience.uchicago.edu/research/here-technologies-ai-powered-feature-extraction-from-street-level-imagery-for-automated-mapmaking/
79. https://datascience.uchicago.edu/research/charactour-ai-developing-a-personality-driven-generative-ai-model-for-capturing-diverse-linguistic-styles/
80. https://datascience.uchicago.edu/research/united-airlines-intelligent-airport-agent-for-enhanced-gate-service/
81. https://datascience.uchicago.edu/research/research-gpt-for-healthcare-concordagent-an-integrated-ai-agent-for-ultrasound-pathology/
82. https://datascience.uchicago.edu/research/rakuten-graphrag-or-similar-llm-applications-for-analyzing-advertiser-and-publisher-partnership/
83. https://datascience.uchicago.edu/research/here-technologies-ensuring-map-freshness-with-an-ai-powered-places-data-pipeline/
84. https://datascience.uchicago.edu/research/grai-matter-total-value-of-workforce-tvow/
85. https://datascience.uchicago.edu/research/argonne-lead-vehicle-distance-estimation-using-forward-vision-in-ego-vehicles/
86. https://datascience.uchicago.edu/research/inference-analytics-healthcare-specialized-llm-with-reinforcement-learning/
87. https://datascience.uchicago.edu/research/research-gpt-for-healthcare/
88. https://datascience.uchicago.edu/research/aetna-optimization-engine-creation-multi-objective-optimization-model/
89. https://datascience.uchicago.edu/research/healthlab-innovations-inc-continuity-engines-unlock-dark-data-to-save-lives-through-ai-driven-follow-ups/
90. https://datascience.uchicago.edu/research/aetna-measuring-the-impact-of-diverse-marketing-campaigns-on-medicare-stars-performance/
91. https://datascience.uchicago.edu/research/global-iq-optimizing-roi-of-global-talent-mobility-through-llm-based-corporate-employee-decision-support/
92. https://datascience.uchicago.edu/research/rakuten-llm-driven-large-scale-scraping-and-competitive-marketing-strategy/
93. https://datascience.uchicago.edu/research/inference-analytics-ai-powered-scientific-research-assistant/
94. https://datascience.uchicago.edu/research/lexlead-ai-legal-services-recommendation-system/
95. https://datascience.uchicago.edu/research/research-gpt-for-healthcare-agentic-clinical-rag-model-context-protocol-based-framework/
96. https://datascience.uchicago.edu/research/healthlab-innovations-inc-synergy-engines-unlocking-business-strategies-with-ai-4000-digital-health-companies/
97. https://datascience.uchicago.edu/research/aetna-developing-a-predictive-medicare-grievance-model-for-cms-star-ratings-improvement/
98. https://datascience.uchicago.edu/research/global-iq-optimizing-roi-of-global-talent-mobility-through-predictive-modeling-and-optimization-of-employee-compensation/
99. https://datascience.uchicago.edu/research/research-robotics-enders-game4/
100. https://datascience.uchicago.edu/research/uniqlo-china-enhancing-actionability-of-external-market-data-with-ai/
101. https://datascience.uchicago.edu/research/labelmaster-digital-marketing-alchemy-unlocking-data-driven-success-or-the-roi-playbook-mastering-ads-emails-web-traffic/
102. https://datascience.uchicago.edu/research/royal-cyber-llm-powered-auto-grading-assignment-with-feedback/
103. https://datascience.uchicago.edu/research/research-gpt-for-healthcare-reinforcement-learning-driven-multi-agent-framework-for-unifying-clinical-imaging-and-text-retrieval/
104. https://datascience.uchicago.edu/research/global-iq-soup-to-nuts-creating-a-global-living-wage-data-product/
105. https://datascience.uchicago.edu/research/research-robotics-robartists4/

---

## 3. Footers and Header Menus 

### Global Header Menu & Footer (appears on all pages)

| Label | URL |
|-------|-----|
| About | https://datascience.uchicago.edu/about/ |
| People | https://datascience.uchicago.edu/about/leadership-staff/ |
| Research | https://datascience.uchicago.edu/research/ |
| Education | https://datascience.uchicago.edu/education/ |
| Outreach | https://datascience.uchicago.edu/outreach/ |
| News | https://datascience.uchicago.edu/news-events/news/ |
| Events | https://datascience.uchicago.edu/news-events/events/ |
| Jobs | https://datascience.uchicago.edu/about/jobs/ |
| Newsletter Archive | https://datascience.uchicago.edu/newsletter-archive/ |
| Physical Sciences Division | https://physicalsciences.uchicago.edu/ |
| Nondiscrimination Statement | https://datascience.uchicago.edu/nondiscrimination-statement/ |
| Accessibility | https://accessibility.uchicago.edu |
| Contact | https://datascience.uchicago.edu/about/contact/ |

Pages on: all pages  
Flag: None are in-scope MS-ADS content pages, but all are classified as "dsi-general".

### Social Media Footer Links (all pages)

X/Twitter, Bluesky, LinkedIn, YouTube, GitHub, Instagram, TikTok — all external; out of scope.

### MS-ADS Program Sidebar (appears on D1+ program pages only; NOT on D0)

This is the persistent left-sidebar navigation present on every page within the MS-ADS program section (D1 and below). It lists all program sub-pages and is the primary within-section navigation mechanism.

Full sidebar link set:
- Overview → D0
- In-Person Program → D1
- Online Program → D1
- Capstone Projects → D1 (with sub-item: Capstone Project Archive → D2)
- Course Progressions → D1
- How to Apply → D1
- Events & Deadlines → **D1** (promoted)
- Tuition, Fees, & Aid → **D1** (promoted)
- Our Students → **D1** (promoted)
- Faculty, Instructors, Staff → D1
- FAQs → D1
- Explore the MS-ADS Campus → **D1** (promoted)
- Career Outcomes → **D1** (promoted)
- Get In Touch → https://apply-psd.uchicago.edu/register/?id=... (external; out of scope)

Pages on: all D1 and D2 program pages  
Flag: Sidebar is absent from D0 itself — see Concern #6 and Notes on Skill Application.

---

## 4. Out-of-Scope Reachable Pages

| URL pattern | Reason |
|-------------|--------|
| https://datascience.uchicago.edu/news/* | Ephemeral/dated news articles |
| https://datascience.uchicago.edu/events/* | Ephemeral event pages |
| https://datascience.uchicago.edu/insights/* | Analytical blog posts; not core program info |
| https://datascience.uchicago.edu/research/ | General DSI research hub; not MS-ADS specific |
| https://datascience.uchicago.edu/about/leadership-staff/ | General DSI leadership; not program-specific (linked from people profiles) |
| https://datascience.uchicago.edu/fellows-and-scholars/ | General DSI scholars; not program-specific (linked from people profiles) |
| https://apply-psd.uchicago.edu/* | External application portal; form-only |
| https://www.chicagobooth.edu/* | External domain (Booth joint degree) |
| https://physicalsciences.uchicago.edu/* | External domain (PSD division) |
| https://grad.uchicago.edu/* | External domain (graduate admissions requirements) |
| https://internationalaffairs.uchicago.edu/* | External domain (CPT/visa info) |
| https://www.uchicago.edu/en/education-and-research/academic-calendar | External domain (academic calendar) |
| https://bursar.uchicago.edu/* | External domain (tuition billing) |
| https://financialaid.uchicago.edu/* | External domain (financial aid) |
| https://www.youtube.com/*, https://youtu.be/* | External; video content |
| https://www.bls.gov/* | External (BLS data; career outcomes page) |
| https://www.linkedin.com/* | External (social) |
| https://applieddatascience.psd.uchicago.edu/* | External subdomain (advising portal) |

---

## 5. Page Details Table

| # | Depth | URL | Title | Content Type | Est. Words | Rendering |
|---|-------|-----|-------|-------------|-----------|-----------|
| 1 | D0 | /education/masters-programs/ms-in-applied-data-science/ | Master's in Applied Data Science | Program overview | 850 | Static HTML |
| 2 | D1 | /education/masters-programs/in-person-program/ | In-Person Program | Program format/curriculum | 3,200 | Static HTML |
| 3 | D1 | /education/masters-programs/online-program/ | Online Program | Program format/curriculum | 2,100 | Static HTML |
| 4 | D1 | .../ms-in-applied-data-science/capstone-projects/ | Capstone Projects | Program feature / sponsor info | 650 | Static HTML |
| 5 | D1 | .../ms-in-applied-data-science/course-progressions/ | Course Progressions | Curriculum / sample schedules | 2,200 | Static HTML (accordion) |
| 6 | D1 | .../ms-in-applied-data-science/how-to-apply/ | How to Apply | Admissions requirements | 1,300 | Static HTML |
| 7 | D1 | .../ms-in-applied-data-science/events-deadlines/ | Events & Deadlines | Events calendar / deadlines | 1,800 | Static HTML |
| 8 | D1 | /education/tuition-fees-aid/ | Tuition, Fees, & Aid | Financial information | 550 | Static HTML |
| 9 | D1 | .../ms-in-applied-data-science/our-students/ | Our Students | Student profiles / testimonials | 380 | Static HTML |
| 10 | D1 | .../ms-in-applied-data-science/instructors-staff/ | Faculty, Instructors, Staff | Faculty/staff directory | 8,500 | Static HTML |
| 11 | D1 | .../ms-in-applied-data-science/faqs/ | FAQs | FAQ — admissions / program | 3,000 | Static HTML (accordion) |
| 12 | D1 | /explore-the-ms-ads-campus/ | Explore the MS-ADS Campus | Campus info / virtual tour | 350 | Static HTML |
| 13 | D1 | .../ms-in-applied-data-science/career-outcomes/ | Career Outcomes | Career data / employer logos | 250 | Static HTML (image-embedded charts) |
| 14 | D2 | .../ms-in-applied-data-science/capstone-project-archive/ | Capstone Project Archive | Project index (10 featured) | 350 | Static HTML |
| 15 | D2 | /people/greg-green/ | Greg Green | Faculty bio | 425 | Static HTML |
| 16 | D2 | /people/arnab-bose-phd/ | Arnab Bose, PhD | Faculty bio | 290 | Static HTML |
| 17 | D2 | /people/patrick-vonesh/ | Patrick Vonesh | Staff bio | ~350 | Static HTML |
| 18 | D2 | /people/jose-alvarado/ | Jose Alvarado | Staff bio | ~350 | Static HTML |
| 19 | D2 | /people/matthew-harris-ridker/ | Matthew Harris-Ridker | Staff bio | ~350 | Static HTML |
| 20–66 | D2 | /people/[name]/ (×47) | [Faculty/Staff Name] | Faculty/staff bio | ~350 avg | Static HTML |
| 67–105 | D3 | /research/[capstone-slug]/ (×39) | [Capstone Project Title] | Capstone project case study | ~225 avg | Static HTML |

**Note on rows 20–66:** These are the remaining faculty and instructors listed on the instructors-staff D1 page. All URLs are in the flat listing (§3). Each is a terminal profile page with no in-scope sub-pages.

**Note on rows 67–105:** All 39 capstone project URLs are listed in the flat listing (§2 D3) and in the URL Tree. These were sourced from all 4 pages of the capstone-project-archive (paginated via JS; fetched 2026-04-19). The sampled page (Argonne/Rezzy) confirmed ~200–250 words of substantive, unique project content per page.

---

## 6. Concerns

### 1. Five URL alias pairs — redirect verification completed

**Description:** Five pages originally appeared to serve identical content at two distinct URL paths. HTTP HEAD requests with `allow_redirects=False` were run on all five pairs to determine the canonical (200) URL and the redirecting alias (301).

**Verification method:** `requests.head(url, allow_redirects=False)` — checked status code and `Location` header for each URL in the pair.

**Results:**

| Pair | Short / alias URL | Status | Long / canonical URL | Status | Classification |
|------|-------------------|--------|----------------------|--------|----------------|
| 1 | /how-to-apply/ | **301 → long** | /ms-in-applied-data-science/how-to-apply/ | 200 | **Redirect** — canonical is long form |
| 2 | /about-us/ | **301 → long** | /ms-in-applied-data-science/instructors-staff/ | 200 | **Redirect** — canonical is long form |
| 3 | /capstone-projects/ | **301 → long** | /ms-in-applied-data-science/capstone-projects/ | 200 | **Redirect** — canonical is long form |
| 4 | /education/masters-programs/in-person-program/ | **200** | /ms-in-applied-data-science/in-person-program/ | **301 → short** | **Redirect (reversed)** — canonical is SHORT form |
| 5 | /education/masters-programs/online-program/ | **200** | /ms-in-applied-data-science/online-program/ | **301 → short** | **Redirect (reversed)** — canonical is SHORT form |

**Key finding for pairs 4–5:** The redirect direction is the opposite of what was assumed. The URLs containing `/ms-in-applied-data-science/` are the aliases; they redirect to the shorter `/education/masters-programs/in-person-program/` and `/education/masters-programs/online-program/` paths, which are the canonical 200 pages. The sitemap has been updated accordingly — canonical URLs for in-person-program and online-program now use the short form, and the long-form variants are moved to Out-of-Scope as known aliases.

**Treatment per classification:**
- **Redirect (pairs 1–3):** Short form moves to Out-of-Scope as a known alias. Scraper uses long-form URL. No content deduplication needed — only one URL serves content.
- **Redirect reversed (pairs 4–5):** Long-form `/ms-in-applied-data-science/` variants move to Out-of-Scope as known aliases. Scraper uses short-form URL (the 200 canonical). No content deduplication needed.

---

### 2. Image-only employer data on Career Outcomes page — spec-compliant exclusion

**Description:** The Career Outcomes page presents its employer and internship data as image grids ("MS in Applied Data Science Internships 2025," "MS in Applied Data Science Alumni Careers"). The project spec (section "Data and Deliverables") limits the data source to "unstructured and textual content (including diagrams, charts but not images)." Employer logo grids are images and are therefore out of scope for ingestion by specification.

**Evidence:** https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/career-outcomes/ — ~250 words of body text confirmed; two image-based employer grids present but excluded per spec.

**Recommendation:** Scrape the ~250 words of text content as normal. Do not attempt image extraction or manual transcription of employer logos — this is a spec boundary, not a tooling gap.

**Note:** This exclusion will be documented in the project's Known Limitations section of the final writeup.

---

### 3. Accordion/collapsed content on two high-value pages

**Description:** FAQs and Course Progressions both use collapsible accordion widgets to present their main content. A static HTML scrape may capture the accordion structure but fail to capture the collapsed section text if it is rendered by JavaScript toggle (class toggling without re-fetch).

**Evidence:**
- FAQs: 5 accordion sections (Application Process, International Students, Online Program, MBA/MS, 2-Year Thesis Track) — ~3,000 words total when expanded.
- Course Progressions: 3 accordion tabs (Full-Time 4-quarter, Part-Time 6-quarter, 2-Year Full-Time Thesis Track) — ~2,200 words total when expanded.

**Recommendation:** After scraping, verify that the raw text includes expanded FAQ answers and quarter-by-quarter course listings. If not, use a JS-rendering scraper (e.g., Playwright) for these two pages only, as noted in CLAUDE.md's scraping fallback guidance.

---

### 4. Dynamic / frequently-changing content on Events & Deadlines

**Description:** The Events & Deadlines page lists upcoming info sessions, application deadlines, and event registrations. This content changes with each admissions cycle. Static scrape captures the current state only.

**Evidence:** https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/events-deadlines/ — links to external apply-psd.uchicago.edu registration forms and session-specific URLs.

**Recommendation:** Scrape for deadlines and event descriptions; exclude direct registration links. Flag this page for re-scrape before each admissions cycle if the chatbot will be used long-term.

---



## 7. Recommended Scrape List

**Should**: MS-ADS scoped content — program sidebar pages, faculty/staff profiles, and the capstone archive. These are the primary sources for the questions this chatbot is meant to answer; scrape all in Stage 1.

**Optional**: Content reachable from the sitemap but not MS-ADS-specific — individual capstone project case studies (short, narrow-topic), DSI-general institutional pages (About, Research hub, News, Events, etc.), and footer-only external links. These would add noise to retrieval without meaningfully improving coverage; include only if post-evaluation retrieval gaps point to a specific need.

**Skip**: Confirmed 301 alias URLs (record alias → canonical in `url_aliases` metadata only; do not fetch) and external domains with no MS-ADS textual content.

---

### Should

| URL | Notes |
|-----|-------|
| https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/ | Program overview |
| https://datascience.uchicago.edu/education/masters-programs/in-person-program/ | In-person format; long-form variant is a 301 alias |
| https://datascience.uchicago.edu/education/masters-programs/online-program/ | Online format; long-form variant is a 301 alias |
| .../ms-in-applied-data-science/course-progressions/ | Curriculum schedules; verify accordion content captured |
| .../ms-in-applied-data-science/faqs/ | Admissions and program Q&A; verify accordion content captured |
| .../ms-in-applied-data-science/how-to-apply/ | Admissions requirements |
| .../ms-in-applied-data-science/instructors-staff/ | Faculty/staff directory; gateway to ~50 individual profiles |
| .../ms-in-applied-data-science/capstone-projects/ | Capstone program overview and sponsors |
| .../ms-in-applied-data-science/capstone-project-archive/ | Featured capstone project list; gateway to D3 pages |
| .../ms-in-applied-data-science/events-deadlines/ | Application deadlines and info sessions; re-scrape each admissions cycle |
| .../ms-in-applied-data-science/career-outcomes/ | Career outcomes text; employer logo grids excluded per spec — see Concern #2 |
| https://datascience.uchicago.edu/education/tuition-fees-aid/ | Tuition and financial aid |
| .../ms-in-applied-data-science/our-students/ | Student profiles and testimonials |
| https://datascience.uchicago.edu/explore-the-ms-ads-campus/ | Campus environment |
| /people/[name]/ — all ~50 profiles | Faculty/staff bios; apply PII redaction before storing — see Concern #5 |

---

### Optional

**Capstone research project pages — 39 pages**
Individual project case studies at `/research/[capstone-slug]/`. Useful only for narrow queries about a specific sponsor or method. See §2 D3 Flat Depth Listing for the full URL list (rows 67–105).

**DSI-general pages — 14 pages**
Not MS-ADS specific; likely to dilute retrieval precision with off-topic chunks.

| URL | Notes |
|-----|-------|
| https://datascience.uchicago.edu/ | DSI homepage |
| https://datascience.uchicago.edu/education/ | All-programs education hub |
| https://datascience.uchicago.edu/research/ | DSI research hub |
| https://datascience.uchicago.edu/outreach/ | DSI outreach |
| https://datascience.uchicago.edu/insights/ | DSI blog posts |
| https://datascience.uchicago.edu/about/ | DSI About |
| https://datascience.uchicago.edu/about/about-dsi/ | About DSI detail |
| https://datascience.uchicago.edu/about/leadership-staff/ | DSI leadership |
| https://datascience.uchicago.edu/news-events/news/ | DSI news |
| https://datascience.uchicago.edu/news-events/events/ | DSI events |
| https://datascience.uchicago.edu/about/jobs/ | DSI jobs |
| https://datascience.uchicago.edu/newsletter-archive/ | Newsletter archive |
| https://datascience.uchicago.edu/nondiscrimination-statement/ | Institutional policy |
| https://datascience.uchicago.edu/about/contact/ | Contact |

**Footer-only links — 9 entries**
External domains or social platforms; no MS-ADS textual content.

| URL | Notes |
|-----|-------|
| https://physicalsciences.uchicago.edu/ | External domain (Physical Sciences Division) |
| https://accessibility.uchicago.edu/ | External accessibility statement |
| X/Twitter, Bluesky, LinkedIn, YouTube, GitHub, Instagram, TikTok | External social platforms |

---

### Skip

**301 alias URLs — record in `url_aliases` metadata on the canonical page's chunks only:**

| Alias URL (301) | Canonical URL (200) |
|-----------------|---------------------|
| https://datascience.uchicago.edu/how-to-apply/ | .../ms-in-applied-data-science/how-to-apply/ |
| https://datascience.uchicago.edu/about-us/ | .../ms-in-applied-data-science/instructors-staff/ |
| https://datascience.uchicago.edu/capstone-projects/ | .../ms-in-applied-data-science/capstone-projects/ |
| .../ms-in-applied-data-science/in-person-program/ | /education/masters-programs/in-person-program/ |
| .../ms-in-applied-data-science/online-program/ | /education/masters-programs/online-program/ |

**External domains — no MS-ADS content:**

| URL pattern | Reason |
|-------------|--------|
| https://apply-psd.uchicago.edu/* | External application portal; form-only |
| https://www.chicagobooth.edu/* | External domain (Booth joint degree reference) |
| https://grad.uchicago.edu/* | External domain (graduate admissions requirements) |
| https://internationalaffairs.uchicago.edu/* | External domain (CPT/visa info) |
| https://bursar.uchicago.edu/* | External domain (tuition billing) |
| https://financialaid.uchicago.edu/* | External domain (financial aid) |
| https://applieddatascience.psd.uchicago.edu/* | External subdomain (advising portal) |
| https://www.uchicago.edu/* | External domain (main university site) |
| https://www.bls.gov/* | External (BLS labor statistics) |

---
