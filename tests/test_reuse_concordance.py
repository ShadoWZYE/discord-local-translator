"""Synthetic agreement is a review signal, never an approval."""
from datetime import datetime,timedelta,timezone
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from translator_bot.reuse_concordance import concordance,review_candidates
from translator_bot.result_cache import ResultCache

SPEC=importlib.util.spec_from_file_location('concordance_runner',Path(__file__).resolve().parents[1]/'scripts/run_maintenance.py')
RUNNER=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(RUNNER)
NOW=datetime(2026,10,24,13,tzinfo=timezone.utc)


class ConcordanceTests(unittest.TestCase):
    def rows(self,targets=('Why?','Why?','Why?'),source='Почему?'):
        return [dict(source_id=i,source_text=source,translated_text=value,source_language='ru',target_language='en') for i,value in enumerate(targets)]

    def test_agreement_is_distinct_source_and_never_approval(self):
        rows=self.rows();rows+=rows
        candidate=concordance(rows)[0]
        self.assertEqual(candidate['dominant_support'],3)
        self.assertEqual(candidate['agreement_percent'],100)
        self.assertEqual(candidate['decision'],'unreviewed_candidate')
        self.assertTrue(candidate['eligible_for_review'])

    def test_conflicting_output_alignment_and_flags_fail_nomination(self):
        c=concordance(self.rows(('Why?','Why?','What?')))[0]
        self.assertEqual(c['agreement_percent'],66.67);self.assertFalse(c['eligible_for_review'])
        self.assertFalse(concordance(self.rows(),flagged=[1])[0]['eligible_for_review'])
        rows=self.rows();rows[0]['source_text']='Почему?\nПочему?';rows[0]['translated_text']='Why?\nWhat?'
        self.assertEqual(concordance(rows)[0]['ambiguous_alignment_sources'],1)

    def test_line_counts_are_not_guessed_and_language_directions_differ(self):
        rows=self.rows();rows[0]['translated_text']='Why?\nAdditional line.'
        c=concordance(rows)[0];self.assertEqual(c['distinct_sources'],2)
        rows.append(dict(rows[1],source_language='en',target_language='ru'))
        self.assertEqual(len(concordance(rows)),1)

    def test_daily_budget_already_approved_and_retired_scopes(self):
        report={'candidates':[]}
        for i in range(7):
            report['candidates']+=concordance(self.rows(source='слово '+('а'* (i+1))))
        self.assertEqual(len(review_candidates(report,{},now=NOW)),5)
        candidate=report['candidates'][0]
        attempts={candidate['fingerprint']:dict(attempted_at=NOW.isoformat(),outcome='deferred',scope_fingerprint=candidate['scope_fingerprint'])}
        candidate['fingerprint']='changed statistics'
        selected=review_candidates(report,attempts,now=NOW)
        self.assertEqual(len(selected),4);self.assertNotIn(candidate,selected)
        approved={(v['source'],v['target'],v['source_text']) for v in report['candidates']}
        self.assertFalse(review_candidates(report,{},approved,now=NOW))

    def test_completion_requires_fresh_memory_attempts_even_without_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);run=root/'run';run.mkdir()
            (run/'events.jsonl').write_text(json.dumps({'type':'item.completed','item':dict(type='command_execution',status='completed',exit_code=0)}))
            (run/'memory_candidates.json').write_text(json.dumps([{'fingerprint':'abc'}]))
            queue={'due':False,'groups':[]}
            with patch.object(RUNNER,'DATA',root):
                self.assertFalse(RUNNER.reviewer_completion(run,queue,{},NOW)[0])
                (root/'memory_review_attempts.json').write_text(json.dumps({'abc':dict(fingerprint='abc',outcome='deferred',attempted_at=NOW.isoformat())}))
                self.assertTrue(RUNNER.reviewer_completion(run,queue,{},NOW)[0])

    def test_persistent_default_capacity_is_100k_and_existing_rows_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'cache.sqlite3';cache=ResultCache(path)
            self.assertEqual(cache.limit,100000)
            cache.put(b'unchanged','retained',0)
            reopened=ResultCache(path)
            self.assertEqual(reopened.get(b'unchanged')[0],'retained')
            self.assertEqual(reopened.limit,100000)


if __name__=='__main__':unittest.main()
