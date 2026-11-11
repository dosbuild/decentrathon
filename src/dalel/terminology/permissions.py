"""Authority to change a term is separate from evidence of inconsistent usage."""


def authorizes_replacement(finding, source_locale, target_locale, glossary):
    from dalel.terminology.glossary import approved, source_match, Matcher, ui_conventions
    entries = list(glossary)
    if (source_locale, target_locale) == ('ru', 'kk'):
        entries.extend({'ru': source, 'kk': target} for source, target in ui_conventions().items())
    matcher = Matcher()
    for entry in entries:
        original, preferred = entry.get(source_locale), entry.get(target_locale)
        rule = entry.get('_rule', {})
        if not original or not preferred or not approved(entry, source_locale, target_locale):
            continue
        if not source_match(finding.get('source') or '', original, rule):
            continue
        if any(matcher.contains(finding.get('target') or '', variant, target_locale)
               for variant in [preferred, *rule.get('allowed_variants', [])]):
            continue
        if matcher.contains(finding.get('suggestion') or '', preferred, target_locale):
            return True
    return False
