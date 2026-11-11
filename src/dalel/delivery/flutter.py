"""Real pinned Flutter generation, explicit transformations and API evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from dalel.resources import PACKAGE_ROOT

from dalel.formats.arb import ArbDocument
from dalel.formats.messages import differences, parse

REVISION = 'c197ca5199e9ee5661e865596c9c07294dfab51c'
VERSION = '3.47.9'
VALID_NAME = re.compile(r'[a-z][A-Za-z0-9_]*\Z')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def without_members(doc,keys):
    """Delete only specified top-level member spans and necessary commas."""
    spans = [(m.start,m.comma+1 if m.comma is not None else m.end) for m in doc.members if m.key in keys]
    retained = [m for m in doc.members if m.key not in keys]
    if retained and retained[-1].comma is not None:
        spans.append((retained[-1].comma,retained[-1].comma+1))
    text = doc.text
    for first,last in sorted(spans,reverse=True):
        text = text[:first]+text[last:]
    raw = text.encode('utf8')
    ArbDocument(raw)
    return raw


def normalized_name(key):
    if VALID_NAME.fullmatch(key):
        return key
    parts = re.findall('[A-Za-z0-9]+',key)
    name = ''.join(part if i==0 else part[0].upper()+part[1:] for i,part in enumerate(parts))
    if not name or not name[0].isalpha():
        name = 'message'+hashlib.sha256(key.encode()).hexdigest()[:12]
    name = name[0].lower()+name[1:]
    if not VALID_NAME.fullmatch(name):
        raise ValueError('Could not normalize message name')
    return name


def key_mapping(documents):
    actions,mapped = [],{}
    for key in dict.fromkeys(key for doc in documents for key in doc.strings):
        if '@' in key:
            base,attribute = key.split('@',1)
            if not base or not attribute:
                raise ValueError('Ambiguous misplaced metadata name')
            actions.append({'kind':'move','from':key,'to':'@'+normalized_name(base),
                            'attribute':attribute,'reason':'misplaced message metadata'})
            continue
        mapped[key] = normalized_name(key)
        if key!=mapped[key]:
            actions.append({'kind':'rename','from':key,'to':mapped[key],'reason':'invalid Flutter message name'})
    reverse = {}
    for old,new in mapped.items():
        if new in reverse and old!=reverse[new]:
            raise ValueError(f'Key normalization collision: {old!r} and {reverse[new]!r}')
        reverse[new] = old
    return mapped,actions


def normalize_document(doc,mapping,actions,canonical=None):
    values = {}
    moving = {a['from'] for a in actions if a['kind']=='move'}
    for key,member in doc.effective.items():
        if key in moving:
            continue
        name = '@'+mapping.get(key[1:],key[1:]) if key.startswith('@') and not key.startswith('@@') else mapping.get(key,key)
        values[name] = member.value
    for action in actions:
        if action['kind']!='move' or action['from'] not in doc.effective:
            continue
        name,attribute = action['to'],action['attribute']
        origin=canonical if canonical and action['from'] in canonical.effective else doc
        value = origin.effective[action['from']].value
        metadata = values.get(name,{})
        if not isinstance(metadata,dict) or attribute in metadata and metadata[attribute]!=value:
            raise ValueError('Conflicting misplaced metadata; manual resolution required')
        values[name] = {**metadata,attribute:value}
    return (json.dumps(values,ensure_ascii=False,indent=2)+'\n').encode('utf8')


def find_flutter():
    configured = os.environ.get('LOCQA_FLUTTER')
    if configured:
        return Path(configured).resolve() if Path(configured).is_file() else None
    executable = shutil.which('flutter')
    if executable:
        return Path(executable).resolve()
    local = Path('.locqa/flutter-partial/bin')/('flutter.bat' if os.name=='nt' else 'flutter')
    return local.resolve() if local.is_file() else None


class FlutterRunner:
    def __init__(self, executable, evidence):
        self.executable = Path(executable)
        self.evidence = Path(evidence).resolve()
        self.evidence.mkdir(parents=True,exist_ok=True)
        self.sdk = self.executable.parent.parent

    def command(self,args,cwd,log,timeout=300):
        env = dict(os.environ,CI='true',FLUTTER_SUPPRESS_ANALYTICS='true',
                   XDG_CONFIG_HOME=os.environ.get('XDG_CONFIG_HOME', str(self.evidence/'config')))
        try:
            result = subprocess.run([str(self.executable),*args],cwd=cwd,env=env,
                                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=timeout)
            code,raw = result.returncode,result.stdout
        except subprocess.TimeoutExpired as exc:
            code,raw = -1,exc.stdout or b''
        output = raw.decode('utf8',errors='replace').replace('\r\n','\n')
        Path(log).parent.mkdir(parents=True,exist_ok=True)
        Path(log).write_bytes(output.encode('utf8'))
        return code,output

    def version(self):
        code,output = self.command(['--version','--machine'],Path.cwd(),self.evidence/'version.log')
        if code:
            return {'status':'not_run','reason':'Flutter version command failed','exit_code':code}
        try:
            version = json.loads(output[output.index('{'):])
        except (ValueError,KeyError):
            return {'status':'failed','reason':'Invalid Flutter version output'}
        write_json(self.evidence/'version.json',version)
        return {'status':'passed' if version.get('frameworkVersion')==VERSION and version.get('frameworkRevision')==REVISION else 'failed',
                'version':version.get('frameworkVersion'),'revision':version.get('frameworkRevision')}

    def generate(self,name,files,template='intl_ru.arb',escaping=False):
        project = self.evidence/name
        arbs,output = project/'arb',project/'generated'
        arbs.mkdir(parents=True,exist_ok=True)
        output.mkdir(parents=True,exist_ok=True)
        # Remove only previous ARBs created by this exact case, after resolving
        # paths. An old locale must not leak into a smaller rerun.
        for path in arbs.glob('*.arb'):
            if path.resolve().parent!=arbs.resolve():
                raise ValueError('Unsafe generated ARB path')
            path.unlink()
        for filename,raw in files.items():
            if Path(filename).name!=filename:
                raise ValueError('ARB filenames must be basenames')
            (arbs/filename).write_bytes(raw)
        (project/'pubspec.yaml').write_bytes(b'name: locqa_flutter_probe\nenvironment:\n  sdk: ">=3.10.0 <4.0.0"\ndependencies:\n  flutter:\n    sdk: flutter\n  flutter_localizations:\n    sdk: flutter\n  intl: any\nflutter:\n  generate: true\n')
        config = {'arb-dir':'arb','template-arb-file':template,'output-dir':'generated',
                  'output-localization-file':'messages.dart','output-class':'Messages',
                  'use-escaping':escaping,'use-named-parameters':False,'nullable-getter':False,
                  'untranslated-messages-file':'untranslated.json'}
        # JSON scalar notation is valid YAML and avoids path/boolean quoting bugs.
        (project/'l10n.yaml').write_bytes((''.join(f'{k}: {json.dumps(v)}\n' for k,v in config.items())).encode())
        code,log = self.command(['gen-l10n'],project,project/'generation.log')
        record = {'generation':'passed' if code==0 else 'failed','exit_code':code,
                  'input_sha256':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()},
                  'log':str(project/'generation.log'),'generated_file':str(output/'messages.dart')}
        write_json(project/'result.json',record)
        return record

    def signatures(self,generated,class_name='Messages'):
        dart = self.sdk/'bin/cache/dart-sdk/bin'/('dart.exe' if os.name=='nt' else 'dart')
        packages = self.sdk/'packages/flutter_tools/.dart_tool/package_config.json'
        if not dart.exists() or not packages.exists():
            return {'status':'not_run','reason':'Pinned Dart/analyzer package configuration unavailable'}
        script = PACKAGE_ROOT/'assets/signatures.dart'
        result = subprocess.run([str(dart),'--packages='+str(packages),str(script.resolve()),str(Path(generated).resolve()),class_name],
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        log = Path(generated).with_suffix('.api.log')
        log.write_bytes(result.stderr.replace(b'\r\n',b'\n'))
        if result.returncode:
            return {'status':'failed','exit_code':result.returncode,'log':str(log)}
        rows = json.loads(result.stdout.decode('utf8'))
        write_json(Path(generated).with_suffix('.api.json'),rows)
        return {'status':'passed','members':rows}

    def runtime_smoke(self,record,latin,fixed):
        """Load the generated script locale and render a real translated getter."""
        sample=next((member['name'] for member in record['api'].get('members',[])
                     if member['kind']=='getter' and member['return_type']=='String'
                     and member['name'] in latin.strings and 0<len(latin.strings[member['name']])<120
                     and latin.strings[member['name']]!=fixed.strings.get(member['name'])
                     and not parse(latin.strings[member['name']]).names),None)
        if sample is None:
            return {'status':'not_run','reason':'No translated getter suitable for locale smoke'}
        project=Path(record['generated_file']).parent.parent
        pubspec=(project/'pubspec.yaml').read_text(encoding='utf8')
        pubspec+='dev_dependencies:\n  flutter_test:\n    sdk: flutter\n'
        font_candidates=[os.environ.get('LOCQA_TEST_FONT',''),
                         'C:/Windows/Fonts/arial.ttf','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
        font=next((Path(path) for path in font_candidates if path and Path(path).is_file()),None)
        if font:
            (project/'qa_font.ttf').write_bytes(font.read_bytes())
            pubspec=pubspec.replace('  generate: true\n','  generate: true\n  assets:\n    - qa_font.ttf\n')
        (project/'pubspec.yaml').write_bytes(pubspec.encode('utf8'))
        def dart_string(value):
            return json.dumps(value,ensure_ascii=False).replace('$',r'\$')
        expected=dart_string(latin.strings[sample])
        font_setup="final loader = FontLoader('Probe'); loader.addFont(rootBundle.load('qa_font.ttf')); await loader.load();" if font else ''
        code="""import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import '../generated/messages.dart';
