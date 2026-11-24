import unittest
from unittest.mock import patch
import test_app
from app import app,video_id

class StreamSourcesTests(unittest.TestCase):
    setUp=test_app.WatchPartyTests.setUp
    tearDown=test_app.WatchPartyTests.tearDown

    def test_join_create_routes_and_direct_source_validation(self):
        for route in ['/','/join','/create']:self.assertEqual(self.host.get(route,follow_redirects=True).status_code,200)
        for value in ['https://media.example/movie.mp4?token=123','https://media.example/live.m3u8','https://media.example/film.webm']:self.assertEqual(video_id(value),value)
        for value in ['javascript:alert(1)','file:///movie.mp4','http://media.example/movie.mp4','https://user:password@media.example/a.mp4','https://netflix.com/watch/1','https://media.example/page.html']:self.assertIsNone(video_id(value))

    def test_mixed_queue_auto_advance_is_shared_and_duplicate_end_cannot_skip(self):
        direct='https://media.example/film.mp4'
        self.assertEqual(self.host.post(self.url,json={'action':'watch','url':direct}).status_code,200)
        self.host.post(self.url,json={'action':'add','url':'aqz-KE-bpKQ'})
        self.host.post(self.url,json={'action':'add','url':direct})
        before=self.host.get(self.url).json
        first=self.guest.post(self.url,json={'action':'next','expected_playback_id':before['playback_id']})
        self.assertEqual(first.json['video'],'aqz-KE-bpKQ');self.assertTrue(first.json['playing'])
        self.assertEqual(self.host.post(self.url,json={'action':'next','expected_playback_id':before['playback_id']}).status_code,409)
        second=self.host.post(self.url,json={'action':'next','expected_playback_id':first.json['playback_id']}).json
        self.assertEqual(second['video'],direct);self.assertTrue(second['playing']);self.assertEqual(second['position'],0)
        self.assertEqual(self.guest.get(self.url).json['video'],direct)

    def test_empty_queue_completion_keeps_the_current_video_instance(self):
        started = self.host.post(self.url, json={'action':'playback','position':27,'playing':True}).json
        ended = self.guest.post(self.url, json={'action':'next','expected_playback_id':started['playback_id']}).json
        self.assertEqual(ended['playback_id'], started['playback_id'])
        self.assertFalse(ended['playing'])
        self.assertGreaterEqual(ended['position'], 27)
        duplicate = self.host.post(self.url, json={'action':'next','expected_playback_id':started['playback_id']})
        self.assertEqual(duplicate.status_code, 409)

    def test_cheers_are_shared_without_invalidating_a_shot_revision(self):
        route=self.url+'/basketball';self.host.post(route,json={'action':'join'});state=self.guest.post(route,json={'action':'join'}).json
        response=self.guest.post(route,json={'action':'cheer','emoji':'🫶'});self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['revision'],state['revision']);self.assertEqual(self.host.get(route).json['cheer']['emoji'],'🫶')
        shot=self.host.post(route,json={'action':'shoot','aim':.5,'revision':state['revision']});self.assertEqual(shot.status_code,200);self.assertTrue(shot.json['shot']['goal'])
        self.assertEqual(self.host.post(route,json={'action':'cheer','emoji':'invalid'}).status_code,400)

    def test_media_inspection_requires_identity_and_preserves_signed_source(self):
        outsider=app.test_client()
        self.assertEqual(outsider.post('/api/media/resolve',json={'url':'https://media.example/x'},headers={'X-CSRF-Token':outsider.get('/api/me').json['csrf']}).status_code,401)
        signed='https://media.example/object?signature=a%2Fb&x=1&x=2'
        with patch('app.resolve_media',return_value={'url':signed,'format':'mp4','range_supported':True,'warning':None}) as resolver:
            result=self.host.post('/api/media/resolve',json={'url':signed},headers={'X-CSRF-Token':self.host.get('/api/me').json['csrf']})
            self.assertEqual(result.status_code,200);resolver.assert_called_once_with(signed)
        proof=result.json['source_token']
        self.assertEqual(self.guest.post(self.url,json={'action':'watch','url':signed,'source_token':proof}).status_code,400)
        result=self.host.post(self.url,json={'action':'watch','url':signed,'source_token':proof})
        self.assertEqual(result.status_code,200);self.assertEqual(result.json['video'],signed)
        self.assertEqual(self.guest.get(self.url).json['video'],signed)

if __name__=='__main__':unittest.main()
