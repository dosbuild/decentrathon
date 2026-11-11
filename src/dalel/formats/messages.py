"""ICU messages as argument roles at selector paths, plus protected game tokens."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

IDENT = re.compile(r'[A-Za-z_][A-Za-z_0-9]*')
POSITIONAL = re.compile(r'%(?!%)(?:\d+\$)?[-+#0]*\d*(?:\.\d+)?[sdf@]')
TAG = re.compile(r'</?[A-Za-z][^<>]*>')
PUA = re.compile('[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]')
# Flutter 3.47.9 gen_l10n_types.dart validDateFormats; skeleton spellings are
# constructor names in intl, not arbitrary ICU pattern strings.
DATE_SKELETONS = frozenset('''d E EEEE LLL LLLL M Md MEd MMM MMMd MMMEd MMMM
MMMMd MMMMEEEEd QQQ QQQQ y yM yMd yMEd yMMM yMMMd yMMMEd yMMMM yMMMMd
yMMMMEEEEd yQQQ yQQQQ H Hm Hms j jm jms jmv jmz jv jz m ms s'''.split())


@dataclass(frozen=True)
class Argument:
    name: str
    role: str
    path: tuple
    start: int
    end: int
    selectors: frozenset[str] = frozenset()


@dataclass
class Message:
    text: str
    arguments: list[Argument] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    text_spans: list[tuple[int,int]] = field(default_factory=list)

    @property
    def names(self) -> set[str]:
        return {a.name for a in self.arguments}

    @property
    def roles(self) -> set[tuple]:
        return {(a.path,a.name,a.role) for a in self.arguments}


class Parser:
    def __init__(self,text: str,escaping: bool = False):
        self.text = text
        self.escaping = escaping
        self.pos = 0
        self.result = Message(text)

    def error(self,reason,kind='syntax',start=None):
        self.result.errors.append({'kind':kind,'reason':reason,'offset':self.pos if start is None else start})

    def whitespace(self):
        while self.pos<len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def parse(self) -> Message:
        self.sequence((),False)
        return self.result

    def sequence(self,path,nested):
        text_start=self.pos
        def flush():
            if text_start<self.pos:
                self.result.text_spans.append((text_start,self.pos))
        while self.pos<len(self.text):
            c = self.text[self.pos]
            if c == "'" and self.escaping:
                self.pos += 1
                if self.pos<len(self.text) and self.text[self.pos]=="'":
                    self.pos += 1
                    continue
                # Flutter use-escaping quotes a run, including literal braces.
                while self.pos<len(self.text):
                    if self.text[self.pos]=="'":
                        self.pos += 1
                        if self.pos<len(self.text) and self.text[self.pos]=="'":
                            self.pos += 1
                            continue
                        break
                    self.pos += 1
                else:
                    self.error('Unclosed apostrophe quotation')
            elif c == '{':
                flush()
                self.argument(path)
                text_start=self.pos
            elif c == '}':
                flush()
                if nested:
                    self.pos += 1
                    return True
                # The pinned Flutter parser stops its root message here and
                # does not reject remaining tokens. Preserve this quirk as a
                # warning instead of claiming gen-l10n will fail.
                self.result.warnings.append({'kind':'syntax','reason':'Flutter ignores unmatched closing brace and following text','offset':self.pos})
                self.pos = len(self.text)
                return False
            else:
                self.pos += 1
        flush()
        if nested:
            self.error('Unclosed branch')
        return False

    def argument(self,path):
        start = self.pos
        self.pos += 1
        self.whitespace()
        match = IDENT.match(self.text,self.pos)
        if not match:
            self.error('Expected argument identifier',start=start)
            return
        name = match.group()
        self.pos = match.end()
        self.whitespace()
        if self.pos<len(self.text) and self.text[self.pos]=='}':
            self.pos += 1
            self.result.arguments.append(Argument(name,'value',path,start,self.pos))
            return
        if self.pos>=len(self.text) or self.text[self.pos]!=',':
            self.result.arguments.append(Argument(name,'value',path,start,self.pos))
            self.error('Expected comma or closing brace',start=start)
            return
        self.pos += 1
        self.whitespace()
        match = IDENT.match(self.text,self.pos)
        if not match:
            self.error('Missing argument type',start=start)
            return
        role = match.group()
        self.pos = match.end()
        self.whitespace()
        if role not in ('plural','select'):
            # Number and date/time styles contain no nested message braces.
            end = self.text.find('}',self.pos)
            if end<0:
                self.error('Unclosed format argument',start=start)
                end = len(self.text)-1
            if role not in ('date','time'):
                self.error('Unsupported argument type',start=start)
            else:
                style = self.text[self.pos:end]
                skeleton = re.fullmatch(r'\s*,\s*:\s*:\s*([A-Za-z0-9_]+)\s*',style)
                if not skeleton or skeleton[1] not in DATE_SKELETONS:
                    self.error('Invalid or missing date/time skeleton',start=start)
            self.pos = end+1
            self.result.arguments.append(Argument(name,role,path,start,self.pos))
            return
        kind = 'plural' if role in ('plural','selectordinal') else 'select'
        if self.pos>=len(self.text) or self.text[self.pos]!=',':
            self.error('Missing control comma',kind,start)
        else:
            self.pos += 1
        selectors = set()
        closed = False
        while self.pos<len(self.text):
            self.whitespace()
            if self.pos>=len(self.text):
                break
            if self.text[self.pos]=='}':
                self.pos += 1
                closed = True
                break
            selector_start = self.pos
            while self.pos<len(self.text) and not self.text[self.pos].isspace() and self.text[self.pos] not in '{}':
                self.pos += 1
            selector = self.text[selector_start:self.pos]
            if selector=='=':
                self.whitespace()
                number=re.match(r'\d+',self.text[self.pos:])
                if number:
                    selector+=number.group()
                    self.pos+=len(number.group())
            if not selector:
                self.error('Missing selector',kind,start)
                self.pos += 1
                continue
            if kind=='plural' and selector not in {'zero','one','two','few','many','other','=0','=1','=2'}:
                self.error('Invalid plural selector '+selector,kind,selector_start)
            if kind=='select' and not re.fullmatch(r'[A-Za-z0-9_|]+',selector):
                self.error('Invalid select selector '+selector,kind,selector_start)
            if selector in selectors:
                self.result.warnings.append({'kind':kind,'reason':'Duplicate selector '+selector,'offset':selector_start})
            selectors.add(selector)
            self.whitespace()
            if self.pos>=len(self.text) or self.text[self.pos]!='{':
                self.error('Missing selector body',kind,start)
                break
            self.pos += 1
            if not self.sequence(path+((name,selector),),True):
                self.error('Unclosed selector body',kind,start)
                break
        if 'other' not in selectors:
            self.error('Missing other selector',kind,start)
        if not closed:
            self.error('Unclosed control argument',kind,start)
        self.result.arguments.append(Argument(name,role,path,start,self.pos,frozenset(selectors)))


def parse(text: str,escaping: bool = False) -> Message:
    return Parser(text,escaping).parse()


def protected(text: str) -> dict:
    # Escaped %% is literal; %s and numbered positions are runtime arguments.
    return {'positional':Counter(POSITIONAL.findall(text.replace('%%',''))),
            'tags':Counter(TAG.findall(text)), 'pua':Counter(PUA.findall(text))}


def differences(source: Message,target: Message) -> list[dict]:
    result = []
    source_controls = {(a.path,a.name):a for a in source.arguments if a.role in ('plural','select','selectordinal')}
    target_controls = {(a.path,a.name):a for a in target.arguments if a.role in ('plural','select','selectordinal')}
    changed_plural_controls = set()
    for k,a in source_controls.items():
        if k not in target_controls or target_controls[k].role!=a.role:
            result.append({'type':'plural_mismatch' if a.role!='select' else 'placeholder_mismatch',
                           'reason':'control argument lost or changed','name':a.name})
            if a.role!='select':
                changed_plural_controls.add(a.name)
    for error in target.errors:
        result.append({'type':'plural_mismatch' if error['kind']=='plural' else 'diagnostic',**error})
    missing,extra = source.names-target.names,target.names-source.names
    lost_plural = {a.name for a in source_controls.values() if a.role!='select' and a.name in missing}
    missing -= lost_plural
    if missing or extra:
        result.append({'type':'placeholder_mismatch','reason':'argument names differ','missing':sorted(missing),'extra':sorted(extra)})
    # Compare shared selector paths. Plural categories legitimately differ by
    # locale; repeated occurrences within a path never change the signature.
    def declared_paths(message):
        paths = {()}
        for argument in message.arguments:
            paths.add(argument.path)
            paths.update(argument.path+((argument.name,selector),) for selector in argument.selectors)
        return paths
    common_paths = declared_paths(source) & declared_paths(target)
    src_roles = {r for r in source.roles if r[0] in common_paths and r[1] not in changed_plural_controls}
    tgt_roles = {r for r in target.roles if r[0] in common_paths and r[1] not in changed_plural_controls}
    if not missing and not extra and src_roles != tgt_roles:
        result.append({'type':'placeholder_mismatch','reason':'argument role or selector path differs'})
    return result


def target_layout_problems(original: str|None,suggestion: str,*,allow_line_reflow=False,source=None) -> list[str]:
    if original is None:
        return []
    problems=[]
    for edge,pattern in (('left',r'^\s*'),('right',r'\s*$')):
        if re.search(pattern,original).group()!=re.search(pattern,suggestion).group():
            problems.append('original target '+edge+' edge whitespace differs')
    layout=source if isinstance(source,str) else original
    if not allow_line_reflow and layout.count('\n')!=suggestion.count('\n'):
        problems.append(('source' if isinstance(source,str) else 'original target')+' newline count differs')
    return problems


def validate_suggestion(source: str,suggestion: str,*,escaping=False,original_target=None,allow_line_reflow=False) -> list[str]:
    a,b = parse(source,escaping),parse(suggestion,escaping)
    problems = [d['reason'] for d in differences(a,b)]
    if b.errors:
        problems += [e['reason'] for e in b.errors]
    for kind,required in protected(source).items():
        if protected(suggestion)[kind] != required:
            problems.append('protected '+kind+' differ')
    if original_target is not None:
        problems.extend(target_layout_problems(original_target,suggestion,allow_line_reflow=allow_line_reflow,source=source))
    else:
        for edge in ('left','right'):
            pattern = r'^\s*' if edge=='left' else r'\s*$'
            if re.search(pattern,source).group() != re.search(pattern,suggestion).group():
                problems.append(edge+' edge whitespace differs')
    return list(dict.fromkeys(problems))
