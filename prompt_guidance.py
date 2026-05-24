STATIC_TRANSLATION_BRIEF = """
You are translating source content for publication or reader-facing use.
Translate faithfully and naturally into the target language.
Do not translate word-by-word when the target language needs different grammar,
idiom, or sentence flow. Rephrase naturally where needed, while preserving the
exact meaning, tone, emphasis, facts, and formatting.
Preserve meaning, tone, names, chronology, source style, and register.
Do not summarize, omit, add, or explain source content.
Preserve the structure present inside the current chunk:
paragraph breaks, headings, line breaks, list markers, and visible formatting markers.
If the chunk includes a heading, translate it as a heading.
If the chunk continues a paragraph, translate it as continuation prose.
Do not merge, reorder, or drop content.
Preserve numbers, dates, URLs, IDs, and placeholders unless localization is clearly required.
Soft hyphens, nonbreaking spaces, and discretionary hyphenation inside ordinary
words are layout artifacts. Translate the word naturally unless it is code,
an identifier, a URL, a proper noun, or a protected term.
Preserve intentional short phrases that are in a language different from the
document's main source language, especially when the author explains their
meaning nearby. Translate the surrounding narrative and do not add or remove
explanations.
Do not add parenthetical translations or explanations for non-source-language
phrases unless the source itself provides that explanation.
Use relevant glossary entries consistently.
""".strip()


CONTENT_FORM_GUIDANCE = {
    "book": (
        "Treat this as a book or manuscript. Preserve chapter structure, narrative "
        "continuity, authorial voice, literary style, scene flow, reader immersion, "
        "recurring motifs, object symbolism, and emotional callbacks."
    ),
    "article": (
        "Treat this as a standalone article or essay. Preserve the headline, "
        "subheadings, argument or news structure, attribution, quotations, and concise flow."
    ),
    "academic_paper": (
        "Treat this as a scholarly paper. Preserve the title, abstract, section "
        "hierarchy, citations, terminology, equations, tables, figure references, "
        "methods, findings, limitations, and reference integrity."
    ),
    "report": (
        "Treat this as a formal report. Preserve executive-summary logic, section "
        "hierarchy, metrics, findings, recommendations, risks, tables, charts, "
        "captions, and evidence."
    ),
    "manual_or_documentation": (
        "Treat this as instructional or reference documentation. Preserve steps, "
        "warnings, UI labels, commands, code, parameters, examples, troubleshooting "
        "logic, and exact operational conditions."
    ),
    "legal_or_policy": (
        "Treat this as legal or policy text. Translate conservatively and preserve "
        "defined terms, parties, obligations, scope, clauses, numbering, cross-references, "
        "dates, and intentional ambiguity. Use conventional target-language legal "
        "boilerplate for clauses, defined-term sentences, limitation-of-liability "
        "language, indemnity/freedom-from-claim language, and signature blocks."
    ),
}


