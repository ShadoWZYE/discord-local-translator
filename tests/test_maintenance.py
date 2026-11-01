from datetime import datetime, timezone
import unittest
from translator_bot.maintenance import fingerprint, review_queue, PRIORITY_GUILD


class MaintenanceTests(unittest.TestCase):
    now = datetime(2026, 10, 19, 20, tzinfo=timezone.utc)

    def row(self, ident, post=None, guild=2, created='2026-10-19 19:00:00'):
        return {'id':ident, 'guild_id':guild, 'channel_id':5,
                'translation_message_id':post or ident, 'created_at':created,
                'source_text':'Example', 'translated_text':'Example output',
                'corrected_text':None, 'status':'open'}

    def queue(self, rows, attempts=None, **kwargs):
        return review_queue(rows, attempts or {}, now=self.now, **kwargs)

    def test_three_distinct_posts_not_three_reporters(self):
        self.assertFalse(self.queue([self.row(i, post=7) for i in (1,2,3)])['due'])
        self.assertTrue(self.queue([self.row(i) for i in (1,2,3)])['due'])

    def test_small_queue_waits_but_not_forever(self):
        self.assertFalse(self.queue([self.row(1)])['due'])
        self.assertEqual(self.queue([self.row(1, created='2026-10-18 19:00:00')])['reason'], 'oldest_over_24h')

    def test_attempt_suppression_and_changed_report(self):
        row=self.row(1, created='2026-10-18 19:00:00')
        attempts={'1':{'fingerprint':fingerprint(row)}}
        self.assertFalse(self.queue([row],attempts)['due'])
        row['source_text']='Changed source'
        self.assertTrue(self.queue([row],attempts)['due'])

    def test_priority_and_terminal_reports(self):
        rows=[self.row(1), self.row(2), self.row(3,guild=PRIORITY_GUILD)]
        self.assertEqual(self.queue(rows)['groups'][0]['report_ids'], [3])
        rows[0]['status']='applied'
        self.assertFalse(self.queue(rows)['due'])

    def test_force_rechecks_and_empty_queue(self):
        row=self.row(1)
        self.assertTrue(self.queue([row],{'1':{'fingerprint':fingerprint(row)}},force=True)['due'])
        self.assertFalse(self.queue([],force=True)['due'])
