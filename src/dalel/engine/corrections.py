"""Minimal AI correction export. This never creates human approval."""
from __future__ import annotations
import hashlib
from dalel.formats.arb import ArbDocument
from dalel.formats.messages import validate_suggestion
from dalel.engine.hygiene import LINE_REFLOW

def apply_ai_changes(source,target,findings,*,provenance,verified_fixes=(),escaping=False,annotate_metadata=True,
                     glossary=(),accepted_keys=()):
    changes={};evidence={}
    source_values=source.strings;target_values=target.strings
    for row in findings:
        key=row['key'];text=row.get('suggestion')
        if row['source']!=source_values.get(key) or row['target']!=target_values.get(key):
            raise ValueError('Correction finding belongs to a different input snapshot')
        if text is None or text.startswith('requires_code_change') or text==target_values.get(key):
            continue
        from dalel.terminology.permissions import authorizes_replacement
        if row.get('issue_type') == 'inconsistent_term' and key not in accepted_keys and not authorizes_replacement(row, source.locale, target.locale, glossary):
            continue
        if key not in source_values or '@' in key:
            raise ValueError('Correction must refer to a source message')
        if key in changes and changes[key]!=text:
            raise ValueError('Conflicting corrections for one key')
        evidence.setdefault(key,[]).append(row['evidence']);changes[key]=text
    for key,text in changes.items():
        errors=validate_suggestion(source_values[key],text,original_target=target_values.get(key),escaping=escaping,
            allow_line_reflow=bool(LINE_REFLOW.search(' '.join(evidence[key]))))
        if errors:
            raise ValueError('Unsafe AI correction: '+','.join(errors))
    existing={key:text for key,text in changes.items() if key in target_values}
    additions={key:text for key,text in changes.items() if key not in target_values}
    fixed=ArbDocument(target.render(existing))
    fixed=ArbDocument(fixed.insert_in_source_order(additions,list(source_values)))
    source_hash=hashlib.sha256(source.raw).hexdigest();target_hash=hashlib.sha256(target.raw).hexdigest()
    changed=[];metadata_edits={};metadata_additions={}
    for key in source_values:
        if key not in changes:
            continue
        name='@'+key;prior=fixed.effective.get(name)
        if prior and not isinstance(prior.value,dict):
            raise ValueError('Message metadata must be an object')
        source_metadata=source.effective.get(name)
        metadata=dict(prior.value) if prior else dict(source_metadata.value) if not annotate_metadata and source_metadata and isinstance(source_metadata.value,dict) else {}
        extra=metadata.get('x-locqa',{})
        if not isinstance(extra,dict):
            raise ValueError('Provenance metadata must be an object')
        if annotate_metadata:
            metadata.update(reviewed_by_human=False,**{'x-locqa':{**extra,'review_stage':'ai_only'}})
        if prior and metadata!=prior.value:metadata_edits[name]=metadata
        elif not prior and metadata:metadata_additions[name]=(key,metadata)
        changed.append({'key':key,'before':target_values.get(key),'after':changes[key],
                        'reviewed_by_human':False,'review_stage':'ai_only',
                        'provenance':{**provenance,'source_sha256':source_hash,'target_sha256':target_hash},
                        'verification':next((r for r in verified_fixes if r['key']==key),None),
                        'finding_types':[row['issue_type'] for row in findings if row['key']==key and 'issue_type' in row],
                        'verification_verdict':'independent_ai_verification',
                        'human_verdict':{'status':'unreviewed','name':None,'at':None}})
    fixed=ArbDocument(fixed.render(metadata_edits))
    return fixed.insert_after_many(metadata_additions),changed
