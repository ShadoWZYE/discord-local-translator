import asyncio
import json
import unittest
from unittest.mock import Mock
from translator_bot.priority import PrioritySlot
from translator_bot.translation import LocalTranslator
from translator_bot.edit_translation import edit_window


class PriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_guild_jumps_queued_work_without_preempting_owner(self):
        gate=PrioritySlot();order=[]
        await gate.acquire()
        async def work(name,priority):
            async with gate.slot(priority):order.append(name)
        tasks=[asyncio.create_task(work('other-first',1)),asyncio.create_task(work('ss-first',0)),
               asyncio.create_task(work('ss-second',0)),asyncio.create_task(work('other-last',1))]
        await asyncio.sleep(0)
        self.assertFalse(order)
        gate.release();await asyncio.gather(*tasks)
        self.assertEqual(order,['ss-first','ss-second','other-first','other-last'])
        self.assertFalse(gate.locked())

    async def test_cancelled_waiter_and_cancelled_handoff_do_not_leak_slot(self):
        gate=PrioritySlot();await gate.acquire()
        waiting=asyncio.create_task(gate.acquire(0));await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiting
        gate.release();self.assertFalse(gate.locked())
        await gate.acquire()
        waiting=asyncio.create_task(gate.acquire(0));await asyncio.sleep(0)
        gate.release();waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):await waiting
        self.assertFalse(gate.locked())


class IncrementalEditTests(unittest.TestCase):
    def setUp(self):
        self.t=LocalTranslator('http://localhost:11434')
        self.source='\n'.join(f'Entry {i}: please keep this section ready until the event starts at time 10.' for i in range(12))
        self.output='\n'.join(f'Строка {i}: пожалуйста, держите этот раздел готовым до начала события в 10.' for i in range(12))
        self.snapshot=dict(source_text=self.source,source_language='en',target_language='ru',
                           translated_text=self.output,policy=self.t.policy_fingerprint())

    def test_only_changed_contextual_block_is_generated(self):
        source=self.source.replace('Entry 5: please','Entry 5: kindly')
        lines=self.output.split('\n');lines[5]=lines[5].replace('пожалуйста','будьте добры')
        self.t._request=Mock(return_value=json.dumps({f'part_{i}':line for i,line in enumerate(lines[4:7])}))
        result=self.t.translate_edit(self.snapshot,source,'en','ru')
        self.assertIsNotNone(result)
        self.assertEqual(result.text,'\n'.join(lines))
        self.t._request.assert_called_once()
        prompt=self.t._request.call_args.args[0]
        self.assertIn('Entry 5: kindly',prompt)
        self.assertNotIn('Entry 0:',prompt)

    def test_unsafe_alignment_policy_or_participant_change_falls_back(self):
        self.assertIsNone(edit_window(self.source,self.source.replace('Entry 5:','Alice 5:'),self.output))
        self.assertIsNone(edit_window(self.source,self.source.replace('\n',' '),self.output))
        self.assertIsNone(edit_window(self.source,self.source.replace('Entry 5:','Entry 5: edited'), 'wrong alignment'))
        self.snapshot['policy']='old-model-policy'
        self.t._request=Mock(side_effect=AssertionError('No partial generation'))
        self.assertIsNone(self.t.translate_edit(self.snapshot,self.source,'en','ru'))

    def test_unchanged_snapshot_needs_no_model(self):
        self.t._request=Mock(side_effect=AssertionError('No generation'))
        self.assertEqual(self.t.translate_edit(self.snapshot,self.source,'en','ru').text,self.output)

    def test_independent_roster_rows_can_allow_name_changes_but_default_cannot(self):
        new=self.source.replace('Entry 5:','Alice 5:')
        self.assertIsNotNone(edit_window(self.source,new,self.output,independent_rows=True))
        self.assertIsNone(edit_window(self.source,new,self.output))
