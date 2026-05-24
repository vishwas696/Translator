# Document Translation Edge Cases

This note tracks document structures and translation risks to support later work on DOCX, EPUB, PDF, and other formats.

DOCX/EPUB implementation coverage is tracked in `DOCX_EPUB_COVERAGE_MATRIX.md`.

## Supported Format Scope

- Primary supported formats: DOCX and EPUB.
- Experimental/dev format: TXT.
- Deferred format: PDF.

DOCX and EPUB are prioritized because they expose real document structure through Word XML or EPUB XHTML. TXT has little structure beyond paragraphs. PDF is layout-first and requires heuristic reconstruction, so it should remain out of scope until the DOCX/EPUB pipeline is solid.

## Text Structure

- Book title, subtitle, author name
- Front matter: dedication, copyright, acknowledgements
- Chapter titles and section/subsection headings
- Normal paragraphs and very long paragraphs
- Dialogue paragraphs
- Poetry, verse, lyrics
- Epigraphs and chapter-opening quotes
- Pull quotes and callouts
- Footnotes and endnotes
- Page headers, footers, and page numbers
- Table of contents entries
- Index and glossary entries
- Bibliography and references
- Captions

## Formatting

- Bold, italic, underline
- Small caps
- Superscript and subscript
- Hyperlinks
- Inline code and code blocks
- Block quotes
- Ordered, unordered, and nested lists
- Indentation
- Intentional line breaks
- Scene breaks such as `***`
- Drop caps
- Text boxes and sidebars
- Marginal notes

## Images

- Cover image
- Decorative images
- Inline images inside text flow
- Full-page illustrations
- Diagrams with embedded text
- Screenshots with embedded text
- Maps with labels
- Image captions
- Alt text
- OCR-needed image text
- Images that should not be translated
- Images that need translated replacement text layered back in

Initial MVP policy:

- Preserve images as-is.
- Translate captions if present.
- Preserve or translate alt text depending on export target.
- Do not OCR embedded image text initially.

## Tables

- Simple tables
- Tables with merged cells
- Tables with header rows/columns
- Tables with footnotes
- Tables where only some columns should be translated
- Tables containing numbers, dates, units, currencies, or formulas
- Tables too large to fit in one chunk
- Table captions
- Repeated table headers across pages

Initial MVP policy:

- Preserve table structure.
- Translate text cells.
- Preserve numbers, dates, units, formulas, and identifiers unless rules say otherwise.
- Split large tables by row groups when needed.

## Special Content

- Math formulas and equations
- Chemical formulas
- Legal clauses
- Forms and questionnaires
- Checkboxes
- Timelines
- Recipes
- Dictionaries and glossaries
- Already bilingual text
- Text in multiple source languages

## Math, Formulas, And Geometry

- Inline formulas such as `a^2 + b^2 = c^2`
- Display equations
- LaTeX / MathML / OMML equation objects
- Fractions, radicals, integrals, summations, limits
- Superscript and subscript notation
- Variables that should not be translated
- Function names such as `sin`, `cos`, `log`, `max`, `min`
- Greek symbols and mathematical symbols
- Equation numbers and references
- Geometry labels: points, lines, rays, angles, triangles, circles, arcs
- Diagram labels embedded in images
- Units and dimensions: `cm`, `m²`, `kg`, degrees, radians
- Scientific notation and significant figures
- Word problems where prose should translate but numeric/formula content must remain stable
- Mixed prose and formula text inside tables

Initial MVP policy:

- Preserve formulas exactly unless the source format exposes surrounding prose separately.
- Translate explanatory prose around equations.
- Preserve variable names, equation numbers, units, and references.
- Preserve geometry labels such as `A`, `B`, `AB`, `∠ABC`, `△ABC`.
- Do not OCR or edit formulas embedded inside images initially.
- Translate captions and surrounding text for diagrams, but preserve diagram artwork as-is.

Reconstruction risks:

- Superscripts/subscripts flattened into normal text.
- Equation objects converted into lossy plain text.
- Variables accidentally translated as words.
- Decimal separators or units localized inconsistently.
- Diagram labels translated in prose but not on the image.
- Cross-references to equation numbers broken.

## Layout And File-Specific Issues

- DOCX styles
- EPUB HTML structure
- PDF extraction artifacts
- OCR errors
- Hyphenated line breaks
- Broken paragraphs from extraction
- Multi-column layout
- Text wrapping around images
- Headers/footers mixed into body text
- Floating objects
- Text boxes
- Comments, revisions, and tracked changes
- Hidden text
- Bookmarks and cross-references
- Internal links
- Page breaks and section breaks

## Translation Rules

- Proper nouns
- Character names
- Place names
- Invented/fantasy terms
- Measurements
- Currency
- Dates
- Idioms
- Formal/informal pronouns
- Gendered language
- Tone consistency
- Repeated phrases and catchphrases
- Dialogue voice per character
- Do-not-translate terms
- Brand names
- Acronyms
- Intentional short phrases in a language different from the document's main source language

Multilingual prose policy:

- Preserve short phrases in a language different from the document's main source language when the author appears to use them intentionally.
- Translate the surrounding narrative into the target language.
- Preserve author-provided explanations of those phrases; do not add new explanations.
- If a large section is in another language, flag/review later rather than blindly preserving or translating everything.

## Reconstruction Risks

- Missing chunk
- Duplicated chunk
- Model merges paragraphs
- Model changes IDs
- Model drops formatting
- Model translates placeholders
- Invalid JSON output
- Output too long
- Translated text expands and breaks layout
- Table columns overflow
- Captions detach from images
- Footnote references mismatch
- Hyperlinks lost
- Cross-references broken

## Planned Block Model

Move from paragraph-only chunking toward document blocks:

```json
{
  "block_id": "b0042",
  "type": "paragraph",
  "translate": true,
  "preserve": false,
  "text": "Source text..."
}
```

Suggested initial block types:

- `paragraph`
- `heading`
- `list_item`
- `quote`
- `footnote`
- `table`
- `image`
- `caption`
- `page_break`
- `special`
