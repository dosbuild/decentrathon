"""One primary finding for the same evidenced defect, after all detectors run."""
import re
from dalel.contracts import Finding,finding_order

PRIORITY={name:i for i,name in enumerate(('untranslated','placeholder_mismatch','plural_mismatch',
    'wrong_language','same_as_source','suffix_harmony','glossary_violation','inconsistent_term',
    'orthography','machine_translation_suspect','length_overflow'))}
LEXICAL={'glossary_violation','inconsistent_term','orthography','machine_translation_suspect'}


def same_defect(a,b):
    if (a['key'],a.get('source'),a.get('target'))!=(b['key'],b.get('source'),b.get('target')):return False
    kinds={a['issue_type'],b['issue_type']}
    if len(kinds)==1:return True
    if kinds=={'length_overflow','machine_translation_suspect'}:
        meaning = a if a['issue_type'] == 'machine_translation_suspect' else b
        length = b if meaning is a else a
        same_fix = (isinstance(meaning.get('suggestion'), str) and
                    meaning['suggestion'].casefold() == (length.get('suggestion') or '').casefold())
        return same_fix and bool(re.search(r'призыв.{0,30}нажать|инструкци|нажм|press|click', meaning.get('evidence', ''), re.I))
    if kinds=={'same_as_source','wrong_language'}:return True
    x,y=a.get('suggestion'),b.get('suggestion')
    if kinds<=LEXICAL and isinstance(x,str) and isinstance(y,str) and not x.startswith('requires_code_change') and x.casefold()==y.casefold():
        return True
    if 'glossary_violation' in kinds and kinds<=LEXICAL:
        glossary=a if a['issue_type']=='glossary_violation' else b
        other=b if glossary is a else a
        quotes=set(re.findall('«([^«»]+)»',other.get('evidence','')))
        # A whole-label glossary mismatch and a meaning claim quoting that
        # exact label describe one replacement, including a withheld fix.
        return bool(other.get('source') and other.get('target') and
            other['source'] in quotes and other['target'] in quotes and
            glossary['source'] in set(re.findall('«([^«»]+)»',glossary.get('evidence',''))))
    return False


def deduplicate(records,*,curated=()):
    rows=[dict(r) for r in records];groups=[];diagnostics=[]
    for row in rows:
        matches=[g for g in groups if any(same_defect(row,prior) for prior in g)]
        if not matches:groups.append([row]);continue
        merged=[row]
        for group in matches:merged.extend(group);groups.remove(group)
        groups.append(merged)
    curated={(r['key'],r['issue_type']):r for r in curated};result=[]
    for group in groups:
        eligible=group
        # A copied source is the untranslated-source class. A second detector
        # calling that same text foreign does not establish another language.
        if any(r['issue_type']=='same_as_source' and r.get('source')==r.get('target') for r in group):
            eligible=[r for r in eligible if r['issue_type']!='wrong_language']
        # Glossary alignment is supporting evidence for an independently
        # established wrong meaning. The glossary class concerns terminology,
        # not an opposite/unrelated action that also happens to miss a term.
        from dalel.engine.hygiene import SEMANTIC_ERROR,MEANING_LOSS
        meaning=any(r['issue_type']=='machine_translation_suspect'
            and (SEMANTIC_ERROR.search(r['evidence']) or MEANING_LOSS.search(r['evidence'])
                 or re.search(r'(?:смысл|значени).{0,100}(?:искаж|подмен|утрачен)|не соответствует источнику.{0,100}означает',r['evidence'],re.I)) for r in group)
        if meaning:eligible=[r for r in eligible if r['issue_type'] not in ('glossary_violation','inconsistent_term')]
        lengths = [r for r in eligible if r['issue_type'] == 'length_overflow']
        meanings = [r for r in eligible if r['issue_type'] == 'machine_translation_suspect']
        if lengths and meanings:
            concise = {r['suggestion'].casefold() for r in lengths if isinstance(r.get('suggestion'), str)}
            if all(isinstance(r.get('suggestion'), str) and r['suggestion'].casefold() in concise for r in meanings):
                eligible = lengths
            else:
                eligible = [r for r in eligible if r['issue_type'] != 'length_overflow']
        primary=min(eligible,key=lambda r:(PRIORITY[r['issue_type']],-r['confidence']))
        chosen=dict(primary)
        assessed=[curated[(r['key'],r['issue_type'])] for r in group if (r['key'],r['issue_type']) in curated]
        texts={r.get('suggestion') for r in assessed}
        if len(texts)==1:chosen['suggestion']=next(iter(texts))
        elif len(texts)>1:chosen['suggestion']=None
        if chosen['suggestion']!=primary.get('suggestion'):
            for quote in re.findall('«([^«»]+)»',chosen['evidence']):
                if not any(quote in (chosen.get(k) or '') for k in ('source','target','suggestion')):
                    chosen['evidence']=chosen['evidence'].replace('«'+quote+'»',quote)
        result.append(Finding.model_validate(chosen).model_dump())
        if len(group)>1:
            diagnostics.append({'type':'merged_same_defect','key':chosen['key'],
                'primary_issue_type':chosen['issue_type'],'findings':group,'publication':'one_primary_finding'})
    return sorted(result,key=lambda r:finding_order(Finding.model_validate(r))),diagnostics
