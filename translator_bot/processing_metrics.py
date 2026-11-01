"""Bounded recent automatic-delivery metrics; no message text retained."""
from collections import deque
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
import uuid
import math
import statistics
import time


class ProcessingMetrics:
    def __init__(self, *, limit=50, clock=time.time, path=None):
        self.clock = clock
        self.started = clock()
        self.limit = limit
        self.samples = {}
        self.path=Path(path) if path is not None else None
        self.session=uuid.uuid4().hex
        self.history_persisted=False
        self.counters_persisted=False
        if self.path:
            try:
                self._load_storage()
            except (OSError,sqlite3.Error):
                # Optional observability must not prevent gateway startup.
                # Preserve the original file for recovery; never reset it here.
                self.path=None
                self.history_persisted=False
                logging.getLogger('translator_bot').warning('Status storage unavailable at startup; retaining metrics in this process only')

    def _load_storage(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self._connect()) as db,db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY,guild INTEGER NOT NULL,payload TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS deliveries_guild ON deliveries(guild,id)')
            db.execute('CREATE TABLE IF NOT EXISTS counter_sessions (session TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS counter_totals (name TEXT PRIMARY KEY,value INTEGER NOT NULL)')
            self.history_persisted=True
            for guild,payload in db.execute('SELECT guild,payload FROM deliveries ORDER BY id'):
                try:
                    sample=json.loads(payload)
                    self._validate_sample(sample,guild)
                except (ValueError,TypeError,KeyError,OverflowError):
                    self.history_persisted=False
                    logging.getLogger('translator_bot').warning('Ignoring an invalid saved status sample; valid history retained')
                    continue
                self.samples.setdefault(guild,deque(maxlen=self.limit)).append(sample)
        self.counters_persisted=True

    @staticmethod
    def _validate_sample(sample,guild):
        if not isinstance(sample,dict) or sample.get('guild')!=guild or not isinstance(sample.get('outcome'),str):
            raise ValueError('Invalid delivery sample identity')
        for key in ('at','total','placeholder','collection','queue','translation','delivery','reused_characters','total_characters'):
            value=sample[key]
            if type(value) not in (int,float) or not math.isfinite(value) or value<0:
                raise ValueError('Invalid delivery measurement')
        if type(sample.get('cache_hit')) is not bool or sample['reused_characters']>sample['total_characters']:
            raise ValueError('Invalid reuse measurement')

    def _connect(self):
        return sqlite3.connect(self.path,timeout=.2)

    def host_counters(self,counters):
        """Persist monotonic per-process deltas, not entry counts or percentages."""
        if self.path is None:
            self.counters_persisted=False
            return counters
        try:
            with closing(self._connect()) as db,db:
                db.execute('BEGIN IMMEDIATE')
                previous=db.execute('SELECT payload FROM counter_sessions WHERE session=?',(self.session,)).fetchone()
                old=json.loads(previous[0]) if previous else {}
                if not isinstance(old,dict) or any(type(v) is not int or v<0 for v in old.values()):
                    raise ValueError('Invalid persisted session counters')
                for key,value in counters.items():
                    if type(value) is not int or value<0:continue
                    delta=max(0,value-old.get(key,0))
                    db.execute('INSERT INTO counter_totals VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value',(key,delta))
                db.execute('INSERT OR REPLACE INTO counter_sessions VALUES(?,?)',(self.session,json.dumps(counters)))
                totals=dict(db.execute('SELECT name,value FROM counter_totals'))
                if any(type(v) is not int or v<0 for v in totals.values()):
                    raise ValueError('Invalid persisted counter totals')
            self.counters_persisted=True
            return totals
        except (OSError,sqlite3.Error,ValueError,TypeError):
            self.counters_persisted=False
            logging.getLogger('translator_bot').warning('Persistent status counters unavailable; showing current process only')
            return counters

    def record(self, guild, outcome, *, total, placeholder=0, collection=0,
               queue=0, translation=0, delivery=0, cache_hit=False,reused_characters=0,total_characters=0):
        sample=dict(at=self.clock(), guild=guild, outcome=outcome,
            total=total, placeholder=placeholder, collection=collection,
            queue=queue, translation=translation, delivery=delivery, cache_hit=cache_hit,
            reused_characters=max(0,min(reused_characters,total_characters)),total_characters=max(0,total_characters))
        self.samples.setdefault(guild,deque(maxlen=self.limit)).append(sample)
        if self.path:
            try:
                with closing(self._connect()) as db,db:
                    db.execute('INSERT INTO deliveries(guild,payload) VALUES(?,?)',(guild,json.dumps(sample)))
                    db.execute('DELETE FROM deliveries WHERE guild=? AND id NOT IN '
                        '(SELECT id FROM deliveries WHERE guild=? ORDER BY id DESC LIMIT ?)',(guild,guild,self.limit))
            except (OSError,sqlite3.Error):
                self.history_persisted=False
                logging.getLogger('translator_bot').warning('Persistent delivery metrics unavailable; current-process sample retained')

    def summary(self, guild):
        now = self.clock()
        recent = list(self.samples.get(guild, ()))
        delivered = [s for s in recent if s['outcome']=='delivered']
        times = sorted(s['total'] for s in delivered)
        result = dict(delivered=len(delivered), failed=sum(s['outcome']=='failed' for s in recent),
            missing=sum(s['outcome']=='missing' for s in recent),
            removed=sum(s['outcome']=='removed' for s in recent),
            samples=len(recent), sample_span_seconds=max(0, now-recent[0]['at']) if recent else 0,
            sample_limit=self.limit, cache_hits=sum(bool(s['cache_hit']) for s in delivered))
        result['partial_cache_deliveries']=sum(0<s['reused_characters']<s['total_characters'] for s in delivered)
        result['reused_characters']=sum(s['reused_characters'] for s in delivered)
        result['total_characters']=sum(s['total_characters'] for s in delivered)
        for field in ('total','placeholder','collection','queue','translation','delivery'):
            result[field] = statistics.fmean(s[field] for s in delivered) if delivered else None
        result['median'] = statistics.median(times) if times else None
        result['p95'] = times[max(0, math.ceil(len(times)*.95)-1)] if times else None
        return result