void main() {
  testWidgets('Latin locale loads and paints', (tester) async {
    FONT_SETUP
    const locale = Locale.fromSubtags(languageCode: 'kk', scriptCode: 'Latn');
    final localized = await Messages.delegate.load(locale);
    expect(localized.SAMPLE, EXPECTED);
    const capture = ValueKey('capture');
    await tester.pumpWidget(MaterialApp(
      locale: locale, supportedLocales: Messages.supportedLocales,
      localizationsDelegates: Messages.localizationsDelegates,
      theme: ThemeData(fontFamily: 'Probe'),
      home: Scaffold(body: RepaintBoundary(key: capture, child: Builder(
        builder: (context) => Padding(padding: const EdgeInsets.all(24), child: Column(
          mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start,
          children: [Text(Messages.of(context).SAMPLE, style: const TextStyle(fontSize: 24)),
            const Text('Áá Ǵǵ Ńń Óó Úú Ýý Iı Ii SH CH', style: TextStyle(fontSize: 24))])))))));
    await tester.pumpAndSettle();
    expect(find.text(EXPECTED), findsOneWidget);
    expect(tester.takeException(), isNull);
    final boundary = tester.renderObject<RenderRepaintBoundary>(find.byKey(capture));
    await tester.runAsync(() async {
      final image = await boundary.toImage(pixelRatio: 2);
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await File('latin_smoke.png').writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
  });
}
""".replace('FONT_SETUP',font_setup).replace('SAMPLE',sample).replace('EXPECTED',expected)
        tests=project/'test';tests.mkdir(exist_ok=True)
        (tests/'latin_smoke_test.dart').write_bytes(code.encode('utf8'))
        dependency_code,_=self.command(['pub','get'],project,project/'pub_get.log',timeout=600)
        if dependency_code:
            return {'status':'not_run','reason':'Flutter test dependencies unavailable','exit_code':dependency_code,'log':str(project/'pub_get.log')}
        exit_code,_=self.command(['test','--no-pub','--reporter','expanded','test/latin_smoke_test.dart'],project,project/'runtime_smoke.log',timeout=600)
        result={'status':'passed' if exit_code==0 else 'failed','exit_code':exit_code,'sample_key':sample,
                'locale':'kk_Latn','log':str(project/'runtime_smoke.log'),'screenshot':str(project/'latin_smoke.png'),
                'font_source':str(font) if font else None,'application_font_coverage':'not_checked'}
        write_json(project/'runtime_smoke.json',result)
        return result


def verify_bundle(bundle,out):
    bundle,out = Path(bundle).resolve(),Path(out).resolve()
    if out in {bundle/name for name in ('intl_ru.arb','intl_kk.arb','intl_kk_fixed.arb','intl_kk_Latn.arb','uz/intl_uz_fixed.arb')}:
        raise ValueError('Validation output cannot overwrite a bundle input')
    executable = find_flutter()
    if executable is None:
        result = {'status':'not_run','reason':'Flutter SDK not found; set LOCQA_FLUTTER or PATH','checks':{}}
        write_json(out,result)
        return result
    runner = FlutterRunner(executable,out.parent)
    version = runner.version()
    if version['status']!='passed':
        result = {'status':version['status'],'sdk':version,'checks':{}}
        write_json(out,result)
        return result
    source = ArbDocument.read(bundle/'intl_ru.arb')
    fixed = ArbDocument.read(bundle/'intl_kk_fixed.arb')
    docs = [source,fixed]
    latin = bundle/'intl_kk_Latn.arb'
    if latin.exists():
        docs.append(ArbDocument.read(latin))
    uzbek=bundle/'uz/intl_uz_fixed.arb'
    if uzbek.exists():
        uz_doc=ArbDocument.read(uzbek)
        if uz_doc.locale!='uz':raise ValueError('Fixed Uzbek file must declare locale uz')
        docs.append(uz_doc)
    excluded = [key for key in source.strings if not VALID_NAME.fullmatch(key)]
    clean_source = without_members(source,set(excluded)|{'@'+key for key in excluded})
    checks = {}
    checks['A_source'] = runner.generate('A/source',{'intl_ru.arb':clean_source})
    check_a={'intl_ru.arb':clean_source,'intl_kk.arb':fixed.raw}
    if uzbek.exists():check_a['intl_uz.arb']=uzbek.read_bytes()
    checks['A'] = runner.generate('A/fixed',check_a)
    checks['A']['fixed_sha256'] = hashlib.sha256(fixed.raw).hexdigest()
    checks['A']['byte_identity'] = checks['A']['input_sha256']['intl_kk.arb']==checks['A']['fixed_sha256']
    if uzbek.exists():
        checks['A']['uz_fixed_sha256']=sha(uzbek)
        checks['A']['uz_byte_identity']=checks['A']['input_sha256']['intl_uz.arb']==sha(uzbek)
    checks['A']['tested_message_ids'] = [key for key in source.strings if key not in excluded]
    checks['A']['excluded_source_ids'] = excluded
    checks['A']['ignored_target_only_ids'] = sorted(fixed.strings.keys()-source.strings.keys())
    mapping,actions = key_mapping(docs)
    write_json(out.parent/'key_map.json',actions)
    files = {}
    for index,doc in enumerate(docs):
        files[f'intl_{doc.locale}.arb'] = normalize_document(doc,mapping,actions,source)
        folder='_'.join('latn' if d.locale=='kk_Latn' else d.locale for d in docs[:index+1])
        checks[f'B{index+1}'] = runner.generate('B/'+folder,dict(files))
        checks[f'B{index+1}']['metadata_moves']=[dict(action,original_value=doc.effective[action['from']].value,
             canonical_value=source.effective[action['from']].value if action['from'] in source.effective else doc.effective[action['from']].value,
             canonical_source_locale=source.locale) for action in actions if action['kind']=='move' and action['from'] in doc.effective]
        checks[f'B{index+1}']['tested_message_ids'] = [mapping[k] for k in source.strings if k in mapping]
        checks[f'B{index+1}']['excluded_source_ids'] = [a['from'] for a in actions if a['kind']=='move']
        checks[f'B{index+1}']['ignored_target_only_ids'] = sorted(fixed.strings.keys()-source.strings.keys())
    for record in checks.values():
        record['api'] = runner.signatures(record['generated_file']) if record['generation']=='passed' else {'status':'not_run'}
    baseline = checks['B1']['api']
    for name,record in checks.items():
        if name.startswith('B') and baseline['status']=='passed' and record['api']['status']=='passed':
            record['api_diff']=signature_diff(baseline['members'],record['api']['members'])
    api_equal = baseline['status']=='passed' and all(record['api']['status']=='passed' and record['api']['members']==baseline['members']
                                                    for name,record in checks.items() if name.startswith('B'))
    result = {'status':'passed' if all(r['generation']=='passed' for r in checks.values()) else 'failed',
              'sdk':version,'checks':checks,'latin':'included' if latin.exists() else 'not_run',
              'uzbek':'included' if uzbek.exists() else 'not_run',
              'api_signatures':'passed' if api_equal else 'failed','runtime_smoke':'not_run'}
    if not api_equal:
        result['status']='failed'
    a_source,a_target=checks['A_source']['api'],checks['A']['api']
    result['check_a_api_signatures']='passed' if a_source['status']=='passed' and a_target['status']=='passed' and a_source['members']==a_target['members'] else 'failed'
    if result['check_a_api_signatures']!='passed':
        result['status']='failed'
    original = bundle/'intl_kk.arb'
    result['protocol'] = run_protocol(runner,source.raw,original.read_bytes() if original.exists() else None)
    if any(r.get('status') in ('failed','not_run') for r in result['protocol'].values()):
        result['status']='failed'
    if latin.exists() and checks['B3']['generation']=='passed' and checks['B3']['api']['status']=='passed':
        result['runtime']=runner.runtime_smoke(checks['B3'],ArbDocument(files['intl_kk_Latn.arb']),ArbDocument(files['intl_kk.arb']))
        result['runtime_smoke']=result['runtime']['status']
        if result['runtime_smoke']!='passed':
            result['status']='failed'
    result['oracle']=run_oracle(runner)
    if result['oracle']['status']!='passed':
        result['status']='failed'
    write_json(out,result)
    return result


def arb_bytes(locale,values):
    return (json.dumps({'@@locale':locale,**values},ensure_ascii=False,indent=2)+'\n').encode('utf8')


def signature_diff(before,after):
    old={json.dumps(row,sort_keys=True):row for row in before}
    new={json.dumps(row,sort_keys=True):row for row in after}
    return {'removed':[old[key] for key in sorted(old.keys()-new.keys())],
            'added':[new[key] for key in sorted(new.keys()-old.keys())]}


def run_oracle(runner,prefix='oracle'):
    cases=json.loads((PACKAGE_ROOT/'assets/icu_flutter.json').read_text(encoding='utf8'))
    records=[]
    for case in cases:
        message=parse(case['text'],case['escaping'])
        placeholders={arg.name:{'type':'DateTime' if arg.role in ('date','time') else 'num' if arg.role=='plural' else 'String'} for arg in message.arguments}
        values={'message':case['text'],'@message':{'placeholders':placeholders}}
        record=runner.generate(str(Path(prefix)/case['id']),{'intl_en.arb':arb_bytes('en',values)},template='intl_en.arb',escaping=case['escaping'])
        sdk_valid=record['generation']=='passed'
        record.update(id=case['id'],sdk_valid=sdk_valid,product_valid=not message.errors,
                      expected_valid=case['valid'],agreement=sdk_valid==(not message.errors))
        records.append(record)
        write_json(runner.evidence/prefix/'results.json',records)
    summary={'cases':len(records),'agreement':sum(r['agreement'] for r in records),
             'sdk_expected':sum(r['sdk_valid']==r['expected_valid'] for r in records)}
    summary['status']='passed' if summary['cases']>=40 and summary['agreement']==summary['sdk_expected']==summary['cases'] else 'failed'
    write_json(runner.evidence/prefix/'summary.json',summary)
    return summary


def run_protocol(runner,original_source=None,original_target=None):
    """One injected defect per experiment; generation and API are separate."""
    source = {'greeting':'Здравствуйте, {person}', 'caption':'Маяк',
              '@greeting':{'placeholders':{'person':{'type':'String'}}}}
    target = {'greeting':'Сәлеметсіз бе, {person}', 'caption':'Шамшырақ'}
    ru,kk = arb_bytes('ru',source),arb_bytes('kk',target)
    experiments = {
        'E1':({'intl_ru.arb':ru},True),
        'E2':({'intl_ru.arb':ru,'intl_kk.arb':arb_bytes('kk',{**target,'greeting':'{person}: {place}'})},True),
        'E3':({'intl_ru.arb':ru,'intl_kk.arb':arb_bytes('kk',{**target,'greeting':'Сәлеметсіз бе'})},True),
        'E4':({'intl_ru.arb':ru,'intl_kk.arb':kk},True),
        'E5':({'intl_ru.arb':ru,'intl_kk.arb':kk,'intl_kk_Latn.arb':arb_bytes('kk_Latn',{'greeting':'Sálem, {person}','caption':'Shamshyraq'})},True),
        'E6':({'intl_ru.arb':arb_bytes('ru',{'message':'{n, plural, one{Один}}','@message':{'placeholders':{'n':{'type':'num'}}}})},False),
        'E7':({'intl_ru.arb':ru,'intl_kk.arb':arb_bytes('kk',{**target,'caption':'Шамшырақ {place}'})},True),
        'E8':({'intl_ru.arb':ru,'intl_kk_fixed.arb':kk},False),
        'E9_valid':({'intl_ru.arb':arb_bytes('ru',{'message':'{n}: {n, plural, =0{#} =1{Один} =2{Два} other{#}}','@message':{'placeholders':{'n':{'type':'num'}}}})},True),
        'E9_invalid':({'intl_ru.arb':arb_bytes('ru',{'message':'{n, plural, =3{Три} other{#}}','@message':{'placeholders':{'n':{'type':'num'}}}})},False),
    }
    result = {}
    if original_source is not None and original_target is not None:
        experiments = {'E0':({'intl_ru.arb':original_source,'intl_kk.arb':original_target},False),**experiments}
    else:
        result['E0'] = {'status':'not_run','reason':'Original source/target pair absent from bundle'}
    for name,(files,expected) in experiments.items():
        record = runner.generate('protocol/'+name,files)
        success = record['generation']=='passed'
        record['expected_generation'] = expected
        record['status'] = ('passed' if expected else 'expected_failure_confirmed') if success==expected else 'failed'
        if success:
            record['api'] = runner.signatures(record['generated_file'])
        result[name] = record
    baseline = result['E1'].get('api',{})
    for name,expect_change in [('E2',True),('E3',False),('E4',False),('E5',False),('E7',True)]:
        api = result[name].get('api',{})
        if baseline.get('status')!='passed' or api.get('status')!='passed':
            result[name]['api_check']='not_run'
            result[name]['status']='failed'
            continue
        changed = baseline['members']!=api['members']
        result[name]['api_diff']=signature_diff(baseline['members'],api['members'])
        result[name]['api_changed'] = changed
        result[name]['api_check'] = 'passed' if changed==expect_change else 'failed'
        if changed!=expect_change:
            result[name]['status']='failed'
    lost = differences(parse(source['greeting']),parse('Сәлеметсіз бе'))
    result['E3']['product_detected_loss'] = any(d['type']=='placeholder_mismatch' for d in lost)
    result['E3']['limitation'] = 'API signatures do not detect target loss; source signature remains. Product validation is required.'
    result['E5']['runtime_smoke']='not_run'
    write_json(runner.evidence/'protocol.json',result)
    return result
