import copy
import json
import os
import pathlib
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
import monthly_changes as monthly
from openai_budget import BudgetUnavailable, atomic_json
from source_verification import fingerprint

PREPARE = datetime(2026, 10, 31, 13, tzinfo=timezone.utc)
PUBLISH = datetime(2026, 10, 31, 14, tzinfo=timezone.utc)
CARRY = datetime(2026, 11, 1, 0, tzinfo=timezone.utc)
URL = 'https://rta.ae/news/new-rule'
NOTICE = {'title': 'New transport rule', 'text': 'The confirmed UAE rule takes effect on 1 November 2026.',
          'url': URL, 'published_at': '2026-10-20T00:00:00+00:00',
          'retrieved_at': PREPARE.isoformat()}


def approval(source, post, storage, target):
    return {'status': 'approved', 'provider': 'openai', 'source_hash': fingerprint(source),
            'summary_hash': fingerprint(post)}


class MonthlyChangesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = pathlib.Path(self.temp.name)
        self.path = self.storage / 'monthly_changes.json'
        self.env = patch.dict(os.environ, {'GITHUB_ACTIONS': 'true', 'GITHUB_REF': 'refs/heads/main',
                                          'GITHUB_EVENT_NAME': 'schedule'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        # Retain actual local serialization while replacing all git/network/AI effects.
        self.persist = patch.object(monthly, 'persist', side_effect=atomic_json).start()
        self.discover = patch.object(monthly, 'discover', return_value=[URL]).start()
        self.collect = patch.object(monthly, 'collect_sources', return_value=[copy.deepcopy(NOTICE)]).start()
        self.writer = patch.object(monthly, 'prepare_post', return_value={
            'status': 'prepared', 'post': 'С 1 ноября 2026 года в ОАЭ действует новое правило.'}).start()
        self.review = patch.object(monthly, 'review', side_effect=approval).start()
        self.send = patch.object(monthly, 'send_post', return_value={'status': 'published', 'message_id': 42}).start()
        self.addCleanup(patch.stopall)

    def read(self):
        return json.loads(self.path.read_text())

    def write_record(self, record):
        atomic_json(self.path, {'version': 1, 'months': {'2026-11': record}})

    def prepare(self):
        return monthly.prepare(self.storage, PREPARE, execute=True)

    def test_changed_source_before_send_cannot_inherit_approval(self):
        self.assertEqual(self.prepare()['status'], 'ready')
        changed = copy.deepcopy(NOTICE)
        changed['text'] += ' The effective date has been postponed.'
        self.collect.return_value = [changed]
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'],
                         'evidence_changed_or_unavailable')
        self.send.assert_not_called()

    def test_calendar_last_day_leap_year_rollover_and_hour_boundaries(self):
        for stamp, target in [('2026-02-28T13:00:00+00:00', date(2026, 3, 1)),
                              ('2028-02-29T13:00:00+00:00', date(2028, 3, 1)),
                              ('2026-12-31T13:00:00+00:00', date(2027, 1, 1)),
                              ('2026-04-30T13:00:00+00:00', date(2026, 5, 1))]:
            with self.subTest(stamp=stamp):
                self.assertEqual(monthly.target_month(datetime.fromisoformat(stamp)), target)
        for stamp in ['2026-02-27T12:00:00+00:00', '2028-02-28T12:00:00+00:00',
                      '2026-10-30T23:59:00+00:00', '2026-11-01T23:59:00+00:00']:
            self.assertIsNone(monthly.target_month(datetime.fromisoformat(stamp)))
        self.assertIsNone(monthly.target_month(PUBLISH.replace(hour=13, minute=59), publish=True))
        self.assertEqual(monthly.target_month(PUBLISH, publish=True), date(2026, 11, 1))
        with self.assertRaises(ValueError):
            monthly.target_month(datetime(2026, 10, 31))

    def test_first_day_only_after_utc_reset_and_never_second_day(self):
        self.assertIsNone(monthly.target_month(datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)))
        self.assertEqual(monthly.target_month(CARRY), date(2026, 11, 1))
        self.assertEqual(monthly.target_month(CARRY, publish=True), date(2026, 11, 1))
        self.assertIsNone(monthly.target_month(datetime(2026, 11, 1, 20, tzinfo=timezone.utc)))

    def test_dry_run_and_nonproduction_guards_have_no_side_effects(self):
        for function, now in [(monthly.prepare, PREPARE), (monthly.publish, PUBLISH)]:
            self.assertEqual(function(self.storage, now)['status'], 'dry_run')
            for override in [{'GITHUB_ACTIONS': 'false'}, {'GITHUB_REF': 'refs/heads/test'},
                             {'GITHUB_EVENT_NAME': 'push'}]:
                with patch.dict(os.environ, override):
                    self.assertEqual(function(self.storage, now, execute=True)['status'], 'dry_run')
        self.persist.assert_not_called()
        self.discover.assert_not_called()
        self.writer.assert_not_called()
        self.send.assert_not_called()

    def test_not_due_has_no_side_effects(self):
        now = datetime(2026, 10, 27, 14, tzinfo=timezone.utc)
        self.assertEqual(monthly.prepare(self.storage, now, execute=True)['status'], 'not_due')
        self.assertEqual(monthly.publish(self.storage, now, execute=True)['status'], 'not_due')
        self.persist.assert_not_called()

    def test_preparation_then_one_publication_with_primary_link(self):
        self.assertEqual(self.prepare()['status'], 'ready')
        self.writer.assert_called_once()
        self.assertEqual(self.writer.call_args.kwargs, {'kind': 'monthly', 'target_date': '2026-11-01'})
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'published')
        text = self.send.call_args.args[0]
        self.assertIn('Что нового с 1 ноября 2026', text)
        self.assertIn(URL, text)
        self.assertIn('#СПервогоЧисла', text)
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'not_ready')
        self.assertEqual(monthly.publish(self.storage, CARRY, execute=True)['status'], 'not_ready')
        self.assertEqual(self.prepare()['status'], 'already_attempted')
        self.send.assert_called_once()
        self.discover.assert_called_once()

    def test_no_downloaded_notice_produces_no_filler(self):
        self.collect.return_value = []
        self.assertEqual(self.prepare()['status'], 'no_confirmed_changes')
        self.writer.assert_not_called()
        self.review.assert_not_called()
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'not_ready')
        self.send.assert_not_called()

    def test_rejected_empty_or_oversized_writer_never_reaches_verifier_or_send(self):
        for written, status in [({'status': 'rejected', 'post': ''}, 'preparation_rejected'),
                                ({'status': 'prepared', 'post': '  '}, 'invalid_post'),
                                ({'status': 'prepared', 'post': 'a' * 801}, 'invalid_post')]:
            with self.subTest(written=written):
                self.path.unlink(missing_ok=True)
                self.writer.return_value = written
                self.assertEqual(self.prepare()['status'], status)
        self.review.assert_not_called()
        self.send.assert_not_called()

    def test_failed_verifier_blocks_publication_and_no_first_day_retry(self):
        self.review.side_effect = None
        self.review.return_value = {'status': 'rejected', 'reason': 'Wrong effective date'}
        self.assertEqual(self.prepare()['status'], 'verification_rejected')
        self.assertEqual(monthly.prepare(self.storage, CARRY, execute=True)['status'], 'already_attempted')
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'not_ready')
        self.send.assert_not_called()

    def test_search_limit_deferral_resumes_once_after_reset(self):
        self.discover.side_effect = [BudgetUnavailable('Monthly OpenAI budget reached'), [URL]]
        self.assertEqual(self.prepare()['status'], 'budget_deferred')
        self.assertEqual(self.prepare()['status'], 'already_attempted')
        self.assertEqual(monthly.prepare(self.storage, CARRY, execute=True)['status'], 'ready')
        self.assertTrue(self.read()['months']['2026-11']['carryover_used'])
        self.assertEqual(monthly.prepare(self.storage, CARRY, execute=True)['status'], 'already_attempted')
        self.assertEqual(self.discover.call_count, 2)
        self.assertEqual(monthly.publish(self.storage, CARRY, execute=True)['status'], 'published')

    def test_cached_urls_are_refetched_not_searched_on_writer_limit_carryover(self):
        self.writer.side_effect = [{'status': 'deferred', 'post': '', 'budget_limited': True},
                                   {'status': 'prepared', 'post': 'Подтвержденное изменение с 1 ноября 2026.'}]
        self.assertEqual(self.prepare()['status'], 'budget_deferred')
        self.assertEqual(monthly.prepare(self.storage, CARRY, execute=True)['status'], 'ready')
        self.discover.assert_called_once()
        self.assertEqual(self.collect.call_args_list[0].args, ([URL], PREPARE))
        self.assertEqual(self.collect.call_args_list[1].args, ([URL], CARRY))
        self.assertEqual(self.writer.call_count, 2)

    def test_verifier_limit_deferral_and_second_budget_failure_never_retry_again(self):
        self.review.side_effect = None
        self.review.return_value = {'status': 'deferred', 'reason': 'Daily OpenAI call limit reached'}
        self.assertEqual(self.prepare()['status'], 'budget_deferred')
        self.assertEqual(monthly.prepare(self.storage, CARRY, execute=True)['status'], 'budget_deferred')
        self.assertEqual(monthly.prepare(self.storage, CARRY.replace(hour=1), execute=True)['status'], 'already_attempted')
        self.assertEqual(self.review.call_count, 2)
        self.assertEqual(monthly.prepare(self.storage, CARRY.replace(day=2), execute=True)['status'], 'not_due')

    def test_first_day_cannot_invent_unattempted_issue(self):
        self.assertEqual(monthly.prepare(self.storage, CARRY, execute=True)['status'], 'no_pending_issue')
        self.discover.assert_not_called()
        self.persist.assert_not_called()

    def test_nonlimit_budget_failure_is_not_carried_over(self):
        self.discover.side_effect = BudgetUnavailable('Budget ledger missing or corrupt; refusing to reset it')
        self.assertEqual(self.prepare()['status'], 'preparation_failed')
        self.assertEqual(monthly.prepare(self.storage, CARRY, execute=True)['status'], 'already_attempted')
        self.discover.assert_called_once()

    def test_preparation_marker_failure_prevents_search(self):
        self.persist.side_effect = OSError('push failed')
        with self.assertRaises(OSError):
            self.prepare()
        self.discover.assert_not_called()

    def test_send_marker_is_written_before_request(self):
        self.prepare()
        def send(text):
            self.assertEqual(self.read()['months']['2026-11']['status'], 'sending')
            return {'status': 'published', 'message_id': 42}
        self.send.side_effect = send
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'published')

    def test_persist_failure_before_send_prevents_side_effect(self):
        self.prepare()
        self.persist.side_effect = OSError('push failed')
        with self.assertRaises(OSError):
            monthly.publish(self.storage, PUBLISH, execute=True)
        self.send.assert_not_called()

    def test_ambiguous_sending_and_failed_receipts_are_not_retried(self):
        self.prepare()
        record = copy.deepcopy(self.read()['months']['2026-11'])
        for status in ['sending', 'send_unknown', 'send_failed', 'published']:
            with self.subTest(status=status):
                self.write_record({**record, 'status': status})
                self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'not_ready')
                self.assertEqual(monthly.publish(self.storage, CARRY, execute=True)['status'], 'not_ready')
        self.send.assert_not_called()

    def test_unknown_send_result_persisted_and_never_resent(self):
        self.prepare()
        self.send.return_value = {'status': 'send_unknown'}
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'send_unknown')
        self.assertEqual(self.read()['months']['2026-11']['status'], 'send_unknown')
        self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'not_ready')
        self.send.assert_called_once()

    def test_tampered_post_source_provenance_provider_target_or_report_blocks_send(self):
        self.prepare()
        original = copy.deepcopy(self.read()['months']['2026-11'])
        mutations = [lambda r: r.update(post_html=r['post_html'] + ' New claim'),
                     lambda r: r['source_snapshot'].update(text='Changed evidence'),
                     lambda r: r['verification'].update(provider='other'),
                     lambda r: r['verification'].update(status='rejected'),
                     lambda r: r['verification'].update(summary_hash='wrong'),
                     lambda r: r.update(target='2026-12-01'),
                     lambda r: r['notices'][0].update(url='https://evil.com/fake'),
                     lambda r: r.update(notices=[])]
        for mutate in mutations:
            record = copy.deepcopy(original)
            mutate(record)
            self.write_record(record)
            self.assertEqual(monthly.publish(self.storage, PUBLISH, execute=True)['status'], 'verification_invalid')
        self.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