DOCUMENT_TYPE_GUIDANCE = {
    "general": "Use a natural, faithful translation style appropriate to the source.",
    "adventure": (
        "Preserve momentum, clear action, stakes, setting detail, and scene geography. "
        "Keep prose energetic without adding melodrama."
    ),
    "academic": (
        "Use formal, precise language. Preserve citations, claims, definitions, "
        "argument structure, and discipline-specific terminology."
    ),
    "architecture": (
        "Preserve spatial descriptions, measurements, materials, project names, "
        "design terminology, and relationships between drawings, captions, and text."
    ),
    "art_design": (
        "Preserve visual terminology, medium, style, period, materials, artist names, "
        "and interpretive nuance. Avoid flattening aesthetic description."
    ),
    "autobiography": (
        "Preserve the first-person voice, chronology, emotional stance, and named "
        "people or places. Do not make the narrator sound more formal than intended."
    ),
    "biography": (
        "Use clear narrative nonfiction. Preserve chronology, names, dates, titles, "
        "relationships, and factual claims."
    ),
    "business": (
        "Use professional, concise language. Preserve product names, metrics, process "
        "terms, roles, and commercial intent."
    ),
    "business_report": (
        "Prioritize clarity, structure, metrics, recommendations, risks, and business "
        "terms. Preserve tables, headings, KPIs, and executive-summary logic."
    ),
    "case_study": (
        "Preserve the problem-solution-results structure, client/product names, "
        "metrics, timelines, and evidence. Keep tone concrete and credible."
    ),
    "contract": (
        "Translate conservatively and precisely. Preserve parties, defined terms, "
        "obligations, conditions, numbering, dates, and legal ambiguity. Prefer "
        "established target-language contract phrasing over literal wording when a "
        "literal rendering weakens legal force or sounds nonstandard."
    ),
    "literary": (
        "Preserve literary voice, imagery, rhythm, emotional subtext, recurring "
        "symbols, context-sensitive callbacks, and paragraph-level flow. Avoid "
        "flattening metaphors, over-explaining, or carrying over source-language "
        "phrasing that sounds translated in the target language."
    ),
    "children": (
        "Use clear, warm, age-appropriate language. Keep sentences natural and easy "
        "to follow while preserving the story's charm."
    ),
    "comic": (
        "Preserve panel-friendly brevity, dialogue punch, captions, sound effects, "
        "character voice, and visual context. Avoid text expansion where space matters."
    ),
    "commercial_fiction": (
        "Prioritize readability, scene clarity, voice, pacing, and emotional payoff. "
        "Keep prose smooth and accessible without over-polishing the author's style."
    ),
    "cookbook": (
        "Preserve ingredient names, quantities, temperatures, timing, sequence, and "
        "food terminology. Translate instructions clearly and consistently."
    ),
    "crime": (
        "Preserve clues, evidence, procedure, tension, alibis, timelines, and tone. "
        "Do not clarify ambiguity that the source intentionally leaves unresolved."
    ),
    "dictionary": (
        "Preserve entry structure, headwords, part-of-speech labels, examples, "
        "cross-references, and ordering. Translate definitions consistently."
    ),
    "drama": (
        "Preserve speaker labels, stage directions, line breaks, cues, subtext, and "
        "performability. Keep dialogue speakable."
    ),
    "dystopian": (
        "Preserve institutional terminology, social rules, atmosphere, threat, and "
        "worldbuilding consistency. Keep invented political terms stable."
    ),
    "economics": (
        "Preserve economic terminology, causal claims, data, models, units, and "
        "policy distinctions. Prefer precision over rhetorical flourish."
    ),
    "educational": (
        "Use clear instructional language. Preserve learning objectives, examples, "
        "questions, answer cues, and progression from simple to complex."
    ),
    "email_correspondence": (
        "Preserve sender intent, politeness level, requests, deadlines, names, and "
        "action items. Keep tone appropriate to the relationship."
    ),
    "essay": (
        "Preserve thesis, voice, transitions, examples, and rhetorical movement. "
        "Avoid making informal essays sound academic unless the source does."
    ),
    "fairy_tale": (
        "Preserve simple narrative cadence, archetypal language, repetition, wonder, "
        "and moral clarity. Keep the timeless storybook feel."
    ),
    "horror": (
        "Preserve suspense, dread, pacing, sensory detail, and atmosphere. Avoid "
        "softening unsettling imagery."
    ),
    "fantasy": (
        "Preserve world-specific terms, invented names, titles, lore, and magical "
        "concepts consistently."
    ),
    "finance": (
        "Preserve financial terminology, amounts, currencies, dates, risk language, "
        "assumptions, and compliance-sensitive wording."
    ),
    "folktale": (
        "Preserve oral-storytelling cadence, repetition, cultural references, moral "
        "framing, and simple direct language."
    ),
    "game_rulebook": (
        "Preserve rule logic, components, turn order, conditions, exceptions, examples, "
        "and defined terms. Prefer unambiguous operational language."
    ),
    "grant_proposal": (
        "Preserve objectives, outcomes, budget language, eligibility claims, impact, "
        "timeline, and funder-facing tone."
    ),
    "graphic_novel": (
        "Preserve panel sequence, dialogue economy, captions, sound effects, character "
        "voice, and continuity with visual storytelling."
    ),
    "health_wellness": (
        "Use clear, careful, accessible language. Preserve cautions, symptoms, steps, "
        "measurements, and advice boundaries."
    ),
    "historical_fiction": (
        "Preserve period feel, social titles, historical references, dialogue register, "
        "and setting detail without making the prose archaic unless the source is."
    ),
    "history": (
        "Preserve chronology, dates, names, places, cause-effect claims, quotations, "
        "and historiographical nuance."
    ),
    "humor": (
        "Preserve comic timing, setup, payoff, irony, wordplay, and voice. Adapt jokes "
        "only when literal translation would lose the intended effect."
    ),
    "journalism": (
        "Use concise, factual language. Preserve attribution, quotes, dates, places, "
        "numbers, source distinctions, and news-style clarity."
    ),
    "legal": (
        "Translate conservatively and precisely. Preserve defined terms, obligations, "
        "references, numbering, and ambiguity where present."
    ),
    "leadership": (
        "Use clear, professional language. Preserve frameworks, principles, examples, "
        "action steps, and motivational-but-credible tone."
    ),
    "lifestyle": (
        "Use approachable, polished language. Preserve practical advice, tone, product "
        "or place names, and reader-facing warmth."
    ),
    "magical_realism": (
        "Preserve the matter-of-fact treatment of uncanny events, cultural texture, "
        "symbolism, and lyrical restraint. Do not over-explain magic."
    ),
    "manual": (
        "Preserve step order, warnings, labels, UI text, part names, measurements, and "
        "conditions. Use direct, unambiguous instructions."
    ),
    "marketing": (
        "Preserve persuasive intent, benefits, calls to action, brand terms, audience "
        "fit, and emotional positioning while sounding natural in the target language."
    ),
    "medical": (
        "Prioritize clinical accuracy, safety, dosage or measurement precision, "
        "symptom terminology, contraindications, and uncertainty."
    ),
    "memoir": (
        "Preserve personal voice, memory texture, chronology, emotional honesty, and "
        "cultural context. Do not over-formalize intimate passages."
    ),
    "middle_grade": (
        "Use vivid, age-appropriate language for independent young readers. Preserve "
        "humor, emotion, clarity, and adventure without sounding childish."
    ),
    "music": (
        "Preserve song titles, performer names, notation references, genre terms, "
        "instrument names, tempo markings, and performance context."
    ),
    "mystery": (
        "Preserve clues, red herrings, timing, withholding of information, atmosphere, "
        "and investigative logic."
    ),
    "mythology": (
        "Preserve names, epithets, ritual language, symbolic weight, genealogy, and "
        "elevated or oral-register cadence."
    ),
    "new_adult": (
        "Preserve contemporary voice, emotional intensity, relationship dynamics, "
        "identity themes, and mature-but-not-formal register."
    ),
    "parenting": (
        "Use warm, practical, nonjudgmental language. Preserve developmental stages, "
        "advice boundaries, examples, and cautions."
    ),
    "paranormal": (
        "Preserve supernatural terminology, atmosphere, romantic or suspense beats, "
        "and rules of the paranormal world."
    ),
    "personal_letter": (
        "Preserve intimacy, politeness, emotion, relationship cues, dates, names, and "
        "the writer's level of formality."
    ),
    "philosophy": (
        "Preserve conceptual distinctions, argument structure, definitions, examples, "
        "and ambiguity. Avoid simplifying technical philosophical terms."
    ),
    "photography": (
        "Preserve camera, lens, exposure, composition, printing, caption, and visual "
        "analysis terminology."
    ),
    "poetry": (
        "Preserve imagery, lineation, sound, rhythm, ambiguity, and emotional density. "
        "Prefer poetic effect over literal word order when necessary."
    ),
    "policy_government": (
        "Use precise institutional language. Preserve policy terms, obligations, "
        "scope, eligibility, dates, references, and procedural distinctions."
    ),
    "politics": (
        "Preserve ideological nuance, institutions, titles, dates, quotations, and "
        "argument framing. Avoid adding partisan color."
    ),
    "popular_science": (
        "Preserve scientific accuracy while keeping explanations accessible, engaging, "
        "and clear for general readers."
    ),
    "presentation": (
        "Preserve slide-like brevity, headings, bullets, speaker intent, metrics, and "
        "parallel structure. Keep text concise."
    ),
    "product_documentation": (
        "Preserve feature names, UI labels, setup steps, compatibility notes, warnings, "
        "limits, troubleshooting, and release-specific terminology."
    ),
    "psychology": (
        "Preserve psychological terms, study references, symptoms, frameworks, and "
        "careful distinctions between evidence and advice."
    ),
    "reference": (
        "Preserve entry structure, alphabetical or logical ordering, cross-references, "
        "labels, abbreviations, and concise explanatory style."
    ),
    "religion_spirituality": (
        "Preserve doctrinal terms, sacred names, quotations, devotional tone, ritual "
        "language, and interpretive sensitivity."
    ),
    "research_report": (
        "Preserve methodology, findings, limitations, tables, citations, statistics, "
        "and cautious claims. Use formal analytical language."
    ),
    "romance": (
        "Preserve emotional arc, intimacy level, consent cues, relationship dynamics, "
        "banter, longing, and tonal warmth."
    ),
    "sales_copy": (
        "Preserve persuasive structure, offer, objections, benefits, urgency, CTA, "
        "and audience fit without sounding mechanically translated."
    ),
    "satire": (
        "Preserve irony, target, exaggeration, deadpan tone, and rhetorical bite. "
        "Avoid explaining the joke."
    ),
    "science": (
        "Preserve scientific terminology, units, formulas, variables, data, methods, "
        "uncertainty, and causal claims."
    ),
    "science_fiction": (
        "Preserve speculative concepts, technology terms, worldbuilding, invented "
        "vocabulary, tone, and internal consistency."
    ),
    "screenplay": (
        "Preserve scene headings, action lines, character names, dialogue formatting, "
        "parentheticals, and concise visual style."
    ),
    "self_help": (
        "Use clear, direct, encouraging language. Preserve practical advice, steps, "
        "examples, and motivational tone."
    ),
    "short_story": (
        "Preserve compression, pacing, implication, voice, emotional progression, "
        "recurring symbols, context-sensitive callbacks, and ending effect. Use "
        "idiomatic literary target-language prose; rewrite literal phrasing when "
        "needed so grief, tenderness, anger, humor, ambiguity, and forgiveness land "
        "naturally without expanding concise prose."
    ),
    "social_science": (
        "Preserve concepts, classifications, populations, evidence, methodology, and "
        "careful claims about groups or societies."
    ),
    "software_documentation": (
        "Preserve code, commands, UI labels, API names, parameters, file paths, errors, "
        "steps, and version-sensitive terminology."
    ),
    "sports_fitness": (
        "Preserve exercise names, technique cues, safety cautions, measurements, "
        "training structure, and motivational tone."
    ),
    "tabletop_rpg": (
        "Preserve rules terminology, character stats, item names, lore, tone, tables, "
        "dice notation, and player-facing instructions."
    ),
    "technical": (
        "Prioritize precision, terminology consistency, units, labels, code, formulas, "
        "and procedural clarity."
    ),
    "textbook": (
        "Preserve pedagogy, definitions, examples, exercises, diagrams, captions, "
        "learning sequence, and terminology consistency."
    ),
    "thriller": (
        "Preserve urgency, tension, reveals, stakes, pacing, and procedural or action "
        "clarity. Keep sentences propulsive where the source is propulsive."
    ),
    "training_material": (
        "Use clear instructional language. Preserve objectives, procedures, examples, "
        "assessments, warnings, and learner-facing structure."
    ),
    "travel": (
        "Preserve place names, directions, cultural references, practical details, "
        "sensory description, and traveler-facing clarity."
    ),
    "true_crime": (
        "Preserve factual accuracy, chronology, legal terms, victim-sensitive tone, "
        "sources, and investigative detail."
    ),
    "user_manual": (
        "Preserve steps, UI labels, warnings, button names, part names, troubleshooting "
        "logic, and exact operational conditions."
    ),
    "western": (
        "Preserve frontier setting, dialect level, action clarity, landscape, honor "
        "codes, and period flavor without caricature."
    ),
    "white_paper": (
        "Use authoritative professional language. Preserve problem framing, evidence, "
        "technical or business claims, recommendations, and credibility markers."
    ),
    "workbook": (
        "Preserve prompts, blanks, answer spaces, exercises, numbering, instructions, "
        "and learner interaction."
    ),
    "young_adult": (
        "Preserve immediacy, emotional authenticity, character voice, pacing, and "
        "age-appropriate but not condescending language."
    ),
}


