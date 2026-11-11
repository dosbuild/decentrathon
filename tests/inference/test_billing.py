import json
import time
from datetime import datetime,timezone

import pytest

from dalel.inference.client import Provider,Config,BudgetError


def setup(tmp_path):
    statement=tmp_path/'LEDGER_RECONCILE.md'
    provider=Provider(Config('https://synthetic.invalid/v1','synthetic','test-key'),tmp_path/'ledger.db',lambda *a:{},reconcile_path=statement)
    now=time.time()
    with provider.connect() as db:
        db.execute("INSERT INTO calls(id,created,status,cost,reserved,group_name) VALUES('known',?,'complete',3,0,'teacher')",(now-100,))
        db.execute("INSERT INTO calls(id,created,status,cost,reserved,group_name) VALUES('unknown',?,'failed_unknown_charge',0,40,'teacher')",(now-100,))
    return provider,statement,now-50


def invoice(path,amount,cutoff):
    path.write_text(f'PROVIDER_BILLED_USD: {amount}\nAS_OF: {datetime.fromtimestamp(cutoff,timezone.utc).isoformat()}\n',encoding='utf8',newline='\n')


def test_no_statement_never_releases_unknown(tmp_path):
    provider,statement,cutoff=setup(tmp_path)
    assert provider.summary()['accounted_usd']==43
    assert provider.summary()['reserved_usd']==40


def test_statement_releases_only_covered_unknowns_and_retains_billed_floor(tmp_path):
    provider,statement,cutoff=setup(tmp_path)
    invoice(statement,5,cutoff)
    result=provider.reconcile()
    assert result['released_reservations']==1 and result['released_usd']==40
    assert provider.summary()['accounted_usd']==5
    assert provider.accounting('teacher')['accounted']==5
    assert provider.summary()['reserved_usd']==0
    # Reapplying is idempotent and preserves original-reservation audit evidence.
    assert provider.reconcile()['released_reservations']==0
    with provider.connect() as db:
        assert db.execute('SELECT previous_reserved FROM reservation_releases').fetchone()[0]==40
        db.execute("INSERT INTO calls(id,created,status,cost,reserved,group_name) VALUES('later',?,'complete',4,0,'teacher')",(cutoff+20,))
        db.execute("INSERT INTO calls(id,created,status,cost,reserved,group_name) VALUES('uncertainLater',?,'failed_unknown_charge',0,2,'teacher')",(cutoff+20,))
    assert provider.summary()['accounted_usd']==11  # historical billed5 + known later4 + reserve2
    assert provider.summary()['reserved_usd']==2


def test_billed_total_never_erases_known_costs_or_live_reservations(tmp_path):
    provider,statement,cutoff=setup(tmp_path)
    with provider.connect() as db:
        db.execute("INSERT INTO calls(id,created,status,cost,reserved) VALUES('live',?,'in_flight',0,2)",(cutoff-10,))
    invoice(statement,1,cutoff)
    assert provider.summary()['accounted_usd']==5
    assert provider.summary()['reserved_usd']==2


def test_invalid_statement_never_changes_accounting(tmp_path):
    provider,statement,cutoff=setup(tmp_path)
    statement.write_text('PROVIDER_BILLED_USD: 0\nAS_OF: invalid\n',encoding='utf8')
    with pytest.raises(ValueError):
        provider.reconcile()
    with provider.connect() as db:
        assert db.execute("SELECT reserved FROM calls WHERE id='unknown'").fetchone()[0]==40


def test_reconciled_floor_still_enforces_group_cap(tmp_path):
    provider,statement,cutoff=setup(tmp_path)
    invoice(statement,59.99,cutoff)
    with pytest.raises(BudgetError):
        provider.call([],max_tokens=32000,budget_group='teacher',group_budget=60)


def test_provider_json_statement_preserves_unknown_floor_and_audit(tmp_path):
    provider, _, cutoff = setup(tmp_path)
    path = tmp_path/'provider_statement.json'
    path.write_bytes(json.dumps({'status':'fetched','provider_total_usd':'7.125',
        'as_of':datetime.fromtimestamp(cutoff,timezone.utc).isoformat()}).encode())
    provider.reconcile_path = path
    assert provider.summary()['accounted_usd'] == 7.125
    assert provider.summary()['reconciliation_hold_usd'] == 4.125
    with provider.connect() as db:
        assert db.execute('SELECT SUM(previous_reserved) FROM reservation_releases').fetchone()[0] == 40
