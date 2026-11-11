import concurrent.futures
import json
import threading
import urllib.error

import pytest

from dalel.inference.client import BudgetError, Config, Provider, ProviderError


def config(**kw):
    return Config('https://synthetic.invalid/v1', 'synthetic', 'private-test-token', **kw)


def reply(*args):
    return {'choices': [{'message': {'content': 'ok'}}], 'usage': {
        'prompt_tokens': 100, 'completion_tokens': 40,
        'prompt_tokens_details': {'cached_tokens': 50},
        'completion_tokens_details': {'reasoning_tokens': 20},
    }}


def test_usage_cache_and_identity(tmp_path):
    provider = Provider(config(), tmp_path/'ledger.db', reply)
    first = provider.call([{'role': 'user', 'content': 'a'}])
    second = provider.call([{'role': 'user', 'content': 'a'}])
    assert not first['cache_hit'] and second['cache_hit']
    assert first['cost'] == pytest.approx((50*1.25 + 50*.15 + 40*4.25)/1e6)
    assert provider.summary()['calls'] == 1
    assert provider.summary()['reasoning_tokens'] == 20
    provider.call([{'role': 'user', 'content': 'a'}], effort='high')
    assert provider.summary()['calls'] == 2


def test_usage_can_isolate_teacher_and_student_without_releasing_unknowns(tmp_path):
    provider=Provider(config(),tmp_path/'ledger.db',reply)
    provider.call([{'role':'user','content':'teacher'}],budget_group='teacher')
    provider.call([{'role':'user','content':'student'}],budget_group='student')
    provider.call([{'role':'user','content':'student'}],budget_group='student')
    assert provider.summary('student')['calls']==1 and provider.summary('student')['cache_hits']==1
    assert provider.summary('teacher')['calls']==1 and provider.summary('teacher')['cache_hits']==0
    assert provider.summary()['calls']==2 and provider.summary()['cache_hits']==1


