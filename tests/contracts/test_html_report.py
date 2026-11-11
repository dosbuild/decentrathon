import json
from html.parser import HTMLParser
from dalel.presentation.report import render_report,marked,difference
from dalel.cli import app
from typer.testing import CliRunner

def row():
    return dict(key='sampleKey',issue_type='orthography',severity='medium',source='Синтетический пример',
        target='Қате <script>alert(1)</script>',evidence='Опечатка в «Қате», требуется исправление по правилу.',
        suggestion='Дұрыс <script>alert(1)</script>',confidence=.88,reviewed_by_human=False)

def test_html_escapes_every_untrusted_surface_and_has_no_external_resources():
    record=row();record['key']='</code><img src=x onerror=alert(1)>'
    rendered=render_report([record],{'mode':'deterministic'},[{'payload':'</pre><script>evil()</script>'}])
    class Tags(HTMLParser):
        def __init__(self):super().__init__();self.tags=[]
        def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))
    tags=Tags();tags.feed(rendered)
    assert len([tag for tag,attrs in tags.tags if tag=='script'])==1
    assert not any(tag in ('img','iframe','link') for tag,attrs in tags.tags)
    assert not any('src' in attrs or 'href' in attrs or any(a.startswith('on') for a in attrs) for tag,attrs in tags.tags)
    assert '<mark>Қате</mark>' in rendered and '&lt;script&gt;' in rendered
    assert 'Дефект не рассмотрен человеком' in rendered
    assert '<del>' in rendered and '<ins>' in rendered

def test_highlight_overlap_and_absent_values():
    assert marked('abcdef',['abc','bcde'])=='<mark>abcde</mark>f'
    assert 'Значение отсутствует' in marked(None,[])
    assert '&lt;' in difference('a','<b>')
    rendered=render_report([],{ 'target_locale':'uz-Latn','mode':'deterministic'})
    assert 'Автоматический анализ без проверки носителем узбекского' in rendered
    assert 'Языковые кандидаты не откалиброваны' in rendered

def test_cli_renders_utf8_lf_and_refuses_input_overwrite(tmp_path):
    report=tmp_path/'report.json';report.write_bytes(json.dumps([row()],ensure_ascii=False).encode())
    original=report.read_bytes();out=tmp_path/'report.html';runner=CliRunner()
    result=runner.invoke(app,['render-html','--report',str(report),'--out',str(out)])
    assert result.exit_code==0,result.output
    assert b'\r\n' not in out.read_bytes() and 'Қате' in out.read_text(encoding='utf8')
    assert runner.invoke(app,['render-html','--report',str(report),'--out',str(report)]).exit_code==2
    assert report.read_bytes()==original
