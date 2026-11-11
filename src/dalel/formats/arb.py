"""Lossless top-level JSON members; last occurrence wins without erasing history."""
from __future__ import annotations

import difflib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Member:
    key: str
    value: Any
    start: int
    value_start: int
    end: int
    comma: int | None


def _reject_constant(value):
    raise ValueError(f'Non-JSON constant: {value}')


class _ObjectPairs(list):
    """Distinguish JSON objects from arrays until duplicates are recorded."""


def string_literal(value: str, original: str | None = None) -> str:
    """Reuse exact raw units on equal spans, and the original style on new text."""
    if original is None:
        return json.dumps(value, ensure_ascii=False)
    old = json.loads(original)
    if old == value:
        return original
    units = []
    pos = 1
    while pos < len(original)-1:
        end = pos+1
        if original[pos] == '\\':
            end = pos + (6 if original[pos+1] == 'u' else 2)
            if original[pos+1] == 'u' and 0xD800 <= int(original[pos+2:pos+6],16) <= 0xDBFF:
                if original[end:end+2] == '\\u' and 0xDC00 <= int(original[end+2:end+6],16) <= 0xDFFF:
                    end += 6
        units.append(original[pos:end])
        pos = end
    style = {char:raw for char,raw in zip(old, units)}
    ascii_style = original.isascii() and not old.isascii()
    out = ['"']
    for operation, a, b, c, d in difflib.SequenceMatcher(a=old,b=value,autojunk=False).get_opcodes():
        if operation == 'equal':
            out.extend(units[a:b])
        elif operation in ('replace','insert'):
            out.extend(style.get(char, json.dumps(char,ensure_ascii=ascii_style)[1:-1]) for char in value[c:d])
    out.append('"')
    return ''.join(out)


