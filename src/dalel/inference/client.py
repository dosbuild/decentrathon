"""OpenAI-compatible HTTP transport, response cache and per-attempt cost ledger.

Ambiguous failures retain their reservation. Authorized retries reserve separately;
a successful response suppresses any further paid attempt for the same input.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import sqlite3
import time
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from dalel.inference import accounting as billing


def load_env(path: Path = Path('.env')) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


@dataclass(frozen=True)
class Config:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    effort: str = 'low'
    budget: float = 95.0
    input_price: float = 1.25
    cached_price: float = .15
    output_price: float = 4.25

    @classmethod
    def from_env(cls) -> 'Config':
        load_env()
        return cls(
            base_url=os.environ.get('LOCQA_BASE_URL', 'https://api.openai.com/v1').rstrip('/'),
            model=os.environ.get('LOCQA_MODEL', 'gpt-6.1-sol'),
            api_key=os.environ.get('OPENAI_API_KEY') or os.environ.get('LOCQA_API_KEY', ''),
            effort=os.environ.get('LOCQA_REASONING_EFFORT', 'low'),
            budget=min(140., float(os.environ.get('LOCQA_BUDGET_USD', '0'))),
            input_price=float(os.environ.get('LOCQA_PRICE_INPUT_PER_M', '2')),
            cached_price=float(os.environ.get('LOCQA_PRICE_CACHED_INPUT_PER_M', '.10')),
            output_price=float(os.environ.get('LOCQA_PRICE_OUTPUT_PER_M', '10')),
        )


class BudgetError(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


class QuotaError(ProviderError):
    """The provider rejected account quota; retrying cannot complete this run."""


class NoRequestSent(Exception):
    """Connection/TLS failed before any model HTTP request bytes were sent."""


class _PreflightConnection:
    def connect(self):
        try:
            return super().connect()
        except (OSError,http.client.HTTPException) as exc:
            raise NoRequestSent(type(exc).__name__) from None


class _HTTPSConnection(_PreflightConnection,http.client.HTTPSConnection):
    pass


class _HTTPConnection(_PreflightConnection,http.client.HTTPConnection):
    pass


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self,request):
        return self.do_open(_HTTPSConnection,request,context=self._context)


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self,request):
        return self.do_open(_HTTPConnection,request)


def http_post(url: str, key: str, body: bytes,timeout=240) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
    })
    opener = urllib.request.build_opener(_HTTPSHandler(),_HTTPHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            try:
                error = json.loads(exc.read(65536)).get('error', {})
                code = error.get('code') if isinstance(error, dict) else None
            except (ValueError, TypeError, OSError):
                code = None
            if code in {'insufficient_quota', 'billing_hard_limit_reached', 'quota_exceeded'}:
                raise QuotaError('Provider account quota unavailable; no automatic retry') from None
        raise


def free_models_probe(base_url,key):
    """Authenticated metadata GET, never a model completion request."""
    request=urllib.request.Request(base_url+'/models',headers={'Authorization':f'Bearer {key}'})
    opener=urllib.request.build_opener(_HTTPSHandler(),_HTTPHandler())
    with opener.open(request,timeout=20) as response:
        if response.status!=200:
            raise ProviderError('Models connectivity probe did not return HTTP 200')
        response.read(1024)


def adaptive_timeout(effort,max_tokens,latencies):
    positive=sorted(value for value in latencies if value>0)
    p95=positive[min(len(positive)-1,math.ceil(len(positive)*.95)-1)] if positive else 0
    rates={'low':120,'medium':100,'high':85,'xhigh':70}
    minimum={'low':120,'medium':180,'high':300,'xhigh':600}
    return math.ceil(max(minimum.get(effort,300),max_tokens/rates.get(effort,70)+90,p95*2+30))


class Provider:
    def __init__(self, config: Config, path: Path = Path('data/private/locqa/usage.sqlite3'), transport=http_post,
                 reconcile_path: Path | None=None,connectivity_probe=None,
                 budget_group: str | None=None,group_budget: float | None=None):
        self._attempt_lock = threading.Lock()
        self._transport_attempts = 0
        self.config = config
        self.path = path
        self.transport = transport
        if (budget_group is None)!=(group_budget is None):raise ValueError('A stream needs both its name and cap')
        if group_budget is not None and (not budget_group or not math.isfinite(group_budget) or group_budget<=0):
            raise ValueError('Invalid stream budget')
        self.budget_group=budget_group;self.group_budget=group_budget
        # Reconciliation is opt-in with an explicit billing statement. A
        # product installation must not depend on repository review files.
        self.reconcile_path = reconcile_path
        self.connectivity_probe = connectivity_probe or (free_models_probe if transport is http_post else None)
        self._probe_lock=threading.Lock()
        self._probe_success=0.
        prices = (config.budget, config.input_price, config.cached_price, config.output_price)
        if any(not math.isfinite(value) or value<0 for value in prices):
            raise ValueError('Prices and budget must be nonnegative')
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY, model TEXT, effort TEXT, created REAL,
                    status TEXT, reserved REAL, cost REAL DEFAULT 0,
                    latency REAL DEFAULT 0, input_tokens INTEGER DEFAULT 0,
                    cached_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0, response TEXT, error TEXT
                );
                CREATE TABLE IF NOT EXISTS accesses (
                    id TEXT, created REAL, cache_hit INTEGER
                );
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(calls)')}
            for name,definition in [('request_id','TEXT'),('attempt','INTEGER DEFAULT 0'),('group_name','TEXT')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE calls ADD COLUMN {name} {definition}')
            if 'timeout_seconds' not in columns:
                db.execute('ALTER TABLE calls ADD COLUMN timeout_seconds REAL')
            billing.initialize(db)
        self.reconcile()

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def reconcile(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return billing.apply_statement(db,self.reconcile_path)

    def accounting(self,group=None):
        self.reconcile()
        with self.connect() as db:
            return billing.amounts(db,group)

    def projection(self,forecast,*,reserve=0.):
        account=self.accounting();stream=self.accounting(self.budget_group) if self.budget_group else None
        allowed=account['accounted']+forecast+reserve<=self.config.budget
        if stream is not None:allowed=allowed and stream['accounted']+forecast<=self.group_budget
        return {'global':account,'stream_name':self.budget_group,'stream':stream,
                'stream_cap':self.group_budget,'forecast':forecast,'reserve':reserve,'allowed':allowed}

    def ensure_connectivity(self):
        if self.connectivity_probe is None:
            return
        with self._probe_lock:
            if self._probe_success and time.monotonic()-self._probe_success<15:
                return
            started=time.perf_counter()
            try:
                self.connectivity_probe(self.config.base_url,self.config.api_key)
            except Exception as exc:
                error=type(exc).__name__
                with self.connect() as db:
                    db.execute('INSERT INTO connectivity_probes VALUES(?,?,?,?)',(time.time(),0,time.perf_counter()-started,error))
                raise ProviderError('NetworkUnavailable: free models probe failed; paid admissions paused ('+error+')') from None
            with self.connect() as db:
                db.execute('INSERT INTO connectivity_probes VALUES(?,?,?,?)',(time.time(),1,time.perf_counter()-started,None))
            self._probe_success=time.monotonic()

    def call(self, messages: list[dict], *, effort: str | None = None,
             max_tokens: int = 4096, prompt_version: str = '1', retry_failed: bool = False,
             budget_group: str | None = None, group_budget: float | None = None, **options) -> dict:
        c = self.config
        if self.budget_group is not None:
            budget_group=self.budget_group;group_budget=self.group_budget
        effort = effort or c.effort
        body = {'model': c.model, 'messages': messages, 'temperature': 0,
                'max_tokens': max_tokens, 'reasoning_effort': effort, **options}
        if urlsplit(c.base_url).hostname == 'api.openai.com':
            # Reasoning models use the completion cap for visible and reasoning tokens.
            body.pop('temperature', None)
            body.pop('max_tokens', None)
            body['max_completion_tokens'] = max_tokens
            body['store'] = False
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')
        logical_id = hashlib.sha256(c.base_url.encode() + prompt_version.encode() + encoded).hexdigest()
        self.reconcile()
        # Cache replay needs no connectivity. Probe only a possible new paid
        # request, then re-check the cache under the atomic admission lock.
        with self.connect() as db:
            cached=db.execute('SELECT id,response,cost FROM calls WHERE (id=? OR request_id=?) AND response IS NOT NULL LIMIT 1',(logical_id,logical_id)).fetchone()
            if cached:
                db.execute('INSERT INTO accesses VALUES(?,?,1)',(cached['id'],time.time()))
                return {'id':cached['id'],'cache_hit':True,'response':json.loads(cached['response']),'cost':cached['cost'],'latency':0.}
        self.ensure_connectivity()
        ident = logical_id
        attempt = 0
        # UTF-8 byte count bounds common byte-fallback tokenization. Extra allowance
        # covers role wrappers and provider-added framing. Reserve output at max.
        reserve = ((len(encoded) + 4096) * c.input_price + max_tokens * c.output_price) / 1e6
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            attempts = db.execute('SELECT * FROM calls WHERE id=? OR request_id=? ORDER BY attempt DESC', (logical_id,logical_id)).fetchall()
            for row in attempts:
                if row['response'] is not None:
                    db.execute('INSERT INTO accesses VALUES (?, ?, 1)', (row['id'], time.time()))
                    return {'id': row['id'], 'cache_hit': True, 'response': json.loads(row['response']),
                            'cost': row['cost'], 'latency': 0.}
            if attempts:
                row = attempts[0]
                if not retry_failed or row['status']=='in_flight':
                    raise ProviderError(f"Request {logical_id[:12]} already {row['status']}; reservation retained")
                transient = any(token in (row['error'] or '') for token in
                                ('NoRequestSent','URLError','TimeoutError','ConnectionResetError','IncompleteRead','RemoteDisconnected','HTTP 429','HTTP 500','HTTP 502','HTTP 503','HTTP 504'))
                attempt = row['attempt']+1
                delay = (10,30,90)[attempt-1] if transient and attempt<=3 else 300 if transient else 1800
                remaining = row['created']+row['latency']+delay-time.time()
                if remaining>0:
                    raise ProviderError(f'Retry deferred for {remaining:.1f}s; prior reservation retained')
                ident = hashlib.sha256(f'{logical_id}:attempt:{attempt}'.encode()).hexdigest()
            spent = billing.amounts(db)['accounted']
            if spent + reserve > min(140., c.budget):
                raise BudgetError(f'Budget exhausted: accounted ${spent:.6f}; requested ${reserve:.6f}')
            if budget_group and group_budget is not None:
                group_spent = billing.amounts(db,budget_group)['accounted']
                if group_spent+reserve>group_budget:
                    raise BudgetError(f'Group budget exhausted: {budget_group}; accounted ${group_spent:.6f}')
            if not c.api_key or not c.base_url or not c.model:
                raise ProviderError('Provider requires LOCQA_BASE_URL, LOCQA_MODEL and LOCQA_API_KEY')
            db.execute('INSERT INTO calls(id,model,effort,created,status,reserved,request_id,attempt,group_name) VALUES(?,?,?,?,?,?,?,?,?)',
                       (ident, c.model, effort, time.time(), 'in_flight', reserve,logical_id,attempt,budget_group))
            db.execute('INSERT INTO accesses VALUES (?, ?, 0)', (ident, time.time()))
            latencies=[r[0] for r in db.execute("SELECT latency FROM calls WHERE effort=? AND status='complete' ORDER BY created DESC LIMIT 100",(effort,))]
            timeout=adaptive_timeout(effort,max_tokens,latencies)
            db.execute('UPDATE calls SET timeout_seconds=? WHERE id=?',(timeout,ident))
        started = time.perf_counter()
        try:
            with self._attempt_lock:
                self._transport_attempts += 1
            response = (self.transport(c.base_url + '/chat/completions', c.api_key, encoded,timeout=timeout)
                        if self.transport is http_post else self.transport(c.base_url + '/chat/completions', c.api_key, encoded))
            latency = time.perf_counter() - started
            # Never persist a credential even if a misconfigured endpoint echoes it.
            safe = json.dumps(response, ensure_ascii=False).replace(c.api_key, '[REDACTED]')
            response = json.loads(safe)
            usage = response.get('usage') or {}
            known = isinstance(usage.get('prompt_tokens'), int) and isinstance(usage.get('completion_tokens'), int)
            inp, out = usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0)
            cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens', 0) or 0
            reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0
            cost = ((inp - min(inp, cached)) * c.input_price + min(inp, cached) * c.cached_price + out * c.output_price) / 1e6 if known else 0.
            with self.connect() as db:
                db.execute('''UPDATE calls SET status=?,reserved=?,cost=?,latency=?,input_tokens=?,
                    cached_tokens=?,output_tokens=?,reasoning_tokens=?,response=? WHERE id=?''',
                    ('complete' if known else 'unknown_usage', 0 if known else reserve, cost,
                     latency, inp, cached, out, reasoning, safe, ident))
            return {'id': ident, 'cache_hit': False, 'response': response, 'cost': cost, 'latency': latency}
        except Exception as exc:
            self._probe_success=0.
            # Exception bodies may contain headers: retain only the class and HTTP status.
            error = type(exc).__name__ + (f' HTTP {exc.code}' if isinstance(exc, urllib.error.HTTPError) else '')
            with self.connect() as db:
                if isinstance(exc,NoRequestSent):
                    db.execute('UPDATE calls SET status=?,reserved=0,error=?,latency=? WHERE id=?',
                               ('failed_not_sent',error,time.perf_counter()-started,ident))
                else:
                    db.execute('UPDATE calls SET status=?,error=?,latency=? WHERE id=?',
                               ('failed_unknown_charge', error, time.perf_counter()-started, ident))
            if isinstance(exc, QuotaError):
                raise
            raise ProviderError(error) from None

    def summary(self,group=None) -> dict:
        self.reconcile()
        with self.connect() as db:
            where=' WHERE group_name=?' if group is not None else ''
            parameters=(group,) if group is not None else ()
            totals = dict(db.execute('''SELECT COUNT(*) calls, COALESCE(SUM(cost),0) cost_usd,
                COALESCE(SUM(reserved),0) reserved_usd, COALESCE(SUM(input_tokens),0) input_tokens,
                COALESCE(SUM(cached_tokens),0) cached_tokens, COALESCE(SUM(output_tokens),0) output_tokens,
                COALESCE(SUM(reasoning_tokens),0) reasoning_tokens FROM calls'''+where,parameters).fetchone())
            totals['cache_hits'] = db.execute('SELECT COUNT(*) FROM accesses a JOIN calls c ON c.id=a.id WHERE a.cache_hit=1'+
                (' AND c.group_name=?' if group is not None else ''),parameters).fetchone()[0]
            accounting=billing.amounts(db,group)
            totals['reconciliation_hold_usd']=accounting['reconciliation_hold']
            totals['accounted_usd']=accounting['accounted']
            return totals

    @property
    def transport_attempts(self):
        with self._attempt_lock:
            return self._transport_attempts

    def call_with_retry(self,messages,**options):
        """Bounded transient retries; run in a worker while other work continues."""
        options['retry_failed'] = True
        for delay in (10,30,90,None):
            try:
                return self.call(messages,**options)
            except ProviderError as exc:
                transient = any(token in str(exc) for token in
                                ('NetworkUnavailable','NoRequestSent','URLError','TimeoutError','ConnectionResetError','IncompleteRead','RemoteDisconnected','HTTP 429','HTTP 500','HTTP 502','HTTP 503','HTTP 504','Retry deferred'))
                if not transient or delay is None:
                    raise
                time.sleep(delay+0.1)