STATIC_REVIEW_BRIEF = """
Review the translation as a publication-quality target-language document.
Check faithfulness, naturalness, tone, formatting, and completeness.
Treat tokens like [[INLINE_0001]] as protected internal DOCX/EPUB structure
markers, not reader-facing translation artifacts. Do not request that they be
removed, renamed, translated, explained, or cleaned up. Review the surrounding
human-readable text for quality, and only flag placeholder tokens if they are
missing, duplicated, malformed, or visibly corrupting nearby text.
Audit ordinary source-language words that remain untranslated by mistake.
Normalize layout artifacts inside words before judging untranslated leftovers:
soft hyphens, discretionary hyphenation, nonbreaking spaces, narrow
nonbreaking spaces, and zero-width characters must not hide source-language words.
Check tables, headers, footers, captions, notes, UI labels and form labels.
Treat visible header/footer labels, document titles, repeated running heads,
watermarks, captions, and table/form labels as translatable content unless they
are clearly proper nouns, codes, URLs, file paths, or protected identifiers.
Do not mark a footer as acceptable merely because page-number placeholders are
preserved; the surrounding visible words must also be translated naturally.
For page-number footers, verify that field order and spacing remain natural,
for example the target-language equivalent of "Page X of Y" rather than
collapsed text such as "Page of XY".
For tables, audit every visible cell, including short labels, column headers,
row headers, form labels, and control labels.
In dense technical tables, distinguish code-like identifiers from
human-readable classifier words and explanatory phrases. Preserve the
identifiers, but flag readable source-language labels or phrases that remain
untranslated.
Preserve non-source-language phrases intentionally present in the source when
the author uses them as quoted, borrowed, or foreign-language text.
If the source explains a non-source-language phrase, translate the explanatory phrase naturally.
The translation should not keep the source-language explanation just because a foreign phrase is preserved.
Also flag added parenthetical target-language explanations that were not present in the source.
List each missed word or phrase separately when separate replacements are needed.
Mention concrete source text and suggested target-language replacement for each
untranslated leftover you find.
""".strip()


