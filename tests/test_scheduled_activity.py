import unittest
from translator_bot.translation import LocalTranslator
from translator_bot.translation_policy import cache_rules


class ScheduledActivityTests(unittest.TestCase):
    def test_temporal_activity_in_both_word_orders(self):
        translator = LocalTranslator('http://localhost')
        for source in ('Я отложил ремонт на утро.', 'Я оставила на завтра уборку.'):
            self.assertIn('SCHEDULED ACTIVITY:', translator._prompt(source, 'ru', 'en'))
            self.assertIn('scheduled-activity-v1', cache_rules(source))

    def test_stored_item_and_ordinary_scheduling_do_not_get_the_hint(self):
        translator = LocalTranslator('http://localhost')
        for source in ('Я оставил улучшение снаряжения в сундуке.',
                       'Я оставила подарок на вечер.', 'Мы поговорим на выходных.'):
            self.assertNotIn('SCHEDULED ACTIVITY:', translator._prompt(source, 'ru', 'en'))
            self.assertNotIn('scheduled-activity-v1', cache_rules(source))
