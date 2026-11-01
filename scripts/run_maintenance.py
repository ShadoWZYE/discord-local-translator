"""Native scheduled preflight: start Codex ONLY when actual work is due."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from translator_bot.config import Settings
from translator_bot.maintenance import review_queue
from translator_bot.maintenance_dispatch import dispatch_decision
from translator_bot.result_cache import capacity_status
from translator_bot.maintenance_runtime import resolve_codex

DATA = ROOT / 'data' / 'maintenance'


def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else (default or {})


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def dispatch_lock(path):
    """OS-released lock survives crashes without a stale PID lockout."""
    with path.open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def capacity_check(settings, now):
    """Every cheap preflight; stable pressure buckets avoid per-row AI wakeups."""
    path=settings.database_path.with_suffix('.cache.sqlite3')
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0.2)) as db:
            status=capacity_status(db)
        status['checked_at']=now.isoformat()
        write_json(DATA/'cache_capacity.json',status)
        if status['pressure']!='normal':
            return ['Persistent cache capacity pressure: '+status['pressure']+
                    '. Inspect cache_capacity.json and disk space; review targeted cleanup or capacity expansion. Never delete valid translations by age.']
    except (OSError,sqlite3.Error) as exc:
        return ['Persistent cache capacity check unavailable: '+type(exc).__name__]
    return []


def health_check(settings, now):
    path = DATA / 'health_state.json'
    old = read_json(path)
    if old.get('checked_at') and (now-datetime.fromisoformat(old['checked_at'])).total_seconds() < 86400:
        return old.get('alerts', [])
    alerts = []
    try:
        log = (ROOT/'logs'/'bot.log').read_text(encoding='utf-8')
    except FileNotFoundError:
        log = ''
        alerts.append('Bot log is missing; inspect bot startup without printing credentials.')
    recent = log[max(0, log.rfind('logging in using static token')):]
    samples = []
    for line in recent.splitlines():
        if 'Delivery metrics guild=' not in line: continue
        fields = dict(re.findall(r'(outcome|fast_cache_hit|total|queue|reused_chars|source_chars)=([^ ]+)', line))
        if 'total' in fields: samples.append(fields)
    samples = samples[-50:]
    cache_metrics = {}
    for line in recent.splitlines():
        if 'Cache metrics process_lifetime=' in line:
            try:
                cache_metrics = json.loads(line.split('process_lifetime=',1)[1])
            except (ValueError, TypeError):
                pass
    delivered = [r for r in samples if r['outcome']=='delivered']
    try:
        with closing(sqlite3.connect(settings.database_path.with_suffix('.cache.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)) as db:
            integrity = db.execute('PRAGMA quick_check').fetchone()[0]
            revision = db.execute('SELECT revision FROM cache_revision WHERE id=1').fetchone()[0]
    except sqlite3.Error:
        integrity, revision = 'unavailable_or_corrupt', None
    failed = sum(r['outcome']=='failed' for r in samples)
    if integrity != 'ok': alerts.append('Cache integrity check failed; do not delete/rebuild it automatically.')
    if failed >= 3: alerts.append('At least three recent logged deliveries failed.')
    if recent.count('Automatic failure retry 3/3') >= 3: alerts.append('At least three automatic retries exhausted since login.')
    report = {'checked_at':now.isoformat(), 'sample':'last up to 50 logged deliveries since latest login',
              'samples':len(samples), 'delivered':len(delivered), 'failed':failed,
              'average_delivered_seconds':statistics.mean(float(r['total'].rstrip('s')) for r in delivered) if delivered else None,
              'max_queue_seconds':max((float(r['queue'].rstrip('s')) for r in samples),default=0),
              'actual_fast_cache_hits':sum(r['fast_cache_hit']=='True' for r in delivered),
              'actual_partial_cache_deliveries':sum(0<int(r.get('reused_chars',0))<int(r.get('source_chars',0)) for r in delivered),
              'actual_reused_source_characters':sum(int(r.get('reused_chars',0)) for r in delivered),
              'measured_source_characters':sum(int(r.get('source_chars',0)) for r in delivered),
              'logged_process_cache_metrics':cache_metrics,
              'cache_integrity':integrity,'global_clear_revision':revision,'alerts':alerts}
    # Bounded, read-only source-pattern diagnostics. Low reuse is not an alert
    # and never starts AI or grants linguistic approval.
    try:
        from scripts.reuse_report import report as reuse_report
        reuse=reuse_report(settings.database_path.with_suffix('.cache.sqlite3'),settings.database_path)
        write_json(DATA/'reuse_patterns.json',reuse)
        report['reuse_audit']={k:v for k,v in reuse.items() if k not in ('candidates','limitations')}
    except (OSError,sqlite3.Error,ValueError,KeyError):
        report['reuse_audit']={'status':'unavailable'}
    write_json(path, report)
    return alerts


def _memory_check(settings,now):
    """Daily cheap agreement scan, then bounded evidence-gated reviewer work."""
    from translator_bot.reuse_concordance import historical_report,review_candidates,digest
    path=DATA/'memory_concordance.json'
    state=read_json(DATA/'memory_candidate_state.json')
    try:
        stamp=datetime.fromisoformat(state['checked_at'])
        fresh=stamp.tzinfo is not None and (now-stamp).total_seconds()<86400 and path.exists() and state.get('schema')=='historical-concordance-v2'
    except (KeyError,ValueError,TypeError):fresh=False
    if not fresh:
        report=historical_report(settings.database_path)
        write_json(path,report)
        write_json(DATA/'memory_candidate_state.json',{'checked_at':now.isoformat(),'schema':report.get('schema')})
    else:report=read_json(path)
    approved=set()
    cache_path=settings.database_path.with_suffix('.cache.sqlite3')
    if cache_path.exists():
        with closing(sqlite3.connect(cache_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'translation_memory' in tables:
                for raw, in db.execute('SELECT a.evidence_json FROM translation_memory m JOIN memory_approvals a ON a.id=m.approval_id '
                        'JOIN cache_revision c ON c.id=1 LEFT JOIN source_revisions s ON s.source=m.source '
                        'WHERE m.scope_kind=\'portable\' AND m.global_revision=c.revision AND m.source_revision=COALESCE(s.revision,0)'):
                    r=json.loads(raw);approved.add((r['source_language'],r['target_language'],r['source_text']))
            if 'reuse_rules' in tables:
                for raw, in db.execute('SELECT m.record_json FROM reuse_rules m JOIN cache_revision c ON c.id=1 '
                        'LEFT JOIN source_revisions s ON s.source=m.source WHERE m.global_revision=c.revision '
                        'AND m.source_revision=COALESCE(s.revision,0)'):
                    r=json.loads(raw)
                    approved.add((r['source_language'],r['target_language'],r['source_template'] if r['rule_kind']=='template' else r['source_text']))
    selected=review_candidates(report,read_json(DATA/'memory_review_attempts.json'),approved,now=now)
    return selected,(['Memory scope review due: '+digest([v['fingerprint'] for v in selected])] if selected else [])


def memory_check(settings,now):
    try:return _memory_check(settings,now)
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):
        # Optional pattern discovery must not prevent flag/integrity maintenance.
        write_json(DATA/'memory_candidate_error.json',{'checked_at':now.isoformat(),'status':'unavailable'})
        return [],[]


def reviewer_completion(run_dir,queue,attempts,started_at):
    """Operational evidence, NOT linguistic approval or publication certification."""
    successful_commands=0
    try:
        for line in (run_dir/'events.jsonl').read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            event=json.loads(line);item=event.get('item',{})
            if (event.get('type')=='item.completed' and item.get('type')=='command_execution'
                    and item.get('status')=='completed' and item.get('exit_code')==0):
                successful_commands+=1
    except (OSError,ValueError,TypeError,AttributeError):
        return False,'missing_or_invalid_command_evidence'
    if not successful_commands:return False,'no_successful_reviewer_commands'
    for group in (queue['groups'][:10] if queue['due'] else []):
        for ident,expected in group['fingerprints'].items():
            receipt=attempts.get(str(ident),{})
            try:stamp=datetime.fromisoformat(receipt.get('attempted_at',''))
            except (ValueError,TypeError):return False,'missing_fresh_review_receipts'
            if (stamp.tzinfo is None or stamp<started_at or receipt.get('fingerprint')!=expected
                    or receipt.get('outcome') not in ('corrected_verified','needs_clarification','acceptable_variant','unavailable')):
                return False,'missing_fresh_review_receipts'
    for candidate in read_json(run_dir/'memory_candidates.json',default=[]):
        receipt=read_json(DATA/'memory_review_attempts.json').get(candidate['fingerprint'],{})
        try:stamp=datetime.fromisoformat(receipt.get('attempted_at',''))
        except (ValueError,TypeError):return False,'missing_fresh_memory_review_receipts'
        if (stamp.tzinfo is None or stamp<started_at or receipt.get('fingerprint')!=candidate['fingerprint'] or
                receipt.get('outcome') not in ('approved','rejected','deferred')):
            return False,'missing_fresh_memory_review_receipts'
    return True,'commands_and_review_receipts_verified'


def load_queue(settings,force=False):
    with closing(sqlite3.connect(settings.database_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(row) for row in db.execute("SELECT * FROM translation_feedback WHERE status IN ('open','reviewed')")
              if row['guild_id'] in settings.guild_ids]
    return review_queue(rows,read_json(DATA/'review_attempts.json'),force=force)


def session_zero():
    if os.name!='nt':return False
    import ctypes
    session=ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(),ctypes.byref(session)):
        raise OSError('Unable to identify maintenance Windows session')
    return session.value==0


def request_interactive_review(args):
    """Boot/session-zero gate stays cheap; AI requires the signed-in user token."""
    if args.force:raise RuntimeError('Run manual --force from a signed-in user session.')
    with dispatch_lock(DATA/'dispatcher.lock') as acquired:
        if not acquired:
            print(json.dumps({'launch':False,'reason':'reviewer_already_running'}));return
        settings=Settings.load();now=datetime.now(timezone.utc)
        queue=load_queue(settings)
        _,memory_alerts=memory_check(settings,now)
        decision=dispatch_decision(queue,read_json(DATA/'dispatcher_state.json'),alerts=health_check(settings,now)+memory_alerts+capacity_check(settings,now),now=now)
        status=dict(decision,checked_at=now.isoformat(),eligible_posts=queue['eligible_posts'],dry_run=args.dry_run)
        runtime=read_json(DATA/'reviewer_runtime.json')
        if runtime:
            status['last_reviewer_status']=runtime.get('status')
            status['last_reviewer_reason']=runtime.get('reason')
        if decision['launch'] and not args.dry_run:
            status.update(launch=False,reason='interactive_reviewer_requested')
        write_json(DATA/'dispatcher_status.json',status)
        print(json.dumps(status),flush=True)
    # Release the shared lock BEFORE the interactive task can start and acquire it.
    if not decision['launch'] or args.dry_run:return
    executable=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32'/'schtasks.exe'
    result=subprocess.run([str(executable),'/Run','/TN',args.reviewer_task],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    # A launch request is not a completed review or even proof of process startup.
    # Without an active user token the queue remains due for the next cheap wake.
    write_json(DATA/'handoff_status.json',{'requested_at':now.isoformat(),'task':args.reviewer_task,
        'request_exit_code':result.returncode,'reason':'requested' if result.returncode==0 else 'reviewer_not_started'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', help='Preferred CLI path; stale app bundle paths fall back to current installation.')
    parser.add_argument('--dry-run', action='store_true', help='Evaluate the gate; never launch AI or record a job.')
    parser.add_argument('--force', action='store_true', help='Manually bypass threshold/backoff once.')
    parser.add_argument('--interactive-reviewer',action='store_true',help='Internal signed-in worker entry point.')
    parser.add_argument('--reviewer-task',default='Discord Translator Maintenance Reviewer')
    args = parser.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    if args.interactive_reviewer and session_zero():
        raise RuntimeError('The reviewer task must use an Interactive Windows principal, not S4U.')
    if session_zero():return request_interactive_review(args)
    with dispatch_lock(DATA/'dispatcher.lock') as acquired:
        if not acquired:
            print(json.dumps({'launch':False,'reason':'reviewer_already_running'})); return
        settings = Settings.load()
        queue = load_queue(settings,force=args.force)
        now = datetime.now(timezone.utc)
        alerts = health_check(settings, now)
        memory,memory_alerts=memory_check(settings,now)
        alerts=alerts+memory_alerts+capacity_check(settings,now)
        state_path = DATA/'dispatcher_state.json'
        state = read_json(state_path)
        decision = dispatch_decision(queue,state,alerts=alerts,now=now,force=args.force)
        write_json(DATA/'dispatcher_status.json',dict(decision,checked_at=now.isoformat(),
                    eligible_posts=queue['eligible_posts'],dry_run=args.dry_run))
        print(json.dumps(decision),flush=True)
        if not decision['launch'] or args.dry_run: return
        executable = resolve_codex(args.codex)
        if executable is None:
            write_json(DATA/'reviewer_runtime.json',dict(status='failed',reason='executable_unavailable',
                checked_at=now.isoformat(),signature=decision['signature'],completion_verified=False))
            write_json(DATA/'dispatcher_status.json',dict(decision,launch=False,reason='reviewer_executable_unavailable',
                checked_at=now.isoformat(),eligible_posts=queue['eligible_posts']))
            raise RuntimeError('Codex executable unavailable. No reviewer started.')
        run_dir = DATA/'runs'/now.strftime('%Y%m%dT%H%M%S%fZ')
        run_dir.mkdir(parents=True)
        write_json(run_dir/'queue.json',queue)
        write_json(run_dir/'health_alerts.json',alerts)
        write_json(run_dir/'memory_candidates.json',memory)
        prompt = ((ROOT/'docs'/'maintenance-reviewer.md').read_text(encoding='utf-8')
                  + '\nThis run evidence directory: '+str(run_dir)
                  + '\nRead queue.json, health_alerts.json and memory_candidates.json there. Work on at most the first ten flag groups and five captured memory candidates.')
        previous = state.setdefault('jobs',{}).get(decision['signature'],{})
        job = {'started_at':now.isoformat(),'attempts':previous.get('attempts',0)+1,
               'exit_code':None,'run_directory':str(run_dir)}
        state['jobs'][decision['signature']] = job
        # Keep a bounded dispatch ledger; review receipts themselves are never removed.
        state['jobs'] = dict(list(state['jobs'].items())[-100:])
        write_json(state_path,state)
        write_json(DATA/'reviewer_runtime.json',dict(status='running',reason='reviewer_started',
            checked_at=now.isoformat(),signature=decision['signature'],executable=str(executable),
            run_directory=str(run_dir),completion_verified=False))
        command = [str(executable),'exec','--ephemeral','--sandbox','workspace-write',
                   '-c','approval_policy="never"','-c','sandbox_workspace_write.network_access=true',
                   '--cd',str(ROOT),'--color','never','--json',
                   '--output-last-message',str(run_dir/'report.txt'),'-']
        try:
            with (run_dir/'events.jsonl').open('w',encoding='utf-8') as events, (run_dir/'stderr.log').open('w',encoding='utf-8') as errors:
                result = subprocess.run(command,input=prompt,text=True,encoding='utf-8',cwd=ROOT,
                                        stdout=events,stderr=errors,check=False,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except OSError:
            job.update(exit_code=1,completion_verified=False,completion_reason='reviewer_process_start_failed',
                       finished_at=datetime.now(timezone.utc).isoformat())
            write_json(state_path,state)
            write_json(DATA/'reviewer_runtime.json',dict(job,status='failed',reason='reviewer_process_start_failed',signature=decision['signature']))
            raise RuntimeError('Reviewer process could not start; diagnostics retained without secret environment details.') from None
        completed,reason=reviewer_completion(run_dir,queue,read_json(DATA/'review_attempts.json'),now)
        job.update(exit_code=result.returncode or (0 if completed else 1),codex_exit_code=result.returncode,
                   completion_verified=bool(result.returncode==0 and completed),completion_reason=reason,
                   finished_at=datetime.now(timezone.utc).isoformat())
        write_json(state_path,state)
        write_json(DATA/'reviewer_runtime.json',dict(job,status='completed' if job['completion_verified'] else 'failed',
            reason=reason,signature=decision['signature'],executable=str(executable)))
        write_json(DATA/'dispatcher_status.json',dict(decision,launch=False,
            reason='review_completed' if job['completion_verified'] else 'review_failed',
            checked_at=job['finished_at'],eligible_posts=queue['eligible_posts'],completion_verified=job['completion_verified']))
        if job['exit_code']:
            raise RuntimeError('Reviewer failed; private diagnostics saved. Retry is code-gated with backoff.')


if __name__ == '__main__':
    main()
