"""Persistence, metric conversion, review provenance and restore integrity."""
import io
import json
import math
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
import workbench as w
import processing as p
from PIL import Image

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.old=(w.ROOT,w.DATA,w.DB_PATH)
        self.temp=tempfile.TemporaryDirectory(prefix='innowing-test-',dir=APP/'data')
        w.ROOT=Path(self.temp.name);w.DATA=w.ROOT/'data';w.DATA.mkdir();w.DB_PATH=w.DATA/'test.sqlite3'
        (w.ROOT/'assets').mkdir()
        for name in ('map-regions.json','map-calibration.json'):
            shutil.copyfile(APP/'assets'/name,w.ROOT/'assets'/name)
        w.init()
        buffer=io.BytesIO();Image.new('RGB',(100,80),'blue').save(buffer,'JPEG');self.data=buffer.getvalue()
        self.pid=w.import_photo(self.data,'test/folder/test.jpg')['id']
        buffer=io.BytesIO();Image.new('RGB',(100,80),'red').save(buffer,'JPEG');self.other=w.import_photo(buffer.getvalue(),'test/other.jpg')['id']
    def tearDown(self):
        w.ROOT,w.DATA,w.DB_PATH=self.old
        self.temp.cleanup()
    def annotation(self):return w.get_photo(self.pid)['annotation']
    def marked(self):
        a=self.annotation();a.update(floor='GF',camera={'x_px':500,'y_px':250},heading_deg=0,status='marked',position_confidence='confirmed',heading_confidence='confirmed');return a
    def test_axis_origin_and_nominal_seven_metres(self):
        for floor,cal in w.maps()['calibration']['floors'].items():
            x,y=cal['origin_pixel'];self.assertLess(math.hypot(*w.metric(floor,x,y)),.00001)
            dc=next(c for c in cal['x']['controls'] if c['axis']=='DC');dd=next(c for c in cal['x']['controls'] if c['axis']=='DD')
            self.assertAlmostEqual(w.metric(floor,dd['pixel'],y)[0]-w.metric(floor,dc['pixel'],y)[0],7,delta=.035)
            self.assertEqual(cal['unit_status'],'accepted_mm_pending_verification')
    def test_zero_heading_distinct_from_unknown(self):
        a=w.save_annotation(self.pid,self.marked(),0);self.assertEqual(a['heading_deg'],0)
        a.update(heading_deg=None,status='unknown');a=w.save_annotation(self.pid,a,a['revision']);self.assertIsNone(a['heading_deg'])
    def test_persistence_uses_database_not_client_state(self):
        a=self.marked();a['notes']['shooting']='现场入口右侧';saved=w.save_annotation(self.pid,a,0)
        self.assertEqual(w.get_photo(self.pid)['annotation']['notes']['shooting'],'现场入口右侧')
        self.assertAlmostEqual(saved['camera']['x_m'],w.metric('GF',500,250)[0],places=5)
    def test_revision_conflict_does_not_overwrite(self):
        w.save_annotation(self.pid,self.marked(),0)
        with self.assertRaises(RuntimeError):w.save_annotation(self.pid,self.marked(),0)
        self.assertEqual(w.get_photo(self.pid)['annotation']['revision'],1)
    def test_undo_multiple_versions_and_first_save(self):
        a=self.marked();a['notes']['content']='one';a=w.save_annotation(self.pid,a,0)
        a['notes']['content']='two';a=w.save_annotation(self.pid,a,a['revision'])
        self.assertEqual(w.undo(self.pid)['notes']['content'],'one')
        self.assertEqual(w.undo(self.pid)['status'],'unmarked')
    def test_invalid_coordinates_and_boxes_rejected(self):
        a=self.marked();a['camera']['x_px']=-1
        with self.assertRaises(ValueError):w.save_annotation(self.pid,a)
        a=self.marked();a['boxes']=[{'x':.9,'y':0,'width':.2,'height':.5}]
        with self.assertRaises(ValueError):w.save_annotation(self.pid,a)
    def test_batch_is_atomic_and_floor_change_clears_geometry(self):
        w.save_annotation(self.pid,self.marked(),0)
        with self.assertRaises(ValueError):w.batch_annotations([self.pid,self.other],{'status':'marked'})
        self.assertEqual(w.get_photo(self.other)['annotation']['revision'],0)
        w.batch_annotations([self.pid],{'floor':'LGF','status':'review'})
        a=w.get_photo(self.pid)['annotation'];self.assertIsNone(a['camera']);self.assertEqual(a['floor'],'LGF')
    def test_candidate_review_preserves_raw_and_removes_stale_fact(self):
        cid=w.add_candidate(self.pid,'ocr','OCR','raw spelling',{},'untouched raw','cache-key')
        w.review_candidate(cid,'confirmed','corrected spelling')
        photo=w.get_photo(self.pid);self.assertEqual(photo['candidates'][0]['raw'],'untouched raw');self.assertEqual(photo['facts'][0]['value'],'corrected spelling')
        w.review_candidate(cid,'rejected');self.assertEqual(w.get_photo(self.pid)['facts'],[])
    def test_candidate_cache_idempotent(self):
        cid=w.add_candidate(self.pid,'ocr','OCR','text',{},'raw','same-key')
        self.assertEqual(cid,w.add_candidate(self.pid,'ocr','OCR','text',{},'raw','same-key'))
        self.assertEqual(len(w.get_photo(self.pid)['candidates']),1)
    def test_chinese_and_exact_text_search(self):
        a=self.marked();a['notes']['content']='入口照片墙';w.save_annotation(self.pid,a)
        w.save_fact({'photo_id':self.pid,'value':'INDUCTION REQUIRED','subject':'sign'})
        self.assertEqual(w.search('照片墙')[0]['photo_id'],self.pid)
        self.assertEqual(w.search('INDUCTION')[0]['photo_id'],self.pid)
    def test_backup_restore_and_mismatched_map_rejection(self):
        w.save_annotation(self.pid,self.marked())
        snapshot=w.snapshot();a=self.annotation();a['notes']['content']='later';w.save_annotation(self.pid,a)
        w.restore_snapshot(snapshot);self.assertEqual(self.annotation()['notes']['content'],'')
        snapshot['photos'][0]['annotation']['map_version']='other-version'
        with self.assertRaises(ValueError):w.restore_snapshot(snapshot)
        self.assertTrue(list((w.DATA/'backups').glob('*.sqlite3')))
    def test_restore_rolls_back_on_invalid_entity(self):
        value=w.snapshot();value['photos'][0]['annotation']['notes']['content']='should rollback';value['entities']=[{'id':'bad'}]
        with self.assertRaises(ValueError):w.restore_snapshot(value)
        self.assertEqual(w.get_photo(value['photos'][0]['id'])['annotation']['notes']['content'],'')
    def test_duplicate_image_content_preserves_single_identity(self):
        result=w.import_photo(self.data,'another/name.jpg');self.assertTrue(result['duplicate']);self.assertEqual(result['id'],self.pid);self.assertEqual(len(w.list_photos()),2)
    def test_confirmed_room_names_preserve_geometry_and_restore(self):
        initial=w.snapshot();before=next(r for f in w.maps()['floors'] if f['id']=='LGF' for r in f['regions'] if r['id']=='L01')
        original_name=before['label'];points=json.loads(json.dumps(before['points']))
        w.confirm_room_name('LGF','L01','现场确认 Brainstorm Room','门牌核对')
        regions=w.maps()['floors'][1]['regions']
        self.assertEqual(next(r for r in regions if r['id']=='L01')['points'],points)
        self.assertEqual(next(r for r in regions if r['id']=='L08')['site_name'],'现场确认 Brainstorm Room')
        w.restore_snapshot(initial)
        self.assertEqual(w.maps()['floors'][1]['regions'][0]['label'],original_name)
    def test_entity_cross_photo_deduplication(self):
        entity=w.save_entity({'name':'柜子','type':'locker'})
        for pid in (self.pid,self.other):
            a=w.get_photo(pid)['annotation'];a.update(floor='GF',targets=[{'id':'t-'+pid,'name':'柜子','type':'point','floor':'GF','x_px':500,'y_px':250,'entity_id':entity['id'],'area_id':'G07','confidence':'confirmed'}]);w.save_annotation(pid,a)
        report=w.entity_report();self.assertEqual(len(report),1);self.assertEqual(report[0]['photo_count'],2);self.assertEqual(len(report[0]['confirmed_room_ids']),1)
    def test_export_separates_pending_candidates_from_confirmed_index(self):
        w.add_candidate(self.pid,'vision','model','UNCONFIRMED-CANDIDATE',{},'raw')
        w.save_fact({'photo_id':self.pid,'value':'CONFIRMED-EVIDENCE','subject':'sign'})
        with zipfile.ZipFile(io.BytesIO(w.export_zip())) as z:
            chunks=json.loads(z.read('spatial-chunks.json'));self.assertIn('CONFIRMED-EVIDENCE',json.dumps(chunks));self.assertNotIn('UNCONFIRMED-CANDIDATE',json.dumps(chunks))
            self.assertIn('region-distances.json',z.namelist());self.assertIn('map-calibration.json',z.namelist());self.assertIsNone(z.testzip())
    def test_path_traversal_and_invalid_thumbnail_id(self):
        with self.assertRaises(ValueError):w.import_photo(self.data,'../../escape.jpg')
        with self.assertRaises(ValueError):w.thumbnail('../../escape',100)
    def test_vision_contract_with_mock_transport(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({'choices':[{'message':{'content':'{"text":"SIGN","objects":[],"description":"visible sign"}'}}]}).encode()
        old=p.RUNTIME['api_key'];p.RUNTIME['api_key']='test-placeholder'
        try:
            with patch('urllib.request.urlopen',return_value=Response()) as request:
                parsed,raw=p.vision(Image.new('RGB',(50,50)),{'vision_model':'test','vision_url':'https://example.invalid/vision','auth_mode':'azure','max_completion_tokens':1600})
                payload=json.loads(request.call_args[0][0].data)
                self.assertEqual(payload['messages'][0]['content'][1]['type'],'image_url');self.assertEqual(parsed['text'],'SIGN')
        finally:p.RUNTIME['api_key']=old

if __name__=='__main__':unittest.main()