def test_budget_reservations_are_atomic(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        release.wait(5)
        return reply()
    provider = Provider(config(budget=.03), tmp_path/'ledger.db', slow)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        pending = pool.submit(provider.call, [{'role': 'user', 'content': 'a'}])
        assert entered.wait(5)
        with pytest.raises(BudgetError):
            provider.call([{'role': 'user', 'content': 'b'}])
        with pytest.raises(ProviderError):
            provider.call([{'role': 'user', 'content': 'a'}])
        release.set()
        pending.result()
    assert provider.summary()['reserved_usd'] == 0


def test_bound_stream_cap_cannot_be_overridden_or_borrowed(tmp_path):
    entered,release=threading.Event(),threading.Event()
    def slow(*args):
        entered.set();release.wait(5);return reply()
    first=Provider(config(budget=1),tmp_path/'ledger.db',slow,budget_group='alpha',group_budget=.03)
    same=Provider(config(budget=1),tmp_path/'ledger.db',reply,budget_group='alpha',group_budget=.03)
    other=Provider(config(budget=1),tmp_path/'ledger.db',reply,budget_group='beta',group_budget=.03)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pending=pool.submit(first.call,[{'role':'user','content':'one'}])
        assert entered.wait(5)
        try:
            with pytest.raises(BudgetError):same.call([{'role':'user','content':'two'}],budget_group='bypass',group_budget=1)
            assert not same.projection(.02)['allowed']
            assert not other.call([{'role':'user','content':'three'}])['cache_hit']
        finally:release.set()
        pending.result()
    assert first.accounting('bypass')['accounted']==0
    assert same.call([{'role':'user','content':'one'}])['cache_hit']


def test_unknown_charge_is_never_retried_or_freed(tmp_path):
    def fail(*args):
        raise RuntimeError('private-test-token')
    provider = Provider(config(), tmp_path/'ledger.db', fail)
    with pytest.raises(ProviderError) as err:
        provider.call([])
    assert 'private-test-token' not in str(err.value)
    assert provider.summary()['reserved_usd'] > 0
    with pytest.raises(ProviderError):
        provider.call([])
    assert provider.summary()['calls'] == 1


def test_missing_usage_keeps_reservation_and_redacts_echo(tmp_path):
    provider = Provider(config(), tmp_path/'ledger.db', lambda *a: {'echo': 'private-test-token'})
    result = provider.call([])
    assert 'private-test-token' not in json.dumps(result)
    assert provider.summary()['reserved_usd'] > 0
    assert provider.call([])['cache_hit']


def test_retry_retains_prior_unknown_charge_and_caches_success(tmp_path):
    count = 0
    def transient(*args):
        nonlocal count
        count += 1
        if count==1:
            raise urllib.error.URLError('network failure')
        return reply()
    provider = Provider(config(),tmp_path/'ledger.db',transient)
    with pytest.raises(ProviderError):
        provider.call([])
    reserved = provider.summary()['reserved_usd']
    with pytest.raises(ProviderError,match='deferred'):
        provider.call([],retry_failed=True)
    with provider.connect() as db:
        db.execute('UPDATE calls SET created=created-20')
    result = provider.call([],retry_failed=True)
    assert not result['cache_hit']
    assert provider.summary()['reserved_usd']==reserved
    assert provider.summary()['calls']==2
    assert provider.call([])['cache_hit']
    assert count==2


def test_retry_cannot_escape_group_or_global_budget(tmp_path):
    provider = Provider(config(budget=.03),tmp_path/'ledger.db',lambda *a: (_ for _ in ()).throw(urllib.error.URLError('temporary')))
    with pytest.raises(ProviderError):
        provider.call([],budget_group='teacher',group_budget=.03)
    with provider.connect() as db:
        db.execute('UPDATE calls SET created=created-20')
    with pytest.raises(BudgetError):
        provider.call([],retry_failed=True,budget_group='teacher',group_budget=.03)
    assert provider.summary()['calls']==1


def test_provable_failure_before_request_has_no_model_charge(tmp_path,monkeypatch):
    import socket
    from dalel.inference.client import http_post,NoRequestSent
    def no_dns(*args,**kwargs):
        raise socket.gaierror('synthetic DNS failure')
    monkeypatch.setattr(socket,'getaddrinfo',no_dns)
    with pytest.raises(NoRequestSent):
        http_post('https://synthetic.invalid/v1/chat/completions','private-test-token',b'{}')
    provider=Provider(config(),tmp_path/'ledger.db',http_post,connectivity_probe=lambda *args:None)
    with pytest.raises(ProviderError,match='NoRequestSent'):
        provider.call([])
    assert provider.summary()['reserved_usd']==0
    assert provider.summary()['cost_usd']==0
    with provider.connect() as db:
        assert db.execute('SELECT status FROM calls').fetchone()[0]=='failed_not_sent'


def test_connectivity_gate_prevents_paid_admissions_and_cache_is_offline(tmp_path):
    available=True
    def probe(*args):
        if not available:
            raise OSError('synthetic outage')
    provider=Provider(config(),tmp_path/'ledger.db',reply,connectivity_probe=probe)
    provider.call([{'role':'user','content':'first'}])
    available=False
    provider._probe_success=0
    assert provider.call([{'role':'user','content':'first'}])['cache_hit']
    with pytest.raises(ProviderError,match='paid admissions paused'):
        provider.call([{'role':'user','content':'second'}])
    assert provider.summary()['calls']==1
    assert provider.summary()['reserved_usd']==0


def test_timeout_tracks_effort_output_cap_and_measured_tail():
    from dalel.inference.client import adaptive_timeout
    assert adaptive_timeout('xhigh',24000,[100,250,430])>=890
    assert adaptive_timeout('xhigh',48000,[])>adaptive_timeout('xhigh',16000,[])
    assert adaptive_timeout('low',1000,[])<adaptive_timeout('xhigh',1000,[])


def test_newly_booted_host_still_requires_free_gate(tmp_path,monkeypatch):
    # WSL can have <15 seconds of uptime; zero is not a successful probe.
    monkeypatch.setattr('dalel.inference.client.time.monotonic',lambda:1.0)
    def unavailable(*args):
        raise OSError('synthetic outage immediately after boot')
    provider=Provider(config(),tmp_path/'ledger.db',reply,connectivity_probe=unavailable)
    with pytest.raises(ProviderError,match='paid admissions paused'):
        provider.call([{'role':'user','content':'new host'}])
    assert provider.summary()['calls']==0


def test_raised_global_budget_respects_lower_config_and_retains_unknowns(tmp_path,monkeypatch):
    monkeypatch.setattr('dalel.inference.client.load_env',lambda:None)
    for specified,expected in [('95',95),('140',140),('145',140),('999',140)]:
        monkeypatch.setenv('LOCQA_BUDGET_USD',specified)
        assert Config.from_env().budget==expected
    path=tmp_path/'ledger.db';provider=Provider(config(budget=140),path,reply)
    with provider.connect() as db:
        db.execute("INSERT INTO calls(id,status,reserved,cost) VALUES('unknown','failed_unknown_charge',100,0)")
    assert provider.reconcile_path is None
    provider.call([{'role':'user','content':'above previous ceiling'}])
    assert provider.accounting()['reserved']==100
    lower=Provider(config(budget=95),path,reply)
    with pytest.raises(BudgetError):lower.call([{'role':'user','content':'lower configuration wins'}])
    with provider.connect() as db:
        db.execute("UPDATE calls SET reserved=140 WHERE id='unknown'")
    with pytest.raises(BudgetError):provider.call([{'role':'user','content':'global ceiling'}])
