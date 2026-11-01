import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

from translator_bot.feedback_resolution import resolve_report
from translator_bot.maintenance import fingerprint, review_queue
from translator_bot.review_evidence import CHECKS, text_hash, load_approval
from translator_bot.storage import SettingsStore


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = SettingsStore(Path(temporary.name)/'settings.sqlite3')
        self.addCleanup(self.store.close)
        self.store.add_feedback(guild_id=1,channel_id=2,source_message_id=3,
            translation_message_id=4,source_author_id=5,reporter_id=6,
            source_language='en',target_language='ru',source_text='Hello.',
            translated_text='Привет.',corrected_text=None,note=None)
        self.row = dict(self.store._connection.execute('SELECT * FROM translation_feedback').fetchone())
        self.store.link_translation(4,3,2,7,'en','ru')
        self.source = {'id':'3','content':'Hello.','author':{'bot':False}}
        self.post = {'id':'4','content':'Привет.','webhook_id':'7'}
        self.evidence = {'decision':'acceptable_variant','reason':'A direct greeting retains its meaning without any rewrite.',
            'report_fingerprint':fingerprint(self.row),'source_sha256':text_hash('Hello.'),
            'candidate_sha256':text_hash('Привет.'),'candidate_text':'Привет.',
            'source_message_ids':[3],'context':[],
            'clauses':[{'source_span':'Hello.','candidate_span':'Привет.',
                'finding':'The greeting and punctuation remain faithful.',
                'change_reason':'No change is needed for this ordinary greeting.'}],
            'checks':{k:'Source-first check preserves the explicit original meaning.' for k in CHECKS},
            'uncertainty':'No material ambiguity in the greeting.',
            'reference_check':{'kind':'source_first','independent':False,
                'finding':'Direct lexical comparison, not model fluency as proof.'}}

    def request(self, method, path, **kwargs):
        if method=='DELETE' or '/reactions/' in path:return []
        return copy.deepcopy(self.source if path.endswith('/3') else self.post)

    def test_no_change_closes_with_receipt_without_corrective_approval(self):
        request=Mock(side_effect=self.request)
        self.assertEqual(resolve_report(self.store,1,self.evidence,request,[1]),'closed_no_change')
        row=dict(self.store._connection.execute('SELECT * FROM translation_feedback').fetchone())
        self.assertEqual(row['translated_text'],'Привет.')
        self.assertIsNone(row['corrected_text'])
        self.assertEqual(review_queue([row],{})['pending_reports'],0)
        receipt=self.store._connection.execute('SELECT * FROM feedback_resolutions').fetchone()
        self.assertEqual(receipt['cleanup_verified'],1)
        with self.assertRaises(ValueError):load_approval(self.store._connection,1,'Hello.','Привет.')
        self.assertFalse(any(call.args[0]=='PATCH' for call in request.call_args_list))
        # A user may reflag after closure; old evidence remains available.
        fields={k:self.row[k] for k in ('guild_id','channel_id','source_message_id','translation_message_id',
            'source_author_id','reporter_id','source_language','target_language','source_text','translated_text','corrected_text','note')}
        self.assertTrue(self.store.add_feedback(**fields))
        self.assertEqual(self.store.open_feedback_count(1),1)
        self.assertEqual(json.loads(receipt['record_json'])['status'],'open')

    def test_stale_source_candidate_group_and_unreviewed_evidence_cannot_close(self):
        for change in ({'source_message_ids':[99]},{'candidate_text':'Пока.'},
                       {'report_fingerprint':'stale'},{'clauses':[]}, {'decision':'uncertain_source'}):
            evidence=dict(self.evidence,**change)
            with self.assertRaises(ValueError):resolve_report(self.store,1,evidence,self.request,[1])
        self.source['content']='Edited original.'
        with self.assertRaises(ValueError):resolve_report(self.store,1,self.evidence,self.request,[1])
        self.assertEqual(self.store.open_feedback_count(1),1)

    def test_obsolete_requires_actual_missing_post_and_handles_missing_original(self):
        evidence={'decision':'obsolete','reason':'The original and translation were both deleted; do not recreate.',
            'missing_translation_id':4,'report_fingerprint':fingerprint(self.row)}
        with self.assertRaises(ValueError):resolve_report(self.store,1,evidence,self.request,[1])
        self.assertEqual(resolve_report(self.store,1,evidence,lambda *a,**k:None,[1]),'obsolete')

    def test_failed_cleanup_is_durable_and_retryable(self):
        def failure(method,path,**kwargs):
            if method=='DELETE':raise RuntimeError('Permission unavailable')
            return self.request(method,path,**kwargs)
        with self.assertRaises(RuntimeError):resolve_report(self.store,1,self.evidence,failure,[1])
        receipt=self.store._connection.execute('SELECT * FROM feedback_resolutions').fetchone()
        self.assertEqual(receipt['cleanup_verified'],0)
        current=dict(self.store._connection.execute('SELECT * FROM translation_feedback').fetchone())
        self.evidence['report_fingerprint']=fingerprint(current)
        self.assertEqual(resolve_report(self.store,1,self.evidence,self.request,[1]),'closed_no_change')

    def test_old_status_schema_upgrade_preserves_rows(self):
        path=Path(self.store._connection.execute('PRAGMA database_list').fetchone()[2])
        # Use an independent old-schema fixture, never mutate a production DB.
        old=path.with_name('legacy.sqlite3')
        schema=self.store._connection.execute("SELECT sql FROM sqlite_master WHERE name='translation_feedback'").fetchone()[0]
        schema=schema.replace(", 'closed_no_change', 'obsolete'",'')
        with closing(sqlite3.connect(old)) as db, db:
            db.execute(schema)
            columns=','.join(self.row)
            db.execute(f'INSERT INTO translation_feedback ({columns}) VALUES ({",".join("?" for _ in self.row)})',tuple(self.row.values()))
        migrated=SettingsStore(old)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.open_feedback_count(1),1)
        with migrated._connection:
            migrated._connection.execute("UPDATE translation_feedback SET status='obsolete'")


if __name__=='__main__':unittest.main()
