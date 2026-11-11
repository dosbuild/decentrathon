"""Reconcile unknown reservations against an explicit provider billing statement.

Provider totals establish an as-of floor. Known later costs and still-active
reservations remain additive, so an old dashboard snapshot cannot fund new work.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS billing_reconciliations (
        source_sha TEXT PRIMARY KEY, as_of REAL NOT NULL, billed REAL NOT NULL,
        applied REAL NOT NULL, source_path TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS reservation_releases (
        source_sha TEXT, call_id TEXT, previous_reserved REAL, released_at REAL,
        PRIMARY KEY(source_sha,call_id)
      );
      CREATE TABLE IF NOT EXISTS connectivity_probes (
        created REAL, succeeded INTEGER, latency REAL, error TEXT
      );
    ''')


def apply_statement(db,path: Path | None):
    if path is None or not path.exists():
        return None
    raw=path.read_bytes()
    text=raw.decode('utf-8-sig')
    if path.suffix.lower()=='.json':
        statement=json.loads(text)
        if statement.get('status')!='fetched':
            raise ValueError('Unsuccessful provider billing statement cannot release reserves')
        amount=[statement['provider_total_usd']]
        dates=[statement['as_of']]
    else:
        amount=re.findall(r'^PROVIDER_BILLED_USD:\s*([0-9]+(?:\.[0-9]+)?)\s*$',text,re.M)
        dates=re.findall(r'^AS_OF:\s*(\S+)\s*$',text,re.M)
    if len(amount)!=1 or len(dates)!=1:
        raise ValueError('Billing statement must have one billed amount and one AS_OF timestamp')
    billed=float(amount[0])
    moment=datetime.fromisoformat(dates[0].replace('Z','+00:00'))
    if not math.isfinite(billed) or billed<0 or moment.tzinfo is None:
        raise ValueError('Invalid billing amount or timezone-less AS_OF')
    cutoff=moment.timestamp()
    if cutoff>time.time()+60:
        raise ValueError('Billing statement AS_OF is in the future')
    digest=hashlib.sha256(raw).hexdigest()
    latest=db.execute('SELECT * FROM billing_reconciliations ORDER BY as_of DESC,applied DESC LIMIT 1').fetchone()
    if latest and cutoff<latest['as_of']:
        return None
    db.execute('INSERT OR IGNORE INTO billing_reconciliations VALUES(?,?,?,?,?)',
               (digest,cutoff,billed,time.time(),str(path)))
    eligible=db.execute("SELECT id,reserved FROM calls WHERE created<=? AND status IN ('failed_unknown_charge','unknown_usage') AND reserved>0",(cutoff,)).fetchall()
    for row in eligible:
        db.execute('INSERT OR IGNORE INTO reservation_releases VALUES(?,?,?,?)',
                   (digest,row['id'],row['reserved'],time.time()))
        db.execute('UPDATE calls SET reserved=0 WHERE id=?',(row['id'],))
    return {'source_sha':digest,'as_of':dates[0],'provider_billed_usd':billed,
            'released_reservations':len(eligible),'released_usd':sum(row['reserved'] for row in eligible)}


def amounts(db,group=None):
    condition=' WHERE group_name=?' if group else ''
    parameters=(group,) if group else ()
    row=db.execute('SELECT COALESCE(SUM(cost),0) known,COALESCE(SUM(reserved),0) reserved FROM calls'+condition,parameters).fetchone()
    hold=0.
    latest=db.execute('SELECT * FROM billing_reconciliations ORDER BY as_of DESC,applied DESC LIMIT 1').fetchone()
    if latest:
        known_before=db.execute('SELECT COALESCE(SUM(cost),0) FROM calls WHERE created<=?',(latest['as_of'],)).fetchone()[0]
        hold=max(0.,latest['billed']-known_before)
        if group:
            # Actual unknown attribution is unavailable. Conservatively charge
            # the whole unassigned remainder to each affected capped group.
            affected=db.execute('SELECT COUNT(*) FROM reservation_releases r JOIN calls c ON r.call_id=c.id WHERE c.group_name=? AND c.created<=?',
                                (group,latest['as_of'])).fetchone()[0]
            if not affected:
                hold=0.
    return {'known':row['known'],'reserved':row['reserved'],'reconciliation_hold':hold,
            'accounted':row['known']+row['reserved']+hold}
