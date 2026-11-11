"""Independent synthetic seeding: surface forms never occur in root support."""
import json
from collections import Counter

from dalel.formats.arb import ArbDocument
from dalel.engine.rules import check, orthography_edits, is_russian_untranslated


# Hand-written complete forms cover plural+possessive+case chains, long roots,
# compound position, front/back harmony, lower/title/upper case and punctuation.
ROOTS = 'бүркіт саңырауқұлақ көбелек құмырсқа жұлдыз жел қобыз түйе мүйіз тас бақа аққу көл бақ өрік мұз'.split()
FORMS = '''бүркіттерімізден саңырауқұлақтарыңызға көбелектердің құмырсқаларымыз
жұлдызшылар қобызшылардың түйелерімізге мүйіздеріңізді өріктеріміздің
тасбақалардың аққулар көлбақа мұзтастар'''.split()
MUTATIONS = {'ә':'а','ғ':'г','қ':'к','ң':'н','ө':'о','ұ':'у','ү':'у','і':'и','һ':'х'}


def seeds():
    for word in FORMS:
        for position,char in enumerate(word):
            if char not in MUTATIONS:
                continue
            # н/ң in a possessive/case suffix can change grammatical person
            # while remaining a valid word; spelling alone cannot decide it.
            if char=='ң' and all(position>=len(root) or not word.startswith(root) for root in ROOTS):
                continue
            bad = word[:position]+MUTATIONS[char]+word[position+1:]
            yield word,bad,position


def evaluate(transform=lambda value:value):
    positive = list(seeds())
    # The corpus contains roots repeatedly, but no correct inflected surfaces.
    # Wrong and clean examples are tested in separate files to avoid leakage.
    roots = {f'anchor{i}':(' '+word)*4 for i,word in enumerate(ROOTS)}
    target = {**roots,**{f'case{i}':transform(bad) for i,(_,bad,_) in enumerate(positive)}}
    source = {key:'Описание объекта' for key in target}
    def doc(locale,values):
        return ArbDocument(json.dumps({'@@locale':locale,**values},ensure_ascii=False).encode())
    findings,_ = check(doc('ru',source),doc('kk',target))
    detected = {f.key for f in findings if f.issue_type=='orthography'}
    expected = {f'case{i}' for i in range(len(positive))}
    negatives = FORMS + '''бүркіттерін саңырауқұлақтарын көбелегін құмырсқасын
жұлдызында қобызын түйелерін мүйіздерін өріктерін тасбақасын аққуын
киім киіміңіз биік биіктігі сыйлық сыйлықтарымыз қиын қиындық
суық суықта қуаныш қуанышты уақыт уақытында халық халықтың
такси таксиге кино кинода гитара гитаралар радио радиода
университет университеттің хирург хирургтар институт институтқа
кокос кокостар хоккей хоккейге технология технологиялар
монитор монитордың гараж гараждар бонус бонустар сонша мұнша ондай'''.split()
    clean = {**roots,**{f'clean{i}':transform(word) for i,word in enumerate(negatives)}}
    clean_findings,_ = check(doc('ru',{k:'Описание объекта' for k in clean}),doc('kk',clean))
    false = [f.model_dump() for f in clean_findings]
    return {'positives':len(expected),'tp':len(detected&expected),'fp':len(detected-expected)+len(false),
            'negatives':len(negatives),'missed':[positive[int(k[4:])] for k in sorted(expected-detected)],
            'false':false}


def test_seeded_morphological_spelling():
    for transform in (lambda w:w,lambda w:'«'+w.upper()+'»',lambda w:'Белгі:\n'+w.capitalize()+'.'):
        result = evaluate(transform)
        assert result['tp']==result['positives'],result
        assert result['fp']==0,result


def test_unattested_native_front_case_ending():
    for bad,good in [('сәулени','сәулені'),('мәдениеттің','мәдениеттің'),('өрнектин','өрнектін')]:
        # Last pair is deliberately not a valid case suffix and must stay put.
        expected = good if bad=='сәулени' else bad
        assert orthography_edits(bad,Counter({bad:1}))[0]==expected


def test_multiline_russian_with_localized_names():
    cases = ['Құйын\nВыберите другой маршрут.',
             'Қыран\r\nПожалуйста, продолжите движение.\r\nVega',
             'Мүлік\nДля получения награды\nоткройте окно.',
             'Шаңырақ\rНажмите на изображение.',
             'Көлік\n\nВаши достижения сохранены.',
             'Попробуйте\nснова', 'Прыгать\nвверх']
    assert all(is_russian_untranslated(text) for text in cases)
    values = {f'line{i}':value for i,value in enumerate(cases)}
    source = ArbDocument(json.dumps({'@@locale':'ru',**values},ensure_ascii=False).encode())
    target = ArbDocument(json.dumps({'@@locale':'kk',**values},ensure_ascii=False).encode())
    findings,_ = check(source,target)
    assert [(f.issue_type,f.severity) for f in findings]==[('same_as_source','high')]*len(cases)
    assert not is_russian_untranslated('Гараж\nБампер\nМеталл\nКиім')
    assert not is_russian_untranslated('Суық\nҚиын\nМүлік')


def test_residual_suffixes_short_stems_and_whole_multiline():
    frequency=Counter({'бар':1,'кел':4,'компьютер':1,'лифт':1,'құз':7,'қыз':7,'құрақ':4})
    for bad,good in [('барган','барған'),('компьютери','компьютері'),('лифти','лифті'),('Кұз','Құз'),('Кыз','Қыз'),('Кұрақ','Құрақ')]:
        assert orthography_edits(bad,frequency)[0]==good
    for word in ('орган','гитара','такси','кино','бар','кел','тор','компьютер'):
        assert not orthography_edits(word,frequency)[1]
    # Whole-string syntax joins fragments too short to trigger linewise rules.
    assert is_russian_untranslated('В\nгараж')
    assert is_russian_untranslated('Получить доступ\nОтправить сообщение')
    assert not is_russian_untranslated('Радио кино\nГараж металл')


if __name__=='__main__':
    for name,transform in [('plain',lambda w:w),('uppercase_quotes',lambda w:'«'+w.upper()+'»'),
                           ('multiline_titlecase',lambda w:'Белгі:\n'+w.capitalize()+'.')]:
        print(json.dumps({'layout':name,**evaluate(transform)},ensure_ascii=False))
