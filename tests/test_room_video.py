import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
import test_app
from tools.maintenance import cleanup


class RoomVideoTests(unittest.TestCase):
    setUp = test_app.WatchPartyTests.setUp
    tearDown = test_app.WatchPartyTests.tearDown
    clip = Path(__file__).with_name('fixtures').joinpath('stream-test.mp4').read_bytes()

    def upload(self, client, data=None, csrf=True):
        headers = {'X-CSRF-Token': client.get('/api/me').json['csrf']} if csrf else {}
        return client.post('/api/me/room-video/' + self.url.rsplit('/', 1)[1],
                           data=self.clip if data is None else data,
                           content_type='application/octet-stream', headers=headers)

    def test_room_only_upload_range_seek_and_cleanup(self):
        result = self.upload(self.host)
        self.assertEqual(result.status_code, 201, result.data)
        url = result.json['url']
        self.assertEqual(app.test_client().get(url).status_code, 404)
        response = self.guest.get(url, headers={'Range': 'bytes=5-19'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, self.clip[5:20])
        self.assertIn('no-store', response.headers['Cache-Control'])
        response.close()
        self.assertEqual(self.guest.get(url, headers={'Range': 'bytes=99999999-'}).status_code, 416)
        player = self.guest.get(url.replace('/api/room-video/', '/room-video-player/'))
        self.assertEqual(player.status_code, 200)
        self.assertIn(url.encode(), player.data)
        self.assertEqual(self.guest.post(self.url, json={'action':'watch','url':url}).status_code, 400)
        queued = self.host.post(self.url, json={'action':'add','url':url}).json['queue']
        self.assertEqual(self.guest.post(self.url, json={'action':'select','id':queued[0]['id']}).json['video'], url)
        self.host.post(self.url, json={'action':'playback','position':2,'playing':False})
        self.assertAlmostEqual(self.guest.get(self.url).json['position'], 2)
        for client in (self.guest, self.host):
            client.post(self.url+'/leave', json={})
        self.assertEqual(self.host.get(url).status_code, 404)
        self.assertEqual(cleanup(app.instance_path)['room_video_files'], 1)

    def test_rejects_invalid_files_nonmembers_and_missing_csrf(self):
        self.assertEqual(self.upload(app.test_client()).status_code, 403)
        self.assertEqual(self.upload(self.host, csrf=False).status_code, 403)
        self.assertEqual(self.upload(self.host, b'<!DOCTYPE html>').status_code, 415)
        with patch.dict(os.environ, {'SOLACE_ROOM_VIDEO_MAX_MIB':'0'}):
            self.assertEqual(self.upload(self.host).status_code, 403)
        with patch.dict(os.environ, {'SOLACE_ROOM_VIDEO_STORAGE_MIB':'0'}):
            self.assertEqual(self.upload(self.host).status_code, 507)

    def test_proxy_denial_can_offer_device_playback_without_a_server_retry(self):
        from media_sources import InspectionUnavailable
        url = 'https://video.example/item?sig=a%2Fb&x=1&x=2'
        headers = {'X-CSRF-Token': self.host.get('/api/me').json['csrf']}
        with patch('app.resolve_media', side_effect=InspectionUnavailable(url, 'Host could not inspect')):
            original = self.host.post('/api/media/resolve', json={'url':url}, headers=headers)
            self.assertEqual(original.status_code, 400)
            self.assertTrue(original.json['can_try_direct'])
            result = self.host.post('/api/media/resolve', json={'url':url,'try_direct':True}, headers=headers)
        self.assertEqual(result.json['url'], url)
        self.assertEqual(self.host.post(self.url, json={'action':'watch','url':url,'source_token':result.json['source_token']}).json['video'], url)