REVIEWER_CONTENT_FORM_CHECKLISTS = {
    "book": (
        "Review long-form continuity: chapter/section flow, character and object "
        "consistency, recurring motifs, callbacks, emotional progression, and whether "
        "the target prose reads like natural finished writing."
    ),
    "article": (
        "Review headline/subheading accuracy, quote handling, attribution, argument "
        "or news structure, concise flow, and whether the translated article keeps "
        "the same stance and emphasis."
    ),
    "academic_paper": (
        "Review abstract/title fidelity, section hierarchy, citations, equations, "
        "figure/table references, terminology, claims, limitations, and reference "
        "integrity."
    ),
    "report": (
        "Review executive-summary logic, findings, recommendations, metrics, risks, "
        "tables, captions, and whether conclusions remain supported by the source."
    ),
    "manual_or_documentation": (
        "Review step order, warnings, UI labels, commands, parameters, code, examples, "
        "troubleshooting logic, and exact operational conditions."
    ),
    "legal_or_policy": (
        "Review defined terms, parties, obligations, scope, clauses, numbering, dates, "
        "cross-references, and whether any legal or policy ambiguity changed."
    ),
}


_REVIEWER_NARRATIVE_CHECKLIST = (
    "Review emotional continuity, character voice, scene logic, implication, subtext, "
    "recurring symbols, object references, callbacks, pacing, and ending effect. "
    "Flag literal or calque phrasing that weakens emotional force, changes implied "
    "meaning, or sounds translated rather than naturally written in the target language."
)

