import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import retro_stage as stage
from retro_commons import validate_metadata

CHANNEL='-1001234567890'
ENV={'GITHUB_ACTIONS':'true','GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'workflow_dispatch',
     'GITHUB_WORKFLOW':'Test bot in private channel','GITHUB_RUN_ATTEMPT':'1',
     'BOT_ENVIRONMENT':'staging','STAGING_OPERATION':'retro','SOURCE_VERIFIER':'openai',
     'TELEGRAM_BOT_TOKEN':'fixture-test-secret','TELEGRAM_CHANNEL_ID':CHANNEL,'PRODUCTION_CHANNEL_ID':'@production'}


class ExistingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.item=json.loads((ROOT/'config/retro_beach_test.json').read_text(encoding='utf-8'))[0]
        self.data=json.loads((ROOT/'tests/fixtures/commons_beach_api.json').read_text(encoding='utf-8'))
        self.item['usage_review'].update(status='approved',channel_id=CHANNEL,telegram_republication=True)

    def test_only_existing_first_manual_retro_workflow_context_allowed(self):
        with patch.dict(os.environ,ENV):
            self.assertTrue(stage.existing_actions_context())
            for key,value in [('GITHUB_RUN_ATTEMPT','2'),('GITHUB_REF','refs/heads/develop'),
                              ('GITHUB_WORKFLOW','another'),('STAGING_OPERATION','publish'),
                              ('GITHUB_EVENT_NAME','schedule')]:
                with patch.dict(os.environ,{key:value}):
                    self.assertFalse(stage.existing_actions_context())

    def test_persistent_marker_scrubs_nested_private_destination(self):
        state={'slots':{'staging:2026-W41':{'payload':{'chat_id':CHANNEL,'caption':'test'},
                    'usage_review':{'channel_id':CHANNEL},'status':'sending'}}}
        with patch.dict(os.environ,ENV),patch('retro_stage.persist') as persist:
            stage.save_state(Path('test.json'),state)
        sanitized=persist.call_args.args[1]
        self.assertNotIn(CHANNEL,json.dumps(sanitized))
        self.assertEqual(sanitized['slots']['staging:2026-W41']['status'],'sending')
        self.assertEqual(state['slots']['staging:2026-W41']['payload']['chat_id'],CHANNEL)

    def test_one_full_caption_verification_one_send_and_no_second_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            storage,production=Path(temp)/'staging',Path(temp)/'production'
            production.mkdir()
            def send(payload):
                saved=json.loads((storage/'retro_photo_publications.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['slots']['staging:2026-W41']['status'],'sending')
                self.assertNotIn(CHANNEL,json.dumps(saved))
                return {'status':'published','message_id':987}
            with patch.dict(os.environ,ENV),patch('retro_stage.persist',side_effect=stage.atomic_json),patch('retro_publish.load_evidence',return_value=validate_metadata(self.item,self.data)),patch('retro_stage.verify_summary',return_value={'status':'approved','api_calls':1}) as verifier,patch('retro_stage.send_photo',side_effect=send) as sender:
                args=dict(storage=storage,production=production,send=True,now=datetime(2026,10,8,tzinfo=timezone.utc))
                self.assertEqual(stage.run([self.item],CHANNEL,**args)['status'],'published')
                self.assertEqual(stage.run([self.item],CHANNEL,**args)['previous_status'],'published')
                verifier.assert_called_once()
                self.assertEqual(verifier.call_args.args[1],stage.caption_html(self.item))
                sender.assert_called_once()

    def test_failed_remote_marker_prevents_paid_call_and_send(self):
        with tempfile.TemporaryDirectory() as temp:
            storage,production=Path(temp)/'staging',Path(temp)/'production'
            production.mkdir()
            with patch.dict(os.environ,ENV),patch('retro_stage.persist',side_effect=RuntimeError('push failure')),patch('retro_publish.load_evidence',return_value=validate_metadata(self.item,self.data)),patch('retro_stage.verify_summary') as verifier,patch('retro_stage.send_photo') as sender:
                with self.assertRaises(RuntimeError):
                    stage.run([self.item],CHANNEL,storage=storage,production=production,send=True)
                verifier.assert_not_called()
                sender.assert_not_called()

    def test_existing_workflow_adapter_validates_channel_and_binds_approved_package(self):
        module=Mock()
        spec=Mock()
        with patch.dict(os.environ,ENV),patch('importlib.util.spec_from_file_location',return_value=spec),patch('importlib.util.module_from_spec',return_value=module),patch('retro_stage.run',return_value={'status':'published'}) as run:
            self.assertEqual(stage.run_existing_workflow(Path('storage/dubai_news_staging'))['status'],'published')
            module.validate_channel.assert_called_once()
            choices,channel=run.call_args.args
            self.assertEqual(channel,CHANNEL)
            self.assertEqual(choices[0]['usage_review']['channel_id'],CHANNEL)
            self.assertTrue(run.call_args.kwargs['send'])

    def test_wrong_destination_prevents_adapter_spending_or_sending(self):
        module=Mock()
        module.validate_channel.side_effect=RuntimeError('invalid destination')
        with patch.dict(os.environ,ENV),patch('importlib.util.spec_from_file_location',return_value=Mock()),patch('importlib.util.module_from_spec',return_value=module),patch('retro_stage.run') as run:
            with self.assertRaises(RuntimeError): stage.run_existing_workflow(Path('test'))
            run.assert_not_called()
