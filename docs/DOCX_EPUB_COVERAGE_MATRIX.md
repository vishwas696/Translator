# DOCX / EPUB Coverage Matrix

This matrix uses `DOCUMENT_EDGE_CASES.md` as the source edge-case list and
`document_adapters.py` as the implementation context.

Current coverage includes parsing/detection, translation-text extraction, and an
initial package writer that copies DOCX/EPUB inputs and replaces detected
translatable text sequentially. Exact inline-range reconstruction, layout
overflow handling, and full visual fidelity are still later hardening work.

Legend: Covered = implemented for current extraction scope; Partial = detected
or extracted with known limits; Deferred = planned/not implemented yet; Not
applicable = does not apply meaningfully to that format.

| Edge case | DOCX coverage | EPUB coverage | Current handling | Remaining risk / next action |
| --- | --- | --- | --- | --- |
| Format detection and loading | Covered | Covered | `.docx` and `.epub` route to dedicated loaders; `.txt` remains dev support. | Add clearer validation for malformed archives and unsupported variants. |
| Book title, subtitle, author | Partial | Partial | EPUB extracts OPF title/creator/publisher/description; DOCX can detect title-like content only if it appears as body text or styled heading. | Add DOCX core properties and richer metadata extraction. |
| Front matter | Partial | Partial | Front matter in body/spine is extracted as normal blocks; EPUB spine order is respected. | Add semantic classification for dedication, copyright, acknowledgements, etc. |
| Chapter titles and headings | Covered | Covered | DOCX detects `HeadingN` styles; EPUB detects `h1`-`h6` and records heading level; writer updates heading text in place. | Exact style/tag hierarchy should be validated on complex files. |
| Normal paragraphs | Covered | Covered | Body paragraphs become translatable `paragraph` blocks. | Very complex inline structure is flattened for translation text. |
| Very long paragraphs | Partial | Partial | Extracted as one block if present. | Chunking/splitting policy must prevent model/output limits later. |
| Dialogue paragraphs | Partial | Partial | Extracted as normal paragraphs. | No special dialogue voice or speaker detection at adapter level. |
| Poetry, verse, lyrics | Partial | Partial | Intentional line breaks are preserved when represented as DOCX breaks or HTML text. | Verse semantics/stanza grouping are not reliably classified. |
| Epigraphs and chapter-opening quotes | Partial | Partial | DOCX quote/epigraph-like styles map to `quote`; EPUB `blockquote` maps to `quote`. | Style/class coverage may miss custom epigraph markup. |
| Pull quotes and callouts | Partial | Partial | Quote styles and EPUB blockquotes can be captured; sidebars/asides have limited handling. | Need richer callout/sidebar classification and export policy. |
| Footnotes and endnotes | Partial | Partial | DOCX reads `footnotes.xml` and `endnotes.xml`; EPUB detects footnote/endnote semantics in `aside`/`nav` metadata. | Reference markers and round-trip linkage are not reconstructed yet. |
| Page headers, footers, page numbers | Partial | Not applicable | DOCX scans related header/footer parts and extracts text. EPUB has no fixed page header/footer model. | Page numbers should often be preserved or skipped, not blindly translated. |
| Table of contents entries | Partial | Partial | DOCX style names containing TOC become `toc_entry`; EPUB `nav`/TOC semantics become `toc` and are not translated. | Need export-safe link preservation and generated TOC policy. |
| Index and glossary entries | Partial | Partial | DOCX style names containing index/reference/bibliography are classified; EPUB glossary/index entries are plain content unless semantically marked. | Add explicit EPUB semantic detection for glossary/index. |
| Bibliography and references | Partial | Partial | DOCX reference/bibliography style names can map to `reference`; EPUB extracts as normal body text unless marked. | Citation fields/cross-references are not preserved through export yet. |
| Captions | Partial | Covered | DOCX caption-like styles become `caption`; EPUB `figcaption` becomes translatable `caption`. | DOCX captions depend on style naming; image/table association is not reconstructed. |
| Bold, italic, underline | Partial | Partial | DOCX records formatting flags at block level; EPUB inline formatting is flattened into text. | Inline-range fidelity is not preserved in translation text or export yet. |
| Small caps | Partial | Partial | DOCX detects small caps at block level; EPUB CSS/class small caps may remain only as metadata class/style context. | Need inline style span preservation. |
| Superscript and subscript | Partial | Partial | DOCX records superscript/subscript flags and now emits protected inline placeholder tokens for short formatting-sensitive sup/sub runs; the DOCX writer can rebuild those runs when tokens return unchanged. EPUB text is extracted but inline tag/style semantics are not preserved. | Extend placeholder protection to EPUB and richer DOCX inline ranges such as hyperlinks, citations, and mixed bold/italic spans. |
| Hyperlinks and internal links | Partial | Partial | DOCX records hyperlink target/anchor metadata; EPUB records hrefs found inside a block; package writer preserves package files but may flatten inline link ranges inside replaced text. | Link ranges, anchors, and precise round-trip insertion need hardening. |
| Inline code and code blocks | Partial | Covered | EPUB `pre`/`code` blocks are non-translatable; DOCX has no dedicated code detection except style metadata. | Add DOCX code-style detection; preserve inline code ranges. |
| Block quotes | Partial | Covered | DOCX quote-like styles map to `quote`; EPUB `blockquote` maps to `quote`. | DOCX depends on style naming; nested quote structure is flattened. |
| Ordered, unordered, and nested lists | Partial | Partial | DOCX detects numbering metadata; EPUB detects `li`, list type, and nesting level. | Exact numbering/bullets and nested reconstruction are later-phase work. |
| Indentation | Deferred | Partial | EPUB classes/ids are kept as metadata; DOCX paragraph style is kept, but indentation values are not extracted. | Add indentation metadata where layout-sensitive translation needs it. |
| Intentional line breaks | Covered | Partial | DOCX `w:br` becomes newline; EPUB extracted text uses normalized spaces and may collapse some breaks. | Preserve HTML `<br>` and stanza breaks more deliberately. |
| Scene breaks such as `***` | Covered | Covered | Ornamental breaks are classified as `special` blocks with `preserve_exact` metadata. | Export should preserve them exactly and avoid decorative normalization. |
| Drop caps | Deferred | Partial | DOCX does not detect drop caps; EPUB may retain class metadata but no explicit classification. | Add style/class-based detection and preserve artwork/layout later. |
| Text boxes and sidebars | Partial | Partial | DOCX flags `contains_text_box`; EPUB `aside` becomes semantic `aside`/`special`. | DOCX text-box content may be flattened into paragraph text without placement fidelity. |
| Marginal notes | Partial | Partial | DOCX comments are extracted as `comment` blocks; EPUB side notes may be captured as `aside`/`special`. | Need a clearer policy for translating, preserving, or excluding editorial notes. |
| Cover image | Partial | Covered | DOCX detects images when they appear in the body; EPUB reads OPF cover-image metadata and emits a non-translatable image block. | DOCX cover pages and export preservation still need explicit handling. |
| Decorative images | Partial | Partial | Image blocks are non-translatable with relationship/src metadata. | Need decorative vs content image classification. |
| Inline images inside text flow | Partial | Partial | DOCX image relationship metadata and EPUB `img src/alt` are captured. | Inline position and surrounding run placement are not round-tripped. |
| Full-page illustrations | Partial | Partial | Detected as image blocks if present in body/spine HTML. | Need layout/export policy to preserve pagination and sizing. |
| Diagrams, screenshots, maps with embedded text | Deferred | Deferred | Images are preserved as image blocks; embedded text is not OCRed. | OCR and translated image replacement/layering are explicitly later work. |
| Image captions | Partial | Covered | Caption text is extracted when styled in DOCX or in EPUB `figcaption`. | DOCX caption association with image is heuristic. |
| Alt text | Partial | Partial | DOCX image title/description and EPUB `alt` are captured as metadata, not translation text. | Decide whether/when alt text should be translated for each export target. |
| Images that should not be translated | Covered | Covered | Image blocks are `translate=False`. | Add policy metadata for OCR exceptions and replacement workflows. |
| Simple tables | Covered | Covered | Tables become translatable `table` blocks with rows and tab/newline-rendered text; writer can place tab/newline-preserving translations back into matching table cells. | Mismatched translated table shape falls back with warnings. |
| Tables with merged cells | Partial | Partial | DOCX records grid span/vertical merge; EPUB records colspan/rowspan; writer keeps the original table structure while replacing cell text. | Need validation on complex merged-cell topology. |
| Tables with header rows/columns | Partial | Partial | DOCX detects `tblHeader`; EPUB detects `th` cells and scope metadata. | Header columns and repeated headers need more explicit handling. |
| Table footnotes | Deferred | Deferred | Footnotes and tables are extracted separately where detectable. | Need association between table footnotes and table cells/captions. |
| Partially translatable table columns | Deferred | Deferred | Entire table text is currently translatable. | Add per-cell/per-column translate flags and rules. |
| Numeric, date, unit, currency, formula table cells | Partial | Partial | EPUB marks cells that look numeric/formula-like; DOCX flags equations inside cells. | Translation pipeline must preserve numbers, units, formulas, and identifiers. |
| Tables too large for one chunk | Deferred | Deferred | Adapter emits each table as one block. | Add row-group splitting for large tables. |
| Table captions | Partial | Partial | Captions are extracted when styled/marked separately. | Need table-caption association and export placement. |
| Math formulas and equations | Partial | Partial | DOCX detects OMML equations and non-translatable equation-only blocks; EPUB records `contains_math` for MathML in metadata. | Formula objects are not round-tripped; surrounding prose/formula boundaries may be lossy. |
| Chemical formulas | Partial | Partial | Extracted as text unless represented as formula/image. | Need preservation heuristics for formulas and symbols. |
| Legal clauses | Covered | Covered | Extracted as normal text. | Requires prompt/rule support, not adapter-specific handling. |
| Forms, questionnaires, checkboxes | Partial | Partial | DOCX and EPUB blocks flag form controls and checkboxes when detected. | Field values/states and exact interactive reconstruction are later-phase work. |
| Timelines and recipes | Covered | Covered | Extracted as paragraphs, lists, or tables depending on structure. | Semantic classification is not implemented. |
| Dictionaries and glossaries | Partial | Partial | Extracted as text/list/table content; DOCX style names may identify glossary/index entries. | Add glossary-specific structure and terminology policies. |
| Already bilingual or multilingual text | Partial | Partial | EPUB block language metadata is captured; DOCX language runs are not extracted. | Add language/run detection and review flags for large mixed-language sections. |
| Variables, function names, Greek/math symbols | Partial | Partial | Formula-like content may be detected in DOCX equations or EPUB tables/MathML metadata. | Preserve/no-translate rules must be enforced downstream. |
| Equation numbers and references | Partial | Partial | DOCX field codes/bookmarks are captured; EPUB hrefs/ids are captured. | Cross-reference reconstruction/export remains high risk. |
| Geometry labels and diagram labels | Partial | Deferred | Text labels in prose are extracted; labels embedded in images are not OCRed. | Add no-translate rules for labels and later OCR/replacement workflows. |
| DOCX styles | Partial | Not applicable | Paragraph style IDs are recorded for DOCX blocks. | Style hierarchy, run styles, and exact reapplication are later-phase work. |
| EPUB HTML structure | Not applicable | Partial | Source item, tag, id, class, role, `epub:type`, language, and href metadata are recorded. | Full DOM/range mapping is not preserved for reconstruction yet. |
| PDF extraction artifacts | Not applicable | Not applicable | PDF is intentionally deferred. | Revisit only after DOCX/EPUB pipeline is solid. |
| OCR errors | Not applicable | Not applicable | OCR is not performed. | Add OCR pipeline only when image/PDF support is in scope. |
| Hyphenated line breaks and broken paragraphs | Partial | Partial | Basic inline whitespace normalization runs during extraction. | No explicit dehyphenation or paragraph repair heuristics yet. |
| Multi-column layout and text wrapping around images | Deferred | Partial | EPUB classes may hint at layout; DOCX layout metadata is not extracted. | Exact layout reconstruction belongs to export phase. |
| Floating objects | Partial | Partial | Images/text boxes may be detected, but float positioning is not preserved. | Capture anchors/positioning if layout fidelity becomes required. |
| Comments, revisions, tracked changes | Partial | Not applicable | DOCX extracts comments as `comment` blocks and flags revision markup. EPUB has no equivalent tracked-change model. | Need an accept/reject policy for tracked changes before production export. |
| Hidden text | Partial | Partial | DOCX flags hidden text; EPUB skips hidden elements. | Decide whether hidden DOCX text should be skipped, preserved, or reviewed. |
| Bookmarks and cross-references | Partial | Partial | DOCX bookmarks/field codes and EPUB ids/hrefs are metadata. | Rebuilding links and references is a later export concern. |
| Page breaks and section breaks | Covered | Partial | DOCX emits non-translatable `page_break` and `section_break` blocks; EPUB emits page-break spans when explicitly marked. | Export must reinsert fixed breaks only where the target format supports them. |
| Proper nouns, names, invented terms, brands, acronyms | Covered | Covered | Extracted as normal translatable text for downstream glossary/prompt rules. | Adapter does not identify or protect these terms by itself. |
| Measurements, currency, dates | Partial | Partial | Extracted as text; EPUB table cells can be flagged as numeric-looking. | Downstream translation rules must prevent unwanted localization. |
| Idioms, pronouns, gendered language, tone, voice | Covered | Covered | Source prose is extracted for translation. | These are translation-quality concerns, not format-adapter concerns. |
| Do-not-translate terms and intentional foreign phrases | Partial | Partial | EPUB language metadata can help; DOCX language metadata is not currently captured. | Add language/run detection and connect to glossary/review rules. |
| Missing, duplicated, merged, or reordered chunks | Partial | Partial | Stable block IDs are generated in extraction order. | Need validation after translation and before export. |
| Model changes IDs or placeholders | Deferred | Deferred | Adapter creates IDs but does not validate model output. | Add schema validation for translated block payloads. |
| Model drops formatting | Partial | Partial | Some formatting/style metadata is captured. DOCX has an initial placeholder preservation layer for short sup/sub inline artifacts. | Expand placeholder-aware reconstruction beyond sup/sub runs to links, footnote refs, equations, character styles, and EPUB inline markup. |
| Invalid JSON or output too long | Not applicable | Not applicable | Adapter does not call the model. | Handle in translation orchestration and chunking. |
| Translated text expansion and layout overflow | Deferred | Deferred | Initial export exists, but no layout measurement or overflow remediation is performed. | Add export previews, overflow detection, and layout-specific remediation. |
| Captions detach from images | Partial | Partial | Captions and images can be separate adjacent blocks. | Need explicit association IDs during extraction/export. |
| Hyperlinks and cross-references lost | Partial | Partial | Link/reference metadata is captured; package writer may preserve surrounding package relationships but can flatten inline ranges inside replaced blocks. | Round-trip reconstruction must preserve link ranges and targets precisely. |
