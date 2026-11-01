import copy
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from translator_bot.review_evidence import CHECKS,text_hash,validate_evidence,save_approval,load_approval
from translator_bot.translation import LocalTranslator,TranslationResult


class ReviewEvidenceTests(unittest.TestCase):
    def record(self):
        source='My neck hurts.';candidate='У меня болит шея.'
        return source,candidate,{'decision':'confirmed_error','source_sha256':text_hash(source),
            'candidate_sha256':text_hash(candidate),'candidate_text':candidate,'source_message_ids':[1],
            'context':[],'clauses':[{'source_span':source,'candidate_span':candidate,
                'finding':'Original output assigned the body part to the addressee.',
                'change_reason':'Restore explicit first-person body ownership from My.'}],
            'checks':{key:'Checked against the source; no additional change needed.' for key in CHECKS},
            'uncertainty':'None for the corrected ownership.',
            'reference_check':{'kind':'source_first_alignment','independent':False,
                'finding':'Direct explicit My; no extra model needed to establish ownership.'}}

    def test_approval_matches_exact_text_and_persists_with_history(self):
        source,candidate,record=self.record()
        with closing(sqlite3.connect(':memory:')) as db:
            with self.assertRaises(ValueError):load_approval(db,1,source,candidate)
            save_approval(db,1,source,candidate,record)
            self.assertEqual(load_approval(db,1,source,candidate),record)
            with self.assertRaises(ValueError):load_approval(db,1,source+' Edited',candidate)
            with self.assertRaises(ValueError):load_approval(db,1,source,candidate+' Changed')
            save_approval(db,1,source,candidate,record)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM feedback_review_evidence').fetchone()[0],2)

    def test_fluency_and_technical_pass_are_not_linguistic_approval(self):
        source,candidate,record=self.record()
        for change in ({'decision':'acceptable_variant'},{'clauses':[]},{'checks':dict.fromkeys(CHECKS,'pass')},
                       {'source_sha256':'stale'},{'reference_check':{'kind':'model','finding':'Looks good'}}):
            value=copy.deepcopy(record);value.update(change)
            with self.assertRaises(ValueError):validate_evidence(source,candidate,value)

    def test_grammatically_complete_sentence_does_not_erase_word_sense_context(self):
        t=LocalTranslator('http://localhost')
        outputs=iter(('Я принесу биту.','Я принесу летучую мышь.'))
        t._translate_uncached=Mock(side_effect=lambda *args:TranslationResult('en','ru',next(outputs)))
        actual=[]
        for context in ('We are discussing baseball equipment.','We are discussing a rescued flying animal.'):
            with t.conversation_context(json.dumps({'originals':[{'text':context}]})):
                actual.append(t.translate_to('I will bring a bat.','ru','en').text)
        self.assertEqual(actual,['Я принесу биту.','Я принесу летучую мышь.'])
        self.assertEqual(t._translate_uncached.call_count,2)

    def test_parent_fragments_cannot_transfer_between_different_passages(self):
        t=LocalTranslator('http://localhost')
        source='I will bring a bat.'
        _,token=t._get_fragment(source,'en','ru',parent='Baseball equipment.\n'+source)
        t._put_fragment(token,'Я принесу биту.')
        self.assertIsNone(t._get_fragment(source,'en','ru',parent='Rescued animals.\n'+source)[0])
        self.assertEqual(t._get_fragment(source,'en','ru',parent='Baseball equipment.\n'+source)[0],'Я принесу биту.')

    def test_human_review_does_not_make_ambiguous_prose_globally_reusable(self):
        t=LocalTranslator('http://localhost')
        self.assertEqual(t.remember_reviewed('I will bring a bat.','en','ru','Я принесу биту.'),0)
        self.assertIsNone(t.cached_translation('I will bring a bat.','en','ru'))


if __name__=='__main__':unittest.main()
