"""Private append-only approvals and bounded hash-only inference audit."""
from contextlib import closing
import hashlib
import json
import time


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS reuse_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, key BLOB NOT NULL,
            evidence_json TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS reuse_rules (
            key BLOB PRIMARY KEY, source BLOB NOT NULL, policy BLOB NOT NULL,
            record_json TEXT NOT NULL, global_revision INTEGER NOT NULL,
            source_revision INTEGER NOT NULL, approval_id INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS reuse_rules_source ON reuse_rules(source);
        CREATE TABLE IF NOT EXISTS reuse_revocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id INTEGER NOT NULL,
            reason TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS result_reuse_dependencies (
            result_key BLOB NOT NULL, kind TEXT NOT NULL, dependency_key BLOB NOT NULL,
            source BLOB NOT NULL, approval_id INTEGER NOT NULL,
            PRIMARY KEY(result_key,kind,dependency_key));
        CREATE INDEX IF NOT EXISTS reuse_dependencies_source ON result_reuse_dependencies(source);
        CREATE TABLE IF NOT EXISTS reuse_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, message BLOB NOT NULL, version BLOB NOT NULL,
            origin TEXT NOT NULL, phase TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
    ''')


def dependencies_valid(db,dependencies):
    for kind,key,source,global_rev,source_rev,receipt in dependencies:
        table='translation_memory' if kind=='memory' else 'reuse_rules'
        row=db.execute(f'SELECT 1 FROM {table} m JOIN cache_revision c ON c.id=1 '
            'LEFT JOIN source_revisions s ON s.source=m.source '
            'WHERE m.key=? AND m.source=? AND m.approval_id=? AND m.global_revision=? '
            'AND m.source_revision=? AND m.global_revision=c.revision '
            'AND m.source_revision=COALESCE(s.revision,0)',(key,source,receipt,global_rev,source_rev)).fetchone()
        if not row:return False
    return True


def invalidate_dependents(db,source):
    keys=[r[0] for r in db.execute('SELECT result_key FROM result_reuse_dependencies WHERE source=?',(source,))]
    for key in keys:
        row=db.execute('SELECT source FROM results WHERE key=?',(key,)).fetchone()
        if row and row[0]!=source:
            db.execute('INSERT INTO source_revisions VALUES(?,1) ON CONFLICT(source) DO UPDATE SET revision=revision+1',(row[0],))
        db.execute('DELETE FROM results WHERE key=?',(key,))
        db.execute('DELETE FROM result_reuse_dependencies WHERE result_key=?',(key,))


class ReuseStore:
    def __init__(self,cache):self.cache=cache

    def recent_summary(self,limit=50):
        """Latest distinct automatic source versions; inference, not deliveries.

        Combine probe + translation phases. Old payloads have no miss taxonomy
        and must not be relabelled as recent diagnosed events after restart.
        """
        with closing(self.cache._connect()) as db:
            rows=db.execute("SELECT message,version,phase,payload FROM reuse_requests WHERE origin='automatic' ORDER BY id DESC LIMIT 2000").fetchall()
        groups={}
        invalid=0
        for message,version,phase,raw in rows:
            try:p=json.loads(raw)
            except (ValueError,TypeError):invalid+=1;continue
            if not isinstance(p,dict):invalid+=1;continue
            if p.get('diagnostics_version')!=2:continue
            if phase not in ('translation','probe'):continue
            key=(message,version)
            if key not in groups and len(groups)>=limit:continue
            phases=groups.setdefault(key,{})
            # A repeated attempt of the same phase is not another request.
            phases.setdefault(phase,p)
        result=dict(sample_limit=limit,samples=len(groups),invalid_events=invalid,whole_misses={},fragment_blockers={},
            full_cache=0,partial_inference=0,no_reuse_inference=0,failed=0,
            note='Host automatic source versions; latest phase attempts, not completed deliveries or proven missed reuse.')
        candidates={};measurements=[]
        for (message,version),phases in groups.items():
            # Newest phase wins: a later successful probe supersedes an older
            # inference for the same source version, not the other way around.
            p=next(iter(phases.values()))
            measurements.extend(p.get('model_measurements',[]))
            for kind,fingerprint,characters in p.get('candidates',[]):
                if kind not in ('field','ngram','template_candidate'):continue
                # Direction is part of opportunity identity, not just spelling.
                for direction in p.get('directions',[]):
                    g=candidates.setdefault((fingerprint,tuple(direction)),dict(messages=set(),used=set()))
                    g['messages'].add(message)
                    if fingerprint in p.get('used_units',[]):g['used'].add(message)
            if p.get('status')=='failed':result['failed']+=1
            elif p.get('returned_translation'):
                if p.get('model_calls',0)==0:result['full_cache']+=1
                elif p.get('reused_characters',0)>0:result['partial_inference']+=1
                else:result['no_reuse_inference']+=1
            for field in ('whole_misses','fragment_blockers'):
                for reason,count in p.get(field,{}).items():
                    result[field][reason]=result[field].get(reason,0)+count
        result['repeat_candidates']=sum(len(g['messages'])>1 for g in candidates.values())
        result['unreused_repeat_candidates']=sum(len(g['messages'])>1 and not g['used'] for g in candidates.values())
        model=dict(calls=len(measurements),native_cache_samples=0,native_cached_tokens=0,prefill_seconds=0,generation_seconds=0)
        for m in measurements:
            cached=m.get('native_cached_tokens')
            if cached is not None:
                model['native_cache_samples']+=1;model['native_cached_tokens']+=cached
            model['prefill_seconds']+=m.get('prefill_seconds') or 0
            model['generation_seconds']+=m.get('generation_seconds') or 0
        if not model['native_cache_samples']:model['native_cached_tokens']=None
        result['model_runtime']=model
        return result

    def approve(self,key,source,policy,record):
        with closing(self.cache._connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            revision=self.cache._revision(db,source)
            if tuple(record['invalidation_revision'])!=revision:raise ValueError('Rule invalidated during review.')
            invalidate_dependents(db,source)
            raw=json.dumps(record,ensure_ascii=False)
            receipt=db.execute('INSERT INTO reuse_approvals(key,evidence_json,created) VALUES(?,?,?)',(key,raw,time.time())).lastrowid
            db.execute('INSERT OR REPLACE INTO reuse_rules VALUES(?,?,?,?,?,?,?)',(key,source,policy,raw,*revision,receipt))
            return receipt

    def rules(self):
        with closing(self.cache._connect()) as db:
            rows=db.execute('SELECT m.key,m.source,m.policy,m.record_json,m.global_revision,m.source_revision,m.approval_id '
                'FROM reuse_rules m JOIN cache_revision c ON c.id=1 '
                'LEFT JOIN source_revisions s ON s.source=m.source '
                'WHERE m.global_revision=c.revision AND m.source_revision=COALESCE(s.revision,0)').fetchall()
        return [dict(record=json.loads(raw),policy=policy,receipt=receipt,
            dependency=('rule',key,source,g,s,receipt)) for key,source,policy,raw,g,s,receipt in rows]

    def valid(self,dependencies):
        if not dependencies:return True
        with closing(self.cache._connect()) as db:return dependencies_valid(db,dependencies)

    def revoke(self,receipt,reason):
        if type(receipt) is not int or receipt<=0 or not isinstance(reason,str) or len(reason.strip())<12:
            raise ValueError('Revoke an exact receipt with a substantive private reason.')
        with closing(self.cache._connect()) as db:
            row=db.execute('SELECT source FROM reuse_rules WHERE approval_id=?',(receipt,)).fetchone()
        if row is None:raise ValueError('Receipt is not an active reuse rule.')
        self.cache.invalidate(row[0],reason=reason,expected_receipt=receipt)

    def audit(self,message,version,origin,phase,payload):
        with closing(self.cache._connect()) as db,db:
            db.execute('INSERT INTO reuse_requests(message,version,origin,phase,payload,created) VALUES(?,?,?,?,?,?)',
                (message,version,origin,phase,json.dumps(payload),time.time()))
            self.cache._trim(db,'reuse_requests','id','id',2000)
