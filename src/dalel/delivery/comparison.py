"""Compatible deterministic baselines and conservative multiset comparisons."""
from __future__ import annotations
import difflib
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from dalel.formats.messages import parse,differences,protected
from dalel.contracts import Finding, ReviewedFinding

LEVEL={'low':1,'medium':2,'high':3}

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def detector_files(root):
    root = Path(root)
    paths = {p for p in root.rglob('*.py') if '__pycache__' not in p.parts}
    for pattern in ('assets/*.dart', 'assets/*.json', 'assets/*.yml',
                    'assets/kazakh/*', 'assets/uzbek/*', 'assets/latin/*.json'):
        paths.update(root.glob(pattern))
    return sorted((p for p in paths if p.is_file()), key=lambda p: p.relative_to(root).as_posix())


def detector_digest(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    return digest([(p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
                   for p in detector_files(root)])


def locator(record,escaping=False):
    kind=record['issue_type'];source=record.get('source') or '';target=record.get('target') or ''
    if kind in ('placeholder_mismatch','plural_mismatch'):
        delta=differences(parse(source,escaping),parse(target,escaping))
        structural=[{k:v for k,v in row.items() if k not in ('offset',)} for row in delta]
        positional={k:protected(target)['positional'][k]-protected(source)['positional'][k]
                    for k in protected(source)['positional'].keys()|protected(target)['positional'].keys()
                    if protected(target)['positional'][k]!=protected(source)['positional'][k]}
        return {'structure':structural,'positional_delta':positional}
    if kind=='suffix_harmony':
        return {'runtime_affixes':re.findall(r'\{([A-Za-z_]\w*)\}[-–]?([а-яәғқңөұүһі]+)',target,re.I)}
    if kind=='orthography' and isinstance(record.get('suggestion'),str):
        old=re.findall(r'\w+',target);new=re.findall(r'\w+',record['suggestion'])
        changed=[old[a:b] for op,a,b,c,d in difflib.SequenceMatcher(a=old,b=new,autojunk=False).get_opcodes()
                 if op!='equal' and a!=b]
        if changed:
            return {'tokens':changed}
    if kind=='glossary_violation':
        return {'term':source.casefold()}
    # These detectors emit one whole-value semantic/translation decision per
    # key/type. Multiplicity is still retained if a caller supplies several.
    return {'scope':'whole_value'}

def identity(record,locale,escaping=False):
    where=json.loads(json.dumps(locator(record,escaping),ensure_ascii=False))
    rule='formal.'+record['issue_type']+'.v1'
    return {'fingerprint':digest([locale,record['key'],record['issue_type'],rule,where]),
            'rule_id':rule,'locator':where}

def run_metadata(records,source,target,references=(),glossary=None,escaping=False):
    from dalel.terminology.glossary import rules_digest
    compatibility={'format':1,'mode':'deterministic','detector_sha256':detector_digest(),
        'source_locale':source.locale,'target_locale':target.locale,'escaping':escaping,
        'reference_locales':sorted(r.locale for r in references),
        'glossary_sha256':hashlib.sha256(glossary.read_bytes()).hexdigest() if glossary else None,
        'glossary_rules_sha256':rules_digest(glossary)}
    inputs=[{'role':role,'locale':doc.locale,'sha256':hashlib.sha256(doc.raw).hexdigest()}
            for role,doc in [('source',source),('target',target)]+[('reference',r) for r in references]]
    return {'compatibility':compatibility,'inputs':inputs,'report_sha256':digest(records),
            'identities':[identity(r,target.locale,escaping) for r in records],
            'rule_change_impact':'not_assessed'}

def load_baseline(path,current_metadata):
    records=json.loads(path.read_text(encoding='utf8'))
    for record in records:
        ReviewedFinding.model_validate(record)
    metadata=json.loads(path.with_suffix('.run.json').read_text(encoding='utf8'))
    if metadata.get('compatibility')!=current_metadata['compatibility']:
        raise ValueError('Baseline detector/config/glossary/locale metadata is incompatible')
    if metadata.get('report_sha256')!=digest(records):
        raise ValueError('Baseline report hash mismatch')
    config=metadata['compatibility']
    if metadata.get('identities')!=[identity(r,config['target_locale'],config['escaping']) for r in records]:
        raise ValueError('Baseline problem identities are invalid')
    return records,metadata

def compare(before,after,before_metadata,after_metadata,fail_on=None):
    if before_metadata['compatibility']!=after_metadata['compatibility']:
        raise ValueError('Incompatible baseline metadata')
    if fail_on is not None and fail_on not in LEVEL:
        raise ValueError('Unknown failure severity')
    left=defaultdict(list);right=defaultdict(list)
    for records,metadata,groups in ((before,before_metadata,left),(after,after_metadata,right)):
        if metadata.get('report_sha256')!=digest(records) or len(metadata.get('identities',[]))!=len(records):
            raise ValueError('Incomplete or stale baseline identity coverage')
        for index,(record,ident) in enumerate(zip(records,metadata['identities'])):
            groups[ident['fingerprint']].append((index,record))
    changes=[]
    def emit(fp,old,new):
        prior=old[1] if old else None;current=new[1] if new else None
        changed=bool(prior and current and (prior['source'],prior['target'])!=(current['source'],current['target']))
        if prior is None:
            status='new'
        elif current is None:
            status='resolved'
        elif LEVEL[current['severity']]>LEVEL[prior['severity']]:
            status='escalated'
        elif changed and current['severity']=='high':
            status='changed_high_requires_review'
        else:
            status='existing'
        changes.append({'fingerprint':fp,'status':status,'key':(current or prior)['key'],
            'issue_type':(current or prior)['issue_type'],'before_index':old[0] if old else None,
            'after_index':new[0] if new else None,'before_severity':prior['severity'] if prior else None,
            'severity':current['severity'] if current else prior['severity'],'input_changed':changed})
    for fp in sorted(left.keys()|right.keys()):
        old=list(left[fp]);new=list(right[fp])
        # Match unchanged occurrences before pairing changed ones; a set-diff
        # would lose newly introduced copies of the same defect.
        for exact_severity in (True,False):
            for item in list(new):
                found=next((candidate for candidate in old
                    if (candidate[1]['source'],candidate[1]['target'])==(item[1]['source'],item[1]['target'])
                    and (not exact_severity or candidate[1]['severity']==item[1]['severity'])),None)
                if found is not None:
                    old.remove(found);new.remove(item);emit(fp,found,item)
        while old and new:
            emit(fp,old.pop(0),new.pop(0))
        for item in old:
            emit(fp,item,None)
        for item in new:
            emit(fp,None,item)
    counts={status:sum(r['status']==status for r in changes) for status in
            ('existing','new','resolved','escalated','changed_high_requires_review')}
    blocked=bool(fail_on and any(r['status'] in ('new','escalated','changed_high_requires_review')
                                 and LEVEL[r['severity']]>=LEVEL[fail_on] for r in changes))
    return {'summary':counts,'blocking':blocked,'fail_on':fail_on,'changes':changes,
            'rule_change_impact':'not_assessed','report_filtered':False}
