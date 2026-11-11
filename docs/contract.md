# Product contract

This document summarizes the Qazaq Loc QA brief. Organizer requirements and project decisions are separated below.

## Organizer requirements

Build a reusable ARB checker and apply it to the supplied localization. Deliver corrected Kazakh text with native-speaker review.

### Deliverables

The required command is:

```sh
locqa check --source intl_ru.arb --target intl_kk.arb --glossary glossary.csv --out report.json
```

It must produce a JSON report and a readable HTML report. The submission also includes:

- `glossary.csv`: Russian, Kazakh and English terms, with usage notes.
- `intl_kk_fixed.arb`: corrected Kazakh strings, each changed string carrying a human-review flag.
- Methodology: detection and correction methods, processing cost, and the number of strings reviewed by a native speaker.
- Documentation and tests showing that the tool handles ARBs beyond the supplied files.

### Issue codes

| Code | Meaning |
| --- | --- |
| `untranslated` | A target string is missing. |
| `same_as_source` | Text matches the source; brands, names and numbers may be valid exceptions. |
| `placeholder_mismatch` | Source and target placeholders differ. |
| `plural_mismatch` | ICU plural forms are invalid or incomplete. |
| `glossary_violation` | A translation conflicts with the glossary. |
| `length_overflow` | A translation is too long relative to its source in a short UI element. |
| `machine_translation_suspect` | Wording suggests a calque, unnatural order or Russian grammar in Kazakh. |
| `inconsistent_term` | A term has inconsistent translations across keys. |
| `suffix_harmony` | A fixed suffix after a runtime placeholder may not fit the inserted word. |
| `orthography` | A spelling error or substitution of a Kazakh letter is suspected. |
| `wrong_language` | Text uses a language other than the target language. |

The tool must handle arbitrary keys and ICU plurals, even when the supplied files contain no plurals. Glossary usage must account for game terminology and established slang. Context may come from descriptions, key names and neighboring strings.

For runtime names, replacing one fixed suffix with another is insufficient. Rephrase the text or identify the need for runtime handling.

### Finding fields

Preserve these nine names:

| Field | Content |
| --- | --- |
| `key` | ARB message key. |
| `issue_type` | One of the eleven codes above. |
| `severity` | `high`, `medium` or `low`. |
| `source` | Source text. |
| `target` | Target text. |
| `evidence` | The reason for the finding. |
| `suggestion` | Proposed correction. |
| `confidence` | Confidence in the finding. |
| `reviewed_by_human` | Human-review status. |

The implementation writes an array of findings. Field types and validation are defined in `src/dalel/contracts.py`. Human-review status belongs to the exact text version being assessed.

### Acceptance and evaluation

Placeholders and ICU syntax must remain valid. The corrected ARB must pass standard `flutter gen-l10n`.

Every changed string must state whether a person reviewed it. Unreviewed changes are excluded from translation-quality assessment. Bulk retranslation without review does not satisfy the task.

| Criterion | Weight | Evaluation |
| --- | ---: | --- |
| Detection | 35% | Typed recall on 100 seeded errors; precision from 100 randomly sampled findings reviewed manually. |
| Correction quality | 35% | Native-speaking judges blindly compare 150 randomly sampled changed strings. |
| Generality | 15% | Arbitrary ARBs, documentation and tests. |
| Explainability | 15% | Clear evidence and severity, with few false alarms. |

The organizer measures these scores using seeded errors, sampled findings and native-speaker assessment.

### Additional scope

The brief also lists Uzbek analysis, official-alphabet Kazakh transliteration with length checks, an ARB pull-request GitHub Action, and suffix rewording suggestions. The implementation records the selected transliteration profile explicitly.

### Data restrictions

Use supplied data within the organizer's purpose and confidentiality terms. Keep inputs and derived outputs private, and follow the organizer's retention deadline.

## Project decisions

The command-line contract is the entry point. Detection and correction take priority over additional interfaces and bonus features because they account for 70% of the evaluation.

Keep findings, proposed changes and human decisions separate. Bind review to the exact wording and relevant context. A later edit must not inherit approval for an earlier version.

Preserve message content, placeholders, ICU structure and ARB metadata through export. Any required identifier adaptation must be explicit and reversible.

Keep rule-based checks available without model credentials. Model processing requires an explicit run allowance. Report measured usage separately from estimates; credentials alone do not authorize spending.

Use software contract checks for reproducibility. Keep datasets and working reports outside the published tree. Verify the exact exported files with Flutter.
