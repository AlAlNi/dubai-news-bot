import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from retro_commons import commons_review, validate_metadata, load_commons_evidence
from retro_policy import source_allowed, usage_allowed
from retro_photos import candidates
from retro_publish import select_photo, image_identity, load_evidence
from retro_stage import photo_payload, caption_html, run


class CommonsTests(unittest.TestCase):
    def setUp(self):
        self.item = json.loads((ROOT/'config/retro_beach_draft.json').read_text(encoding='utf-8'))[0]
        self.data = json.loads((ROOT/'tests/fixtures/commons_beach_api.json').read_text(encoding='utf-8'))
        self.channel = '-1001234567890'
        self.item['usage_review'].update(status='approved', channel_id=self.channel, telegram_republication=True)

    def test_only_exact_manually_reviewed_card_admitted_for_staging(self):
        self.assertFalse(source_allowed(self.item['source_url']))
        self.assertTrue(source_allowed(self.item['source_url'], allow_commons=True))
        for url in ['https://commons.wikimedia.org/wiki/File:Other.jpg',
                    'https://commons.wikimedia.org/wiki/Main_Page',
                    self.item['source_url']+'?other=1',
                    self.item['source_url'].replace('commons.wikimedia.org','commons.wikimedia.org.evil.com')]:
            self.assertFalse(source_allowed(url, allow_commons=True))

    def test_discovery_marks_reviewed_card_without_automatic_usage_approval(self):
        found = candidates([{'link':self.item['source_url'],'imageUrl':self.item['image_url']}],set())[0]
        self.assertEqual(found['publication_eligibility'],'commons_manual_usage_review_required')
        self.assertFalse(usage_allowed(found,self.channel))

    def test_pinned_metadata_uses_capture_date_and_preserves_uncertainty(self):
        source = validate_metadata(self.item, self.data)
        self.assertIn('1993-11-01', source['text'])
        self.assertIn("I don't know where this is", source['text'])
        self.assertIn("I'm guessing", source['text'])
        self.assertNotIn('2018-02-21 05:37:23', source['text'])
        self.assertIn('Mitch Barrie', source['text'])

    def test_license_author_date_description_credit_and_hash_changes_fail_closed(self):
        for key in ['Artist', 'DateTimeOriginal', 'ImageDescription', 'Credit', 'LicenseShortName',
                    'LicenseUrl', 'AttributionRequired', 'Restrictions']:
            data = copy.deepcopy(self.data)
            next(iter(data['query']['pages'].values()))['imageinfo'][0]['extmetadata'][key]['value'] = 'changed'
            with self.assertRaisesRegex(ValueError,'commons_metadata_changed'):
                validate_metadata(self.item,data)
        data = copy.deepcopy(self.data)
        next(iter(data['query']['pages'].values()))['imageinfo'][0]['sha1'] = 'other'
        with self.assertRaises(ValueError): validate_metadata(self.item,data)
        item = dict(self.item,image_url=self.item['image_url'].replace('.jpg','-other.jpg'))
        with self.assertRaises(ValueError): validate_metadata(item,self.data)

    def test_unknown_card_never_loaded_or_selected(self):
        item = dict(self.item,source_url='https://commons.wikimedia.org/wiki/File:Other.jpg')
        with patch('retro_publish.load_evidence') as loader:
            selected, source, diagnostics = select_photo([item],set(),allow_commons=True)
        self.assertIsNone(selected)
        loader.assert_not_called()
        with patch('retro_publish.requests.get') as get:
            with self.assertRaises(ValueError): load_evidence(self.item)
        get.assert_not_called()

    def test_real_metadata_loader_bounded_and_no_redirects(self):
        response = Mock(status_code=200,headers={'Content-Type':'application/json'})
        response.iter_content.return_value = [json.dumps(self.data).encode()]
        with patch('retro_commons.requests.get',return_value=response) as get:
            self.assertIn('1993',load_commons_evidence(self.item)['text'])
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        response.close.assert_called_once()
        response.iter_content.return_value = [b'x'*2_000_001]
        with patch('retro_commons.requests.get',return_value=response):
            with self.assertRaisesRegex(ValueError,'archive_too_large'): load_commons_evidence(self.item)

    def test_rights_require_exact_author_license_attribution_image_and_destination(self):
        self.assertTrue(usage_allowed(self.item,self.channel))
        for key,value in [('rights_holder','unknown'),('license_url','https://example.com'),
                          ('attribution','Mitch only'),('image_sha1','other'),('modifications','cropped'),
                          ('share_alike',False),('channel_id','-999')]:
            item = copy.deepcopy(self.item)
            item['usage_review'][key] = value
            self.assertFalse(usage_allowed(item,self.channel))

    def test_caption_contains_title_author_card_license_and_no_national_label(self):
        caption = photo_payload(self.item,self.channel)['caption']
        for value in ['Mitch Barrie', 'Persian Gulf', self.item['source_url'],
                      'https://creativecommons.org/licenses/by-sa/2.0/', 'Фото без изменений.', 'CC BY-SA 2.0']:
            self.assertIn(value,caption)
        self.assertNotIn('The National',caption)
        self.assertIn('лишь предполагает',caption)

    def test_commons_image_deduplicated_with_url_variants(self):
        variant = self.item['image_url'].replace('%28','(').replace('%29',')')+'?width=640'
        self.assertEqual(image_identity(variant),image_identity(self.item['image_url']))
        with patch('retro_publish.load_evidence') as load:
            item, source, report = select_photo([self.item],{image_identity(variant)},allow_commons=True)
        self.assertIsNone(item)
        self.assertEqual(report['counts'],{'already_used':1})
        load.assert_not_called()

    def test_draft_is_not_approved_to_send(self):
        item = json.loads((ROOT/'config/retro_beach_draft.json').read_text(encoding='utf-8'))[0]
        self.assertFalse(usage_allowed(item,self.channel))
        with self.assertRaises(ValueError): photo_payload(item,self.channel)
        self.assertIn('Wikimedia Commons',caption_html(item))

    def test_commons_stage_keeps_verifier_and_send_guard(self):
        with tempfile.TemporaryDirectory() as temp:
            stage,prod = Path(temp)/'stage',Path(temp)/'production'
            prod.mkdir()
            env={'GITHUB_ACTIONS':'false','BOT_ENVIRONMENT':'staging','SOURCE_VERIFIER':'openai',
                 'TEST_TELEGRAM_CHANNEL_ID':self.channel,'TEST_TELEGRAM_BOT_TOKEN':'fixture-only',
                 'TELEGRAM_CHANNEL_ID':'-999','TELEGRAM_BOT_TOKEN':'production-fixture'}
            with patch.dict(os.environ,env), patch('retro_commons.load_commons_evidence',return_value=validate_metadata(self.item,self.data)) as evidence, patch('retro_stage.verify_summary',return_value={'status':'rejected'}) as verifier, patch('retro_stage.send_photo') as sender:
                result=run([self.item],self.channel,storage=stage,production=prod,send=True,now=datetime(2026,10,8,tzinfo=timezone.utc))
                self.assertEqual(result['status'],'verification_rejected')
                sender.assert_not_called()
                verifier.assert_called_once()
                evidence.assert_called_once()