_REVIEWER_TECHNICAL_CHECKLIST = (
    "Review terminology precision, units, formulas, code, commands, parameters, UI "
    "labels, identifiers, procedural conditions, examples, warnings, and whether "
    "human-readable labels were translated while protected identifiers stayed stable."
)

_REVIEWER_ACADEMIC_CHECKLIST = (
    "Review formal register, citation integrity, definitions, claims, evidence, "
    "methods, results, limitations, tables, figures, equations, statistics, and "
    "whether cautious academic meaning or uncertainty shifted."
)

_REVIEWER_LEGAL_CHECKLIST = (
    "Review defined terms, parties, obligations, rights, restrictions, conditions, "
    "exceptions, numbering, cross-references, dates, scope, and preserved ambiguity. "
    "Flag wording that changes legal force, duty, permission, or liability. Check "
    "that defined-term boilerplate is syntactically complete, that indemnity and "
    "limitation-of-liability terms use standard target-language legal equivalents, "
    "and that signature/execution headings follow target-language legal convention."
)

_REVIEWER_BUSINESS_CHECKLIST = (
    "Review business terminology, metrics, roles, recommendations, risks, KPIs, "
    "commercial intent, executive-summary logic, and whether the target language "
    "sounds professional rather than mechanically translated."
)

_REVIEWER_INSTRUCTIONAL_CHECKLIST = (
    "Review learning or task sequence, objectives, prompts, examples, answer cues, "
    "warnings, steps, conditions, and whether instructions remain clear, complete, "
    "and actionable."
)

