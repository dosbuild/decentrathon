"""Self-contained Russian HTML. Untrusted localization is always escaped text."""
from __future__ import annotations
from dalel.resources import PACKAGE_ROOT
import difflib
import html
import json
import re
from collections import Counter
from jinja2 import Environment

TYPE_NAMES={
 'untranslated':'Нет перевода','same_as_source':'Совпадает с исходником',
 'placeholder_mismatch':'Аргументы','plural_mismatch':'ICU plural',
 'glossary_violation':'Глоссарий','length_overflow':'Длина',
 'machine_translation_suspect':'Смысл и грамматика','inconsistent_term':'Терминология',
 'suffix_harmony':'Аффикс у аргумента','orthography':'Орфография','wrong_language':'Другой язык'}
SEVERITY_NAMES={'high':'Высокая','medium':'Средняя','low':'Низкая'}

def marked(text,quotes):
    if text is None:
        return '<span class="muted">Значение отсутствует</span>'
    spans=[]
    for quote in quotes:
        if not quote:
            continue
        start=0
        while (position:=text.find(quote,start))>=0:
            spans.append((position,position+len(quote)));start=position+len(quote)
    merged=[]
    for first,last in sorted(spans):
        if merged and first<=merged[-1][1]:
            merged[-1]=(merged[-1][0],max(last,merged[-1][1]))
        else:
            merged.append((first,last))
    result=[];offset=0
    for first,last in merged:
        result.extend((html.escape(text[offset:first]),'<mark>',html.escape(text[first:last]),'</mark>'));offset=last
    result.append(html.escape(text[offset:]));return ''.join(result)

def difference(before,after):
    if after is None:
        return '<span class="muted">Безопасное предложение отсутствует</span>'
    before=before or '';result=[]
    for op,a,b,c,d in difflib.SequenceMatcher(None,before,after,autojunk=False).get_opcodes():
        if op=='equal':
            result.append(html.escape(after[c:d]))
        else:
            if op in ('delete','replace'):
                result.extend(('<del>',html.escape(before[a:b]),'</del>'))
            if op in ('insert','replace'):
                result.extend(('<ins>',html.escape(after[c:d]),'</ins>'))
    return ''.join(result)

def render_report(records,coverage=None,diagnostics=None):
    cards=[]
    for record in records:
        quotes=re.findall('«([^«»]+)»',record['evidence'])
        cards.append(dict(record,source_html=marked(record.get('source'),quotes),
            target_html=marked(record.get('target'),quotes),diff_html=difference(record.get('target'),record.get('suggestion'))))
    counts=Counter(r['issue_type'] for r in records);severities=Counter(r['severity'] for r in records)
    coverage=coverage or {};methods=[]
    complete=coverage.get('language_complete',False)
    producer='внешняя языковая проверка' if coverage.get('producer')=='external' else 'языковая модель'
    if coverage.get('mode')=='replay':producer+=' (сохранённые результаты)'
    for kind,label in TYPE_NAMES.items():
        method='не запускался'
        if kind in coverage.get('formal',[]):method='Детерминированные правила'
        elif kind in coverage.get('partial',[]):method='Детерминированные правила (частичное покрытие)'
        if complete:method=method+' + '+producer if method!='не запускался' else producer.capitalize()
        elif kind in coverage.get('retained_imported_types', []):
            saved='Сохранённые находки для этой версии'
            method=method+' + '+saved if method!='не запускался' else saved
        methods.append({'label':label,'count':counts[kind],'method':method})
    env=Environment(autoescape=True)
    return env.from_string(TEMPLATE).render(cards=cards,counts=counts,severities=severities,
       types=TYPE_NAMES,levels=SEVERITY_NAMES,total=len(records),coverage=coverage,methods=methods,
       diagnostic_count=len(diagnostics or []),diagnostics=json.dumps(diagnostics or [],ensure_ascii=False,indent=2))+'\n'

TEMPLATE = (PACKAGE_ROOT / 'assets/views/report.html').read_text(encoding='utf-8')
