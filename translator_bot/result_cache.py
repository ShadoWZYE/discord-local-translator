"""Retained local SQLite cache. No source/context plaintext is stored."""
from contextlib import closing
from pathlib import Path
import sqlite3
import time
from .reuse_store import initialize as initialize_reuse, dependencies_valid, invalidate_dependents

DEFAULT_RESULT_LIMIT=100_000


def capacity_status(db, limit=DEFAULT_RESULT_LIMIT):
    """Cheap read-only soft-capacity gauge; never evicts translations."""
    count=db.execute('SELECT COUNT(*) FROM results').fetchone()[0]
    percent=100*count/limit if limit>0 else 0
    pressure='over_capacity' if percent>=100 else 'high' if percent>=90 else 'warning' if percent>=80 else 'normal'
    return {'retained_results':count,'configured_result_limit':limit,
            'capacity_percent':round(percent,2),'remaining_before_target':max(0,limit-count),
            'pressure':pressure,'retention':'no_age_expiry; soft_capacity; targeted_invalidation_only',
            'database_bytes':db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]}


class ResultCache:
    def __init__(self, path: Path, *, limit: int = DEFAULT_RESULT_LIMIT, ttl: float | None = None, touch_interval: float = 0, auto_trim: bool = False):
        self.path, self.limit, self.ttl = path, limit, ttl
        # TTL/eviction remain explicit opt-ins for isolated callers, never the bot default.
        self.auto_trim = auto_trim
        self.touch_interval = touch_interval
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cache_revision (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO cache_revision VALUES(1,0);
                CREATE TABLE IF NOT EXISTS results (
                    key BLOB PRIMARY KEY, value TEXT NOT NULL,
                    expires REAL NOT NULL, accessed REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS results_accessed ON results(accessed);
                CREATE TABLE IF NOT EXISTS source_revisions (source BLOB PRIMARY KEY, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS memory_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, key BLOB NOT NULL,
                    evidence_json TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS translation_memory (
                    key BLOB PRIMARY KEY, source BLOB NOT NULL, value TEXT NOT NULL,
                    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('portable','dependency_block')),
                    global_revision INTEGER NOT NULL, source_revision INTEGER NOT NULL,
                    approval_id INTEGER NOT NULL, accessed REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS translation_memory_source ON translation_memory(source);
                CREATE TABLE IF NOT EXISTS memory_scopes (source BLOB PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS fragment_observations (
                    unit BLOB PRIMARY KEY, identity BLOB NOT NULL, lookups INTEGER NOT NULL,
                    hits INTEGER NOT NULL, misses INTEGER NOT NULL, repeated_misses INTEGER NOT NULL,
                    eligible_repeated_misses INTEGER NOT NULL, changed_scope_misses INTEGER NOT NULL,
                    reason TEXT NOT NULL, characters INTEGER NOT NULL, last_seen REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS request_fragment_observations (
                    unit BLOB NOT NULL, message BLOB NOT NULL, version BLOB NOT NULL,
                    origin TEXT NOT NULL, identity BLOB NOT NULL, attempts INTEGER NOT NULL,
                    hit INTEGER NOT NULL, eligible INTEGER NOT NULL, characters INTEGER NOT NULL,
                    last_seen REAL NOT NULL, PRIMARY KEY(unit,message,version,origin));
            ''')
            if 'source' not in [row[1] for row in db.execute('PRAGMA table_info(results)')]:
                db.execute('ALTER TABLE results ADD COLUMN source BLOB')
            if 'reviewed' not in [row[1] for row in db.execute('PRAGMA table_info(results)')]:
                db.execute('ALTER TABLE results ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE INDEX IF NOT EXISTS results_source ON results(source)')
            db.execute('CREATE INDEX IF NOT EXISTS results_expires ON results(expires)')
            db.execute('CREATE INDEX IF NOT EXISTS fragment_observations_seen ON fragment_observations(last_seen)')
            db.execute('CREATE INDEX IF NOT EXISTS request_fragment_observations_seen ON request_fragment_observations(last_seen)')
            initialize_reuse(db)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=0.2)

    @staticmethod
    def _revision(db, source):
        revision = db.execute('SELECT revision FROM cache_revision WHERE id=1').fetchone()[0]
        if source is None:
            return revision
        row = db.execute('SELECT revision FROM source_revisions WHERE source=?', (source,)).fetchone()
        return revision, row[0] if row else 0

    def get(self, key, source=None):
        with closing(self._connect()) as db:
            # Revision and value must come from the same database snapshot.
            db.execute('BEGIN')
            revision = self._revision(db, source)
            row = db.execute('SELECT value,expires,source,accessed FROM results WHERE key=?', (key,)).fetchone()
            now = time.time()
            db.commit()
            if row and (self.ttl is None or row[1] > now) and (source is None or row[2] == source):
                # LRU bookkeeping must not turn a successful WAL read into a
                # miss/lock error or wait behind a model/correction writer.
                if now-row[3] >= self.touch_interval:
                    try:
                        db.execute('PRAGMA busy_timeout=0')
                        with db:
                            db.execute('UPDATE results SET accessed=? WHERE key=? AND accessed<=?',
                                       (now, key, now-self.touch_interval))
                    except sqlite3.Error:
                        pass
                return row[0], revision
            return None, revision

    def put(self, key, value, revision, source=None, *, reviewed=False, dependencies=()):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if self._revision(db, source) != revision or not dependencies_valid(db,dependencies):
                return False  # A report invalidated this generation while it ran.
            now = time.time()
            if self.ttl is not None:
                db.execute('DELETE FROM results WHERE expires<=?', (now,))
            cursor = db.execute('INSERT INTO results(key,value,expires,accessed,source,reviewed) VALUES(?,?,?,?,?,?) '
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value,expires=excluded.expires,'
                       'accessed=excluded.accessed,source=excluded.source,reviewed=excluded.reviewed '
                       'WHERE results.reviewed<=excluded.reviewed',
                       (key, value, now + self.ttl if self.ttl is not None else 0, now, source, int(reviewed)))
            if self.auto_trim:
                self._trim(db,'results','key','accessed',self.limit)
            if cursor.rowcount==1:
                db.execute('DELETE FROM result_reuse_dependencies WHERE result_key=?',(key,))
                db.executemany('INSERT INTO result_reuse_dependencies VALUES(?,?,?,?,?)',
                    [(key,kind,dep_key,dep_source,receipt) for kind,dep_key,dep_source,_,_,receipt in dependencies])
            db.execute('DELETE FROM result_reuse_dependencies WHERE result_key NOT IN (SELECT key FROM results)')
            return cursor.rowcount == 1

    def clear(self):
        with closing(self._connect()) as db, db:
            db.execute('UPDATE cache_revision SET revision=revision+1 WHERE id=1')
            db.execute('DELETE FROM results')
            db.execute('DELETE FROM translation_memory')
            db.execute('DELETE FROM reuse_rules')
            db.execute('DELETE FROM result_reuse_dependencies')

    def memory_get(self, keys, source):
        """Durable approvals have no generated-result TTL/LRU or promotion path."""
        return self.memory_get_many([(keys,source)])[0]

    def memory_get_many(self,requests,*,provenance=False):
        """One fresh SQL snapshot for exact/envelope candidates; no stale negatives."""
        keys=list(dict.fromkeys(key for candidates,_ in requests for key in candidates))
        if not keys:return [None for _ in requests]
        with closing(self._connect()) as db:
            placeholders=','.join('?' for _ in keys)
            rows=db.execute('SELECT m.key,m.source,m.value,m.global_revision,m.source_revision,m.approval_id FROM translation_memory m '
                'JOIN cache_revision c ON c.id=1 '
                'LEFT JOIN source_revisions s ON s.source=m.source '
                f'WHERE m.key IN ({placeholders}) AND m.global_revision=c.revision '
                'AND m.source_revision=COALESCE(s.revision,0)',keys).fetchall()
        values={(key,source):(value,('memory',key,source,g,s,receipt)) if provenance else value
                for key,source,value,g,s,receipt in rows}
        return [next((values[(key,source)] for key in candidates if (key,source) in values),None)
                for candidates,source in requests]

    def approve_memory(self,key,source,value,scope_kind,evidence_json,expected_revision):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            revision = self._revision(db,source)
            if tuple(expected_revision) != revision:
                raise ValueError('Source invalidated since memory review; review again.')
            invalidate_dependents(db,source)
            receipt = db.execute('INSERT INTO memory_approvals(key,evidence_json,created) VALUES(?,?,?)',
                                 (key,evidence_json,time.time())).lastrowid
            db.execute('INSERT OR REPLACE INTO translation_memory VALUES(?,?,?,?,?,?,?,?)',
                       (key,source,value,scope_kind,*revision,receipt,time.time()))
            db.execute('INSERT OR IGNORE INTO memory_scopes VALUES(?)',(source,))
            return receipt

    def has_memory_scope(self,source):
        with closing(self._connect()) as db:
            return db.execute('SELECT 1 FROM memory_scopes WHERE source=?',(source,)).fetchone() is not None

    def memory_count(self):
        with closing(self._connect()) as db:
            return db.execute('SELECT COUNT(*) FROM translation_memory').fetchone()[0]

    @staticmethod
    def _trim(db,table,key,ordered,limit):
        # Identifiers are internal constants, never user input. Avoid scanning
        # the full sorted retention window on every under-capacity write.
        overflow=db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]-limit
        if overflow>0:
            db.execute(f'DELETE FROM {table} WHERE {key} IN '
                f'(SELECT {key} FROM {table} ORDER BY {ordered} ASC LIMIT ?)',(overflow,))

    def observe_fragment(self,unit,identity,*,hit,eligible,reason,characters,request=None):
        """Inference-path observations only; counts are not delivery hit rates.

        No plaintext source/context is recorded. Bound bookkeeping separately
        from translation-memory retention. Failures are handled by callers.
        """
        with closing(self._connect()) as db, db:
            db.execute('INSERT INTO fragment_observations VALUES(?,?,1,?,?,0,0,0,?,?,?) '
                'ON CONFLICT(unit) DO UPDATE SET identity=excluded.identity,lookups=lookups+1,'
                'hits=hits+excluded.hits,misses=misses+excluded.misses,'
                'repeated_misses=repeated_misses+excluded.misses,'
                'eligible_repeated_misses=eligible_repeated_misses+?, '
                'changed_scope_misses=changed_scope_misses+CASE WHEN identity!=excluded.identity THEN excluded.misses ELSE 0 END,'
                'reason=excluded.reason,characters=excluded.characters,last_seen=excluded.last_seen',
                (unit,identity,int(hit),int(not hit),reason,characters,time.time(),int(not hit and eligible)))
            self._trim(db,'fragment_observations','unit','last_seen',10000)
            if request:
                self._observe_request_fragment(db,unit,identity,*request,hit=hit,eligible=eligible,characters=characters)

    def observation_stats(self):
        with closing(self._connect()) as db:
            row=db.execute('SELECT COUNT(*),COALESCE(SUM(lookups),0),COALESCE(SUM(repeated_misses),0),'
                'COALESCE(SUM(eligible_repeated_misses),0),COALESCE(SUM(changed_scope_misses),0) '
                'FROM fragment_observations').fetchone()
            return dict(zip(('distinct_units','lookup_events','repeated_misses','eligible_repeated_misses','changed_scope_misses'),row))

    def observe_request_fragment(self,unit,identity,message,version,origin,*,hit,eligible,characters):
        with closing(self._connect()) as db,db:
            self._observe_request_fragment(db,unit,identity,message,version,origin,hit=hit,eligible=eligible,characters=characters)

    def _observe_request_fragment(self,db,unit,identity,message,version,origin,*,hit,eligible,characters):
        if origin not in ('automatic','retry','regeneration','manual'):origin='manual'
        db.execute('INSERT INTO request_fragment_observations VALUES(?,?,?,?,?,1,?,?,?,?) '
                'ON CONFLICT(unit,message,version,origin) DO UPDATE SET attempts=attempts+1,'
                'hit=MAX(hit,excluded.hit),last_seen=excluded.last_seen',
                (unit,message,version,origin,identity,int(hit),int(eligible),characters,time.time()))
        self._trim(db,'request_fragment_observations','rowid','last_seen',10000)

    def stored_count(self):
        """Retained rows, including legacy identities; not a hit count."""
        with closing(self._connect()) as db:
            if self.ttl is None:
                return db.execute('SELECT COUNT(*) FROM results').fetchone()[0]
            return db.execute('SELECT COUNT(*) FROM results WHERE expires>?',(time.time(),)).fetchone()[0]

    def reviewed_count(self):
        """Approved retained units, not a linguistic accuracy percentage."""
        with closing(self._connect()) as db:
            if self.ttl is None:
                return db.execute('SELECT COUNT(*) FROM results WHERE reviewed=1').fetchone()[0]
            return db.execute('SELECT COUNT(*) FROM results WHERE reviewed=1 AND expires>?',
                              (time.time(),)).fetchone()[0]

    def miss_reason(self, key, source):
        """Hash-only diagnostics; never infer a hit from another context's row."""
        with closing(self._connect()) as db:
            row=db.execute('SELECT expires FROM results WHERE key=?',(key,)).fetchone()
            now=time.time()
            if self.ttl is not None and row and row[0]<=now:return 'expired'
            if db.execute('SELECT 1 FROM results WHERE source=? AND (? OR expires>?) LIMIT 1',(source,self.ttl is None,now)).fetchone():
                return 'other_key'
            revision=db.execute('SELECT revision FROM source_revisions WHERE source=?',(source,)).fetchone()
            return 'invalidated' if revision and revision[0] else 'cold'

    def invalidate(self, source, *, reason='targeted_source_invalidation',expected_receipt=None):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if expected_receipt is not None and not db.execute('SELECT 1 FROM reuse_rules WHERE source=? AND approval_id=?',
                    (source,expected_receipt)).fetchone():
                raise ValueError('Rule changed before revocation; inspect current receipt.')
            invalidate_dependents(db,source)
            db.execute('INSERT INTO source_revisions VALUES(?,1) '
                       'ON CONFLICT(source) DO UPDATE SET revision=revision+1', (source,))
            db.execute('DELETE FROM results WHERE source=?', (source,))
            db.execute('DELETE FROM translation_memory WHERE source=?', (source,))
            db.execute('INSERT INTO reuse_revocations(approval_id,reason,created) '
                       'SELECT approval_id,?,? FROM reuse_rules WHERE source=?',(reason,time.time(),source))
            db.execute('DELETE FROM reuse_rules WHERE source=?', (source,))
            db.execute('DELETE FROM result_reuse_dependencies WHERE result_key NOT IN (SELECT key FROM results)')