_REVIEWER_JOURNALISTIC_CHECKLIST = (
    "Review attribution, quotations, dates, places, numbers, source distinctions, "
    "headline accuracy, neutrality, and whether emphasis or factual framing shifted."
)

_REVIEWER_CREATIVE_FORMAT_CHECKLIST = (
    "Review speaker labels, dialogue voice, performability, line breaks, stage or "
    "scene directions, sound effects, panel/visual context, and whether brevity or "
    "timing changed the effect."
)


REVIEWER_DOCUMENT_TYPE_CHECKLISTS = {
    "general": (
        "Review whether the translation is faithful, complete, natural, and suited "
        "to the source's register and purpose."
    ),
    "academic": _REVIEWER_ACADEMIC_CHECKLIST,
    "academic_paper": _REVIEWER_ACADEMIC_CHECKLIST,
    "business": _REVIEWER_BUSINESS_CHECKLIST,
    "business_report": _REVIEWER_BUSINESS_CHECKLIST,
    "case_study": _REVIEWER_BUSINESS_CHECKLIST,
    "contract": _REVIEWER_LEGAL_CHECKLIST,
    "legal": _REVIEWER_LEGAL_CHECKLIST,
    "policy_government": _REVIEWER_LEGAL_CHECKLIST,
    "technical": _REVIEWER_TECHNICAL_CHECKLIST,
    "software_documentation": _REVIEWER_TECHNICAL_CHECKLIST,
    "product_documentation": _REVIEWER_TECHNICAL_CHECKLIST,
    "manual": _REVIEWER_TECHNICAL_CHECKLIST,
    "user_manual": _REVIEWER_TECHNICAL_CHECKLIST,
    "science": _REVIEWER_ACADEMIC_CHECKLIST,
    "medical": _REVIEWER_ACADEMIC_CHECKLIST,
    "research_report": _REVIEWER_ACADEMIC_CHECKLIST,
    "social_science": _REVIEWER_ACADEMIC_CHECKLIST,
    "economics": _REVIEWER_ACADEMIC_CHECKLIST,
    "finance": _REVIEWER_BUSINESS_CHECKLIST,
    "white_paper": _REVIEWER_BUSINESS_CHECKLIST,
    "journalism": _REVIEWER_JOURNALISTIC_CHECKLIST,
    "essay": _REVIEWER_JOURNALISTIC_CHECKLIST,
    "educational": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "textbook": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "training_material": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "workbook": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "literary": _REVIEWER_NARRATIVE_CHECKLIST,
    "short_story": _REVIEWER_NARRATIVE_CHECKLIST,
    "commercial_fiction": _REVIEWER_NARRATIVE_CHECKLIST,
    "memoir": _REVIEWER_NARRATIVE_CHECKLIST,
    "autobiography": _REVIEWER_NARRATIVE_CHECKLIST,
    "biography": _REVIEWER_NARRATIVE_CHECKLIST,
    "children": _REVIEWER_NARRATIVE_CHECKLIST,
    "middle_grade": _REVIEWER_NARRATIVE_CHECKLIST,
    "young_adult": _REVIEWER_NARRATIVE_CHECKLIST,
    "new_adult": _REVIEWER_NARRATIVE_CHECKLIST,
    "adventure": _REVIEWER_NARRATIVE_CHECKLIST,
    "crime": _REVIEWER_NARRATIVE_CHECKLIST,
    "dystopian": _REVIEWER_NARRATIVE_CHECKLIST,
    "fairy_tale": _REVIEWER_NARRATIVE_CHECKLIST,
    "fantasy": _REVIEWER_NARRATIVE_CHECKLIST,
    "folktale": _REVIEWER_NARRATIVE_CHECKLIST,
    "historical_fiction": _REVIEWER_NARRATIVE_CHECKLIST,
    "horror": _REVIEWER_NARRATIVE_CHECKLIST,
    "humor": _REVIEWER_NARRATIVE_CHECKLIST,
    "magical_realism": _REVIEWER_NARRATIVE_CHECKLIST,
    "mystery": _REVIEWER_NARRATIVE_CHECKLIST,
    "mythology": _REVIEWER_NARRATIVE_CHECKLIST,
    "paranormal": _REVIEWER_NARRATIVE_CHECKLIST,
    "romance": _REVIEWER_NARRATIVE_CHECKLIST,
    "satire": _REVIEWER_NARRATIVE_CHECKLIST,
    "science_fiction": _REVIEWER_NARRATIVE_CHECKLIST,
    "thriller": _REVIEWER_NARRATIVE_CHECKLIST,
    "true_crime": _REVIEWER_NARRATIVE_CHECKLIST,
    "western": _REVIEWER_NARRATIVE_CHECKLIST,
    "comic": _REVIEWER_CREATIVE_FORMAT_CHECKLIST,
    "drama": _REVIEWER_CREATIVE_FORMAT_CHECKLIST,
    "graphic_novel": _REVIEWER_CREATIVE_FORMAT_CHECKLIST,
    "poetry": _REVIEWER_CREATIVE_FORMAT_CHECKLIST,
    "screenplay": _REVIEWER_CREATIVE_FORMAT_CHECKLIST,
    "game_rulebook": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "tabletop_rpg": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "cookbook": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "health_wellness": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "parenting": _REVIEWER_INSTRUCTIONAL_CHECKLIST,
    "personal_letter": (
        "Review intimacy, politeness level, relationship cues, dates, names, emotion, "
        "and whether the writer's formality and intent are preserved."
    ),
    "email_correspondence": (
        "Review sender intent, politeness level, requests, deadlines, names, action "
        "items, and whether the relationship tone is preserved."
    ),
    "marketing": (
        "Review persuasive intent, benefits, calls to action, brand terms, audience "
        "fit, emotional positioning, and whether the target copy sounds natural."
    ),
    "sales_copy": (
        "Review offer clarity, objections, benefits, urgency, calls to action, "
        "audience fit, and whether persuasion survived without sounding literal."
    ),
    "popular_science": (
        "Review scientific accuracy, explanation clarity, examples, terminology, "
        "and whether accessibility was preserved without distorting meaning."
    ),
    "self_help": (
        "Review practical advice, steps, examples, motivational tone, reader address, "
        "and whether the translation stays clear, direct, and encouraging."
    ),
}