class ArbDocument:
    def __init__(self, raw: bytes, *, path: Path | None = None):
        self.raw = raw
        self.text = raw.decode('utf-8')
        self.path = path
        self.members: list[Member] = []
        self.effective: dict[str, Member] = {}
        self.nested_duplicates: list[dict] = []
        self._parse()

    @classmethod
    def read(cls, path: Path | str) -> 'ArbDocument':
        path = Path(path)
        return cls(path.read_bytes(), path=path)

    def _parse(self):
        text = self.text
        decoder = json.JSONDecoder(parse_constant=_reject_constant,object_pairs_hook=_ObjectPairs)
        def ws(pos):
            while pos < len(text) and text[pos] in ' \t\r\n':
                pos += 1
            return pos
        def expect(pos, char):
            if pos >= len(text) or text[pos] != char:
                raise ValueError(f'Expected {char!r} at character {pos}')
        pos = ws(1 if text.startswith('\ufeff') else 0)
        expect(pos,'{')
        self.open = pos
        pos = ws(pos+1)
        while pos < len(text) and text[pos] != '}':
            start = pos
            key, pos = decoder.raw_decode(text,pos)
            if not isinstance(key,str):
                raise ValueError('ARB member names must be strings')
            pos = ws(pos)
            expect(pos,':')
            value_start = ws(pos+1)
            value, end = decoder.raw_decode(text,value_start)
            value = self._nested_value(value,[key])
            pos = ws(end)
            comma = pos if pos < len(text) and text[pos] == ',' else None
            member = Member(key,value,start,value_start,end,comma)
            self.members.append(member)
            self.effective[key] = member
            if comma is None:
                break
            pos = ws(pos+1)
            if pos < len(text) and text[pos] == '}':
                raise ValueError('Trailing comma in ARB object')
        expect(pos,'}')
        self.close = pos
        if ws(pos+1) != len(text):
            raise ValueError('Trailing data after ARB object')

    def _nested_value(self,value,path):
        if isinstance(value,_ObjectPairs):
            grouped=defaultdict(list);result={}
            for key,child in value:
                child=self._nested_value(child,[*path,key])
                grouped[key].append(child);result[key]=child
            for key,values in grouped.items():
                if len(values)>1:
                    self.nested_duplicates.append({'path':[*path,key],'field':key,
                        'occurrences':len(values),'conflict':any(v!=values[-1] for v in values[:-1])})
            return result
        if isinstance(value,list):
            return [self._nested_value(child,[*path,i]) for i,child in enumerate(value)]
        return value

    @property
    def strings(self) -> dict[str,str]:
        return {key:m.value for key,m in self.effective.items()
                if not key.startswith('@') and isinstance(m.value,str)}

    @property
    def locale(self) -> str:
        member = self.effective.get('@@locale')
        if member and isinstance(member.value,str):
            return member.value
        if self.path:
            parts = self.path.stem.split('_',1)
            if len(parts) == 2:
                return parts[1]
        return 'und'

    def render(self, edits: Mapping[str,Any] | None = None) -> bytes:
        edits = edits or {}
        replacements = []
        for key,value in edits.items():
            member = self.effective[key]
            if member.value == value:
                continue
            literal = string_literal(value,self.text[member.value_start:member.end]) if isinstance(value,str) and isinstance(member.value,str) else json.dumps(value,ensure_ascii=False)
            replacements.append((member.value_start,member.end,literal))
        chunks=[]
        cursor=0
        for a,b,replacement in sorted(replacements):
            chunks.extend((self.text[cursor:a],replacement))
            cursor=b
        chunks.append(self.text[cursor:])
        return ''.join(chunks).encode('utf-8')

    def insert(self, key: str, value: Any, *, after: str | None = None) -> bytes:
        if key in self.effective:
            raise ValueError(f'Key already exists: {key}')
        newline = '\r\n' if '\r\n' in self.text else '\n'
        anchor = self.effective[after] if after is not None else None
        reference = anchor or (self.members[0] if self.members else None)
        indent = '  '
        if reference:
            line_start = self.text.rfind('\n',0,reference.start)+1
            prefix = self.text[line_start:reference.start]
            if prefix.strip() == '':
                indent = prefix
        literal = json.dumps(key,ensure_ascii=False) + ': ' + json.dumps(value,ensure_ascii=False)
        text = self.text
        if anchor:
            if anchor.comma is not None:
                pos = anchor.comma+1
                text = text[:pos] + newline+indent+literal+',' + text[pos:]
            else:
                pos = anchor.end
                text = text[:pos] + ','+newline+indent+literal + text[pos:]
        elif self.members:
            pos = self.members[0].start
            text = text[:pos]+literal+','+newline+indent+text[pos:]
        else:
            pos = self.open+1
            text = text[:pos]+newline+indent+literal+newline+text[pos:]
        return text.encode('utf-8')

    def insert_in_source_order(self, additions: Mapping[str,Any], source_order: Sequence[str]) -> bytes:
        doc = self
        remaining = dict(additions)
        predecessor = None
        for key in source_order:
            if key in remaining:
                doc = ArbDocument(doc.insert(key,remaining.pop(key),after=predecessor),path=self.path)
            if key in doc.effective:
                predecessor = key
        if remaining:
            raise ValueError('Insertion keys absent from source order')
        return doc.raw

    def insert_after_many(self, additions: Mapping[str,tuple[str,Any]]) -> bytes:
        """Insert distinct new members after distinct existing anchors in one pass."""
        inserts=[];anchors=set()
        newline='\r\n' if '\r\n' in self.text else '\n'
        for key,(after,value) in additions.items():
            if key in self.effective:raise ValueError('Insertion member already exists')
            if after in anchors:raise ValueError('Insertion anchors must be distinct')
            anchors.add(after);anchor=self.effective[after]
            line_start=self.text.rfind('\n',0,anchor.start)+1
            prefix=self.text[line_start:anchor.start];indent=prefix if not prefix.strip() else '  '
            literal=json.dumps(key,ensure_ascii=False)+': '+json.dumps(value,ensure_ascii=False)
            if anchor.comma is not None:
                inserts.append((anchor.comma+1,newline+indent+literal+','))
            else:inserts.append((anchor.end,','+newline+indent+literal))
        chunks=[];cursor=0
        for position,text in sorted(inserts):
            chunks.extend((self.text[cursor:position],text));cursor=position
        chunks.append(self.text[cursor:])
        return ''.join(chunks).encode('utf8')

    def snapshot(self) -> dict:
        grouped = defaultdict(list)
        for m in self.members:
            if not m.key.startswith('@') and isinstance(m.value,str):
                grouped[m.key].append(m.value)
        return {
            'unique':len(self.strings),
            'duplicates':sum(len(v)>1 for v in grouped.values()),
            'conflicts':sum(len(set(v))>1 for v in grouped.values()),
            'empty':sum(v=='' for v in self.strings.values()),
            'comments':sum(m.key=='@@comment' for m in self.members),
            'invalid_names':sum(re.fullmatch(r'[a-z][A-Za-z0-9_]*', k) is None for k in self.strings),
        }
