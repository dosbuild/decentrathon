"""Versioned table-driven Cyrillic to Latin conversion of ICU text nodes."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from dalel.resources import PACKAGE_ROOT

from dalel.formats.arb import ArbDocument
from dalel.formats.messages import parse, TAG, POSITIONAL, PUA, differences, protected

DEFAULT_PROFILE='kk-latn-2018-decree637'
WORD=re.compile(r'[^\W\d_]+',re.UNICODE)
TOKENS=re.compile(r'https?://[^\s<>]+|[\w.+-]+@[\w.-]+|'+TAG.pattern+'|'+POSITIONAL.pattern+'|'+PUA.pattern)


def load_profile(name=DEFAULT_PROFILE):
    if Path(name).name!=name:
        raise ValueError('Profile must be a registered basename')
    path=PACKAGE_ROOT/'assets/latin'/f'{name}.json'
    profile=json.loads(path.read_text(encoding='utf8'))
    if profile.get('id')!=name:
        raise ValueError('Profile identity mismatch')
    return profile


def transliterate(text,profile=None,*,escaping=False,preserve=()):
    profile=profile or load_profile()
    parsed=parse(text,escaping)
    if parsed.errors:
        return text,[{'reason':'invalid_icu','details':parsed.errors}]
    unresolved=[]
    def visible(value,offset):
        value=unicodedata.normalize('NFC',value)
        def word(match):
            all_caps=match.group().isupper()
            result=[]
            for index,char in enumerate(match.group()):
                pair=profile['mapping'].get(char.lower())
                if pair:
                    variant='all_caps' if all_caps else 'upper' if char.isupper() else 'lower'
                    result.append(pair[variant])
                else:
                    result.append(char)
                    if '\u0400'<=char<='\u052f':
                        unresolved.append({'reason':'unmapped_cyrillic','character':char,'offset':offset+match.start()+index})
            return ''.join(result)
        return unicodedata.normalize('NFC',WORD.sub(word,value))
    replacements=[]
    for start,end in parsed.text_spans:
        value=text[start:end]
        spans=[m.span() for m in TOKENS.finditer(value)]
        for literal in preserve:
            if literal:
                spans.extend(m.span() for m in re.finditer(re.escape(literal),value))
        spans.sort()
        cursor=0;parts=[]
        for first,last in spans:
            if last<=cursor:
                continue
            if first>cursor:
                parts.append(visible(value[cursor:first],start+cursor))
            first=max(cursor,first)
            parts.append(value[first:last]);cursor=last
        parts.append(visible(value[cursor:],start+cursor))
        replacements.append((start,end,''.join(parts)))
    result=text
    for start,end,value in sorted(replacements,reverse=True):
        result=result[:start]+value+result[end:]
    if differences(parsed,parse(result,escaping)) or protected(text)!=protected(result):
        raise ValueError('Latin conversion changed runtime structure or protected tokens')
    if not unicodedata.is_normalized('NFC',result):
        unresolved.append({'reason':'protected_content_not_nfc'})
    if parsed.warnings:
        unresolved.append({'reason':'icu_warning','details':parsed.warnings})
    return result,unresolved


def convert_document(doc,profile=None,*,escaping=False,preserve=()):
    profile=profile or load_profile()
    source_sha256=hashlib.sha256(doc.raw).hexdigest()
    strings=doc.strings
    edits={'@@locale':'kk_Latn'} if '@@locale' in doc.effective else {}
    rows=[];metadata={}
    for key,value in strings.items():
        # A known misplaced description remains structural metadata.
        if '@' in key and key.split('@',1)[0] in strings:
            continue
        latin,unresolved=transliterate(value,profile,escaping=escaping,preserve=preserve)
        if latin!=value:
            edits[key]=latin
            original=doc.effective.get('@'+key)
            prior=original.value if original and isinstance(original.value,dict) else {}
            if original and not isinstance(original.value,dict):
                raise ValueError('Non-object message metadata cannot carry review provenance')
            extra=prior.get('x-locqa',{})
            if not isinstance(extra,dict):
                raise ValueError('Non-object provenance metadata')
            metadata['@'+key]={**prior,'reviewed_by_human':False,'x-locqa':{
                **extra,'latin_profile':profile['id'],'latin_profile_version':profile['version'],
                'cyrillic_source_sha256':source_sha256,
                'source_review_flag':prior.get('reviewed_by_human',False)}}
            # The Cyrillic receipt is evidence about its own exact version.
            # It must not remain an active approval inside a converted message.
            if isinstance(prior.get('locqaReview'), dict):
                metadata['@'+key]['sourceReview'] = prior['locqaReview']
                metadata['@'+key]['locqaReview'] = {
                    'reviewed_by_human': False, 'reviewer': None,
                    'reviewed_at': None, 'revision_id': None,
                    'native_speaker_self_declared': False,
                }
        rows.append({'key':key,'cyrillic_length':len(value),'latin_length':len(latin),
                     'max_line_length':max(map(len,latin.splitlines()),default=0),
                     'growth_ratio':len(latin)/len(value) if value else 1,
                     'length_risk':bool(value) and len(latin)>1.3*len(value),
                     'non_ascii':sorted({f'U+{ord(c):04X}' for c in latin if ord(c)>127}),
                     'unresolved':unresolved})
    edits.update({key:value for key,value in metadata.items() if key in doc.effective})
    result=ArbDocument(doc.render(edits))
    insertions=[]
    for key,value in metadata.items():
        if key not in result.effective:
            anchor=result.effective[key[1:]]
            start=result.text.rfind('\n',0,anchor.start)+1
            indent=result.text[start:anchor.start]
            if indent.strip():
                indent='  '
            newline='\r\n' if '\r\n' in result.text else '\n'
            literal=json.dumps(key,ensure_ascii=False)+': '+json.dumps(value,ensure_ascii=False)
            if anchor.comma is None:
                insertions.append((anchor.end,','+newline+indent+literal))
            else:
                insertions.append((anchor.comma+1,newline+indent+literal+','))
    chunks=[];cursor=0
    for position,literal in sorted(insertions):
        chunks.extend((result.text[cursor:position],literal));cursor=position
    chunks.append(result.text[cursor:])
    result=ArbDocument(''.join(chunks).encode('utf8'))
    if '@@locale' not in result.effective:
        result=ArbDocument(result.insert('@@locale','kk_Latn'))
    profile_key='@@x-locqa-latin-profile'
    result=ArbDocument(result.render({profile_key:profile['id']}) if profile_key in result.effective
                       else result.insert(profile_key,profile['id'],after='@@locale'))
    report={'profile':profile['id'],'profile_version':profile['version'],
            'source_sha256':source_sha256,'output_sha256':hashlib.sha256(result.raw).hexdigest(),
            'strings':len(rows),'changed_strings':sum(key in strings for key in edits),
            'unresolved_strings':sum(bool(row['unresolved']) for row in rows),
            'glyph_coverage':'not_checked','human_review':'not_reviewed','rows':rows}
    return result.raw,report
