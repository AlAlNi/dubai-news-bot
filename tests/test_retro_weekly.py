import copy
import hashlib
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
import retro_weekly as weekly
from retro_photo import caption_html, photo_payload, send_photo, validate_destination, download_approved_photo
from retro_publish import image_identity, run, select_photo
from retro_policy import source_allowed, usage_allowed, discovery_eligibility
from retro_commons import commons_review, validate_metadata, load_commons_evidence
from openai_budget import atomic_json

NOW=datetime(2026,10,17,tzinfo=timezone.utc)
CHANNEL='-1001234567890'
ENV={'GITHUB_ACTIONS':'true','GITHUB_REF':'refs/heads/main','GITHUB_EVENT_NAME':'schedule',
     'GITHUB_WORKFLOW':'Publish Dubai Then','BOT_ENVIRONMENT':'production','SOURCE_VERIFIER':'openai',
     'TELEGRAM_BOT_TOKEN':'offline-fixture-secret','TELEGRAM_CHANNEL_ID':'@rudubainews'}


def fixture():
    seed=json.loads((ROOT/'config/retro_first_post.json').read_text(encoding='utf-8'))
    item={'id':'SYNTHETIC OFFLINE ONLY','source_url':'https://www.thenationalnews.com/news/uae/fixture/',
          'image_url':'https://images.example.com/offline-fixture.jpg','post_html':seed['post_html']}
    item['usage_review']={'status':'approved','basis':'written_permission','reviewer':'offline fixture',
        'reviewed_at':'2026-10-11','evidence':'SYNTHETIC TEST ONLY','rights_holder':'fixture owner',
        'attribution':'Fixture owner','source_url':item['source_url'],'image_url':item['image_url'],
        'target_username':'rudubainews','telegram_republication':True,'channel_id':None}
    item['publication_review']={'status':'approved','reviewer':'offline fixture','reviewed_at':'2026-10-11',
        'target_username':'rudubainews','visual_checked':True,'image_identity':image_identity(item['image_url']),
        'caption_sha256':hashlib.sha256(caption_html(item).encode()).hexdigest(),
        'image_sha256':hashlib.sha256(b'offline photo').hexdigest()}
    return item


class WeeklyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.storage,self.staging=self.base/'production',self.base/'staging'
        self.storage.mkdir(); self.staging.mkdir()
        self.queue=self.base/'queue.json'
        self.item=fixture()
        self.save_queue([self.item])
        self.source={'title':'Photo','text':'Dubai in the 1980s. Richard Parry.','url':self.item['source_url']}

    def save_queue(self,items):
        self.queue.write_text(json.dumps({'version':1,'candidates':items}),encoding='utf-8')

    def call(self,execute=True):
        return weekly.run_weekly(storage=self.storage,queue=self.queue,staging=self.staging,now=NOW,execute=execute)

    def record(self):
        return json.loads((self.storage/'retro_publications.json').read_text(encoding='utf-8'))['slots']['2026-W42']

    def context(self,verification=None,sender=None):
        patches=[patch.dict(os.environ,ENV),patch('retro_weekly.persist',side_effect=atomic_json),
            patch('retro_weekly.validate_destination',return_value=CHANNEL),
            patch('retro_publish.load_evidence',return_value=self.source),
            patch('retro_weekly.verify_summary',return_value=verification or {'status':'approved'}),
            patch('retro_weekly.send_photo',side_effect=sender or (lambda *a:{'status':'published','message_id':123})),
            patch('retro_weekly.download_approved_photo',return_value=b'offline photo')]
        mocks=[p.start() for p in patches]
        for p in patches:self.addCleanup(p.stop)
        return mocks

    def test_empty_queue_skips_without_discovery_credentials_network_or_spend(self):
        self.save_queue([])
        with patch('retro_weekly.verify_summary') as verify,patch('retro_weekly.validate_destination') as target,patch('retro_publish.collect') as collect,patch('retro_publish.write_post') as writer,patch('retro_weekly.send_photo') as send:
            self.assertEqual(self.call()['status'],'no_reviewed_photo')
            for mock in [verify,target,collect,writer,send]:mock.assert_not_called()

    def test_dry_run_checks_only_local_queue_no_state_writes_or_network(self):
        with patch('retro_weekly.verify_summary') as verify,patch('retro_weekly.persist') as persist,patch('retro_weekly.validate_destination') as target:
            self.assertEqual(self.call(False)['status'],'reviewed_queue_ready')
        for mock in [verify,persist,target]:mock.assert_not_called()
        self.assertFalse((self.storage/'retro_publications.json').exists())

    def test_main_entry_point_uses_reviewed_queue_not_legacy_seed_search(self):
        with patch('retro_weekly.STAGING',self.staging),patch('retro_publish.legacy_run') as legacy:
            result=run(storage=self.storage,queue=self.queue,now=NOW)
        self.assertEqual(result['status'],'reviewed_queue_ready')
        legacy.assert_not_called()

    def test_full_caption_verified_once_and_remote_marker_precedes_send(self):
        def send(payload,token,image_bytes):
            self.assertEqual(self.record()['status'],'sending')
            self.assertEqual(payload['chat_id'],CHANNEL)
            self.assertEqual(image_bytes,b'offline photo')
            return {'status':'published','message_id':123}
        mocks=self.context(sender=send)
        self.assertEqual(self.call()['status'],'published')
        self.assertEqual(self.call()['previous_status'],'published')
        mocks[4].assert_called_once()
        self.assertEqual(mocks[4].call_args.args[1],caption_html(self.item))
        mocks[5].assert_called_once()
        self.assertNotIn(CHANNEL,json.dumps(self.record()))

    def test_rejected_or_deferred_never_sent(self):
        mocks=self.context(verification={'status':'deferred','reason':'quota'})
        self.assertEqual(self.call()['status'],'verification_deferred')
        self.assertEqual(self.call()['previous_status'],'verification_deferred')
        mocks[5].assert_not_called()

    def test_uncertain_send_blocks_rerun(self):
        mocks=self.context(sender=lambda *a:{'status':'send_unknown'})
        self.assertEqual(self.call()['status'],'send_unknown')
        self.assertEqual(self.call()['previous_status'],'send_unknown')
        mocks[4].assert_called_once();mocks[5].assert_called_once()

    def test_failed_final_persistence_keeps_sending_and_blocks_second_send(self):
        mocks=self.context()
        def persist(path,state):
            if state['slots']['2026-W42']['status']=='published':raise OSError('disk')
            atomic_json(path,state)
        with patch('retro_weekly.persist',side_effect=persist):
            with self.assertRaises(OSError):self.call()
        self.assertEqual(self.call()['previous_status'],'sending')
        mocks[5].assert_called_once()

    def test_failed_pre_verification_marker_prevents_paid_call(self):
        mocks=self.context()
        with patch('retro_weekly.persist',side_effect=RuntimeError('push')):
            with self.assertRaises(RuntimeError):self.call()
        mocks[4].assert_not_called();mocks[5].assert_not_called()

    def test_lock_and_nonmain_context_block_side_effects(self):
        mocks=self.context()
        (self.storage/'retro_publish.lock').touch()
        self.assertEqual(self.call()['status'],'locked')
        mocks[4].assert_not_called()
        (self.storage/'retro_publish.lock').unlink()
        for key,value in [('GITHUB_REF','refs/heads/develop'),('GITHUB_EVENT_NAME','push'),
                          ('BOT_ENVIRONMENT','staging'),('GITHUB_WORKFLOW','other')]:
            with patch.dict(os.environ,{key:value}):
                with self.assertRaises(ValueError):self.call()
        mocks[5].assert_not_called()

    def test_manual_rights_caption_visual_and_source_review_are_required(self):
        for section,key,value in [('usage_review','status','needs_review'),('usage_review','evidence',''),
            ('usage_review','target_username','test'),('publication_review','visual_checked',False),
            ('publication_review','caption_sha256','changed'),('publication_review','image_identity','changed')]:
            item=copy.deepcopy(self.item);item[section][key]=value
            self.assertNotEqual(weekly.candidate_reason(item,set()),'pending_live_evidence_recheck')
        item=copy.deepcopy(self.item);item['post_html']+=' changed'
        self.assertEqual(weekly.candidate_reason(item,set()),'approved_caption_changed')

    def test_production_history_creek_and_staging_attempts_are_deduplicated(self):
        identity=image_identity(self.item['image_url'])
        (self.staging/'retro_photo_publications.json').write_text(json.dumps({'slots':{'staging:2026-W41':{'image_identity':identity,'status':'published','message_id':76}}}),encoding='utf-8')
        self.assertEqual(self.call()['diagnostics']['counts'],{'already_used_in_production_or_test':1})

    def test_evidence_failure_is_reported_without_paid_call(self):
        mocks=self.context()
        with patch('retro_publish.load_evidence',side_effect=ValueError('photo_date_or_location_not_confirmed')):
            result=self.call()
        self.assertEqual(result['status'],'no_verified_photo')
        self.assertEqual(result['evidence_diagnostics']['counts'],{'photo_date_or_location_not_confirmed':1})
        mocks[4].assert_not_called();mocks[5].assert_not_called()

    def test_transport_never_retries_timeout_and_masks_secrets(self):
        with patch('retro_photo.requests.post',side_effect=TimeoutError('secret')) as post:
            self.assertEqual(send_photo({'chat_id':CHANNEL},'secret',b'fixture'),{'status':'send_unknown'})
        post.assert_called_once()

    def test_destination_must_match_public_production_channel(self):
        response=Mock(status_code=200)
        response.json.return_value={'ok':True,'result':{'type':'channel','username':'wrong','id':int(CHANNEL)}}
        with patch('retro_photo.requests.post',return_value=response):
            with self.assertRaisesRegex(ValueError,'production_destination'):validate_destination('secret','@configured','rudubainews')
        response.close.assert_called_once()

    def test_actual_beach_test_is_excluded_from_proposed_production_queue(self):
        review=commons_review('https://commons.wikimedia.org/wiki/File:Persian_Gulf_(4462609004).jpg')
        item={'id':'old-test-76','image_url':review['image_url'],'source_url':review['source_url']}
        ready,used,diagnostics=weekly.queue_inventory(ROOT/'config/retro_publish_queue.json',ROOT/'storage/dubai_news',ROOT/'storage/dubai_news_staging')
        self.assertEqual(weekly.candidate_reason(item,used),'already_used_in_production_or_test')

    def test_unknown_commons_card_not_whitelisted(self):
        self.assertFalse(source_allowed('https://commons.wikimedia.org/wiki/File:Unknown.jpg',allow_commons=True))
        self.assertEqual(discovery_eligibility('https://example.com/file'),'unsupported_archive_source')

    def test_commons_payload_requires_exact_credit_license_hash_and_sharealike(self):
        review=commons_review('https://commons.wikimedia.org/wiki/File:Persian_Gulf_(4462609004).jpg')
        item=copy.deepcopy(self.item)
        item.update(source_url=review['source_url'],image_url=review['image_url'])
        item['usage_review'].update(basis='explicit_license',rights_holder=review['author'],
            attribution=review['attribution'],source_url=review['source_url'],image_url=review['image_url'],
            channel_id=CHANNEL,license_url=review['metadata']['LicenseUrl'].rstrip('/')+'/',
            image_sha1=review['sha1'],modifications='none',share_alike=True)
        caption=photo_payload(item,CHANNEL)['caption']
        for value in [review['attribution'],review['source_url'],'CC BY-SA 2.0','Фото без изменений.']:
            self.assertIn(value,caption)
        for key,value in [('license_url','https://example.com'),('rights_holder','unknown'),
                          ('attribution','unknown'),('image_sha1','other'),('share_alike',False),('modifications','cropped')]:
            changed=copy.deepcopy(item);changed['usage_review'][key]=value
            with self.assertRaises(ValueError):photo_payload(changed,CHANNEL)

    def test_photo_http_transport_confirms_exact_destination_and_dimensions(self):
        response=Mock(status_code=200)
        response.json.return_value={'ok':True,'result':{'message_id':1,'chat':{'id':int(CHANNEL)},
            'photo':[{'file_id':'fixture','width':640,'height':338}]}}
        payload={'chat_id':CHANNEL,'photo':'https://images.example.com/fixture.jpg','caption':'fixture'}
        with patch('retro_photo.requests.post',return_value=response) as post:
            result=send_photo(payload,'fixture-secret',b'fixture')
        self.assertEqual(result['status'],'published')
        self.assertEqual(result['photo_dimensions'],{'width':640,'height':338})
        self.assertTrue(post.call_args.args[0].endswith('/sendPhoto'))
        self.assertEqual(post.call_args.kwargs['files']['photo'][1],b'fixture')
        self.assertNotIn('photo',post.call_args.kwargs['data'])
        response.close.assert_called_once()

    def test_commons_license_author_date_hash_and_attribution_fail_closed(self):
        data=json.loads((ROOT/'tests/fixtures/commons_beach_api.json').read_text(encoding='utf-8'))
        review=commons_review('https://commons.wikimedia.org/wiki/File:Persian_Gulf_(4462609004).jpg')
        item={'source_url':review['source_url'],'image_url':review['image_url']}
        source=validate_metadata(item,data)
        self.assertIn('1993-11-01',source['text']);self.assertIn("I'm guessing",source['text'])
        self.assertNotIn('2018-02-21 05:37:23',source['text'])
        for key in ['Artist','LicenseUrl','LicenseShortName','ImageDescription','DateTimeOriginal','Credit']:
            changed=copy.deepcopy(data)
            next(iter(changed['query']['pages'].values()))['imageinfo'][0]['extmetadata'][key]['value']='changed'
            with self.assertRaisesRegex(ValueError,'commons_metadata_changed'):validate_metadata(item,changed)
        variant=item['image_url'].replace('%28','(').replace('%29',')')+'?tracking=1'
        self.assertEqual(image_identity(variant),image_identity(item['image_url']))

    def test_queue_beyond_first30_diagnosed_without_silent_loss(self):
        rows=[dict(self.item,id=str(i),source_url='https://unknown.example.com/file') for i in range(31)]+[self.item]
        self.save_queue(rows)
        result=self.call(False)
        self.assertEqual(result['diagnostics']['candidates'],32)
        self.assertEqual(result['diagnostics']['ready_for_recheck'],1)

    def test_malformed_rows_do_not_hide_valid_candidate(self):
        missing=copy.deepcopy(self.item);missing.pop('post_html')
        null=copy.deepcopy(self.item);null['post_html']=None
        bad=copy.deepcopy(self.item);bad['image_url']=123
        self.save_queue([missing,null,bad,self.item])
        result=self.call(False)
        self.assertEqual(result['diagnostics']['candidates'],4)
        self.assertEqual(result['diagnostics']['ready_for_recheck'],1)

    def test_image_byte_mismatch_blocks_verification_and_send(self):
        mocks=self.context()
        with patch('retro_weekly.download_approved_photo',side_effect=ValueError('approved_image_bytes_changed')):
            self.assertEqual(self.call()['reason'],'approved_image_bytes_changed')
        mocks[4].assert_not_called();mocks[5].assert_not_called()
        response=Mock(status_code=200)
        response.iter_content.return_value=[b'changed']
        with patch('retro_photo.requests.get',return_value=response):
            with self.assertRaisesRegex(ValueError,'approved_image_bytes_changed'):
                download_approved_photo(self.item)
        response.close.assert_called_once()

    def test_download_returns_exact_approved_bytes_without_redirects(self):
        response=Mock(status_code=200)
        response.iter_content.return_value=[b'offline ',b'photo']
        with patch('retro_photo.requests.get',return_value=response) as get:
            self.assertEqual(download_approved_photo(self.item),b'offline photo')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_failed_sending_marker_prevents_send_and_blocks_retry(self):
        mocks=self.context()
        def persist(path,state):
            if state['slots']['2026-W42']['status']=='sending':raise OSError('push')
            atomic_json(path,state)
        with patch('retro_weekly.persist',side_effect=persist):
            with self.assertRaises(OSError):self.call()
        self.assertEqual(self.call()['previous_status'],'verifying')
        mocks[4].assert_called_once();mocks[5].assert_not_called()
