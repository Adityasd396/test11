"""Site publishing and staff enrollment integration tests."""
import io
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from PIL import Image

from app import app, db
from staff_auth import totp
import test_app
import test_safety


BASE='/ops/solace/management/'


class ManagementTests(unittest.TestCase):
    setUp=test_app.WatchPartyTests.setUp
    tearDown=test_app.WatchPartyTests.tearDown
    staff=test_safety.SafetyTests.staff

    def edit(self, client, csrf, path, data):
        return client.post(BASE+path,json=data,headers={'X-CSRF-Token':csrf})

    def test_publishing_preview_revision_restore_and_crawler_rules(self):
        admin,csrf,_=self.staff()
        operations,_,_=self.staff('operations','operator')
        self.assertEqual(operations.get(BASE+'site').status_code,403)
        with patch.dict(app.config,{'SOLACE_PUBLIC_URL':'','SOLACE_SITE_ORIGINS':'https://watch.example.test'}):
            settings=admin.get(BASE+'site').json['settings']
            settings.update(name='Moon House',origin='https://watch.example.test',index=True,contact='help@example.test',address='12 Test Avenue')
            form={'action':'preview','settings':settings,'revision':0,'confirmed':True,'reason':'Publish synthetic test settings'}
            self.assertEqual(self.edit(admin,csrf,'site',form).json['preview']['robots'],'index, follow')
            self.assertEqual(self.host.get('/sitemap.xml').status_code,503)
            rejected=self.edit(admin,csrf,'site',{**form,'settings':{**settings,'origin':'https://unapproved.example.test'}})
            self.assertEqual(rejected.status_code,400)
            published=self.edit(admin,csrf,'site',{**form,'action':'publish'})
            self.assertEqual(published.status_code,200,published.text)
            page=app.test_client().get('/watch-together?utm_source=campaign')
            self.assertIn('Moon House',page.text)
            self.assertIn('rel="canonical" href="https://watch.example.test/watch-together"',page.text)
            self.assertNotIn('noindex',page.headers.get('X-Robots-Tag',''))
            self.assertIn('noindex',self.host.get('/?room=123456').headers['X-Robots-Tag'])
            self.assertIn('12 Test Avenue',self.host.get('/contact').text)
            self.assertIn('/guides/youtube-playback',self.host.get('/sitemap.xml').text)
            self.assertEqual(self.edit(admin,csrf,'site',{**form,'action':'publish'}).status_code,409)
            restored=self.edit(admin,csrf,'site',{**form,'action':'restore','revision':published.json['revision'],'restore':1})
            self.assertEqual(restored.status_code,200,restored.text)
            self.assertEqual(self.host.get('/sitemap.xml').status_code,503)

    def test_integration_snapshot_matches_crawler_responses_without_exposing_secrets(self):
        self.staff()
        operations,_,_=self.staff('operations','operator')
        self.assertIn(self.host.get(BASE+'integrations').status_code,(401,403))
        backup_root=Path(app.instance_path)/'synthetic-backups'
        manifest=backup_root/'timestamp'/'database'/'backup.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{}',encoding='utf-8')
        config={'SOLACE_BACKUP_DIRECTORY':str(backup_root),'SOLACE_PUBLIC_URL':'https://watch.example.test','YOUTUBE_API_KEY':'never-show-this-test-key',
                'SOLACE_SMTP_HOST':'smtp.example.test','SOLACE_MAIL_FROM':''}
        with patch.dict(app.config,config):
            snapshot=operations.get(BASE+'integrations')
            self.assertEqual(snapshot.status_code,200)
            self.assertTrue(snapshot.json['youtube_configured'])
            self.assertFalse(snapshot.json['email_configured'])
            self.assertEqual(snapshot.json['latest_backup_manifest'],manifest.stat().st_mtime)
            self.assertNotIn(config['YOUTUBE_API_KEY'],snapshot.text)
            crawler=snapshot.json['crawler']
            self.assertTrue(crawler['indexing'])
            for key in ('robots','sitemap'):
                expected=crawler[key]
                with self.host.get(expected['path']) as response:
                    self.assertEqual((response.status_code,response.text),(expected['status'],expected['body']))
            urls=[tag.text for tag in ET.fromstring(crawler['sitemap']['body']).iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
            self.assertEqual(len(urls),crawler['sitemap_urls'])
            self.assertTrue(all(url.startswith(config['SOLACE_PUBLIC_URL']) and '/ops/' not in url for url in urls))

    def test_provider_settings_are_private_validated_and_used_by_runtime(self):
        admin,csrf,_=self.staff()
        operations,_,_=self.staff('operations','operator')
        self.assertEqual(operations.get(BASE+'providers').status_code,403)
        key='synthetic-Youtube-Provider-Key'
        payload={'provider':'youtube','action':'save','confirmed':True,'settings':{'YOUTUBE_API_KEY':key}}
        self.assertEqual(admin.post(BASE+'providers',json=payload).status_code,403)
        result=self.edit(admin,csrf,'providers',payload)
        self.assertEqual(result.status_code,200,result.text)
        self.assertNotIn(key,admin.get(BASE+'providers').text)
        self.assertNotIn(key,admin.get(BASE+'integrations').text)
        self.assertTrue(admin.get(BASE+'integrations').json['youtube_configured'])
        self.assertTrue(self.host.get('/api/settings').json['search_enabled'])
        for provider,fields in [('email',{'SOLACE_SMTP_HOST':'127.0.0.1'}),
                                ('backup',{'SOLACE_BACKUP_DIRECTORY':'relative/path'}),
                                ('google_credentials',{'SOLACE_GOOGLE_SERVICE_ACCOUNT':'{"type":"service_account","token_uri":"https://untrusted.example.test/token"}'})]:
            self.assertEqual(self.edit(admin,csrf,'providers',{**payload,'provider':provider,'settings':fields}).status_code,400)
        analytics=self.edit(admin,csrf,'providers',{**payload,'provider':'analytics','settings':{'SOLACE_GA4_PROPERTY':'12345678'}})
        self.assertEqual(analytics.status_code,200)
        self.assertTrue(admin.get(BASE+'integrations').json['analytics_property'])
        with app.app_context():
            logs=db().execute("SELECT reason FROM safety_audit WHERE action LIKE 'provider_settings_%'").fetchall()
            self.assertEqual(len(logs),2)
            self.assertNotIn(key,str([row['reason'] for row in logs]))
        with patch.dict(app.config,{'YOUTUBE_API_KEY':''}):
            self.assertEqual(self.edit(admin,csrf,'providers',{**payload,'action':'reset','settings':{}}).status_code,200)
            self.assertFalse(self.host.get('/api/settings').json['search_enabled'])

    def test_artwork_validated_and_served_without_input_metadata(self):
        admin,csrf,_=self.staff()
        image=io.BytesIO()
        Image.new('RGB',(300,160),'purple').save(image,'JPEG',comment=b'Metadata to remove')
        result=admin.post(BASE+'artwork',data={'field':'image','image':(io.BytesIO(image.getvalue()),'example.svg')},headers={'X-CSRF-Token':csrf})
        self.assertEqual(result.status_code,201,result.text)
        with self.host.get(result.json['path']) as asset:
            self.assertEqual(asset.mimetype,'image/webp')
            self.assertEqual(Image.open(io.BytesIO(asset.data)).size,(1200,630))
            self.assertNotIn(b'Metadata to remove',asset.data)
        invalid=admin.post(BASE+'artwork',data={'field':'logo','image':(io.BytesIO(b'<svg><script>bad</script></svg>'),'fake.png')},headers={'X-CSRF-Token':csrf})
        self.assertEqual(invalid.status_code,400)

    def test_two_user_funnel_records_once_and_directory_is_private(self):
        admin,_,_=self.staff()
        self.assertIn(self.host.get(BASE+'users').status_code,(401,403))
        landing=app.test_client()
        landing.get('/watch-together',headers={'Referer':'https://www.google.com/search?q=private'})
        created=landing.post('/api/me/profile',json={'name':'Growth host'},headers={'X-CSRF-Token':landing.get('/api/me').json['csrf']})
        self.assertEqual(created.status_code,200)
        room=landing.post('/api/rooms',json={}).json['id']
        for client in (landing,self.guest,self.guest):
            self.assertEqual(client.post('/api/rooms/'+room,json={'action':'join'}).status_code,200)
        landing.post('/api/rooms/'+room,json={'action':'playback','playing':True,'position':5})
        for _ in range(2):
            self.assertEqual(landing.post('/api/me/watch',json={'room':room,'active':True},headers={'X-CSRF-Token':landing.get('/api/me').json['csrf']}).status_code,200)
        room_path='/api/rooms/'+room
        self.assertEqual(landing.post(room_path,json={'action':'chat','body':'Private synthetic body'}).status_code,200)
        self.assertEqual(landing.post(room_path,json={'action':'chat','body':''}).status_code,400)
        report=admin.get(BASE+'overview?days=7').json
        self.assertEqual(report['messages'],[{'event':'room_messages','count':1}])
        self.assertEqual(report['daily'][-1]['messages'],{'room_messages':1})
        self.assertNotIn('Private synthetic body',str(report))
        search=report['growth'][0]
        self.assertEqual((search['landing_profiles'],search['rooms'],search['paired'],search['playback']),(1,1,1,1))
        directory=admin.get(BASE+'users?kind=guest&q=Growth').json
        self.assertEqual([u['name'] for u in directory['users']],['Growth host'])
        self.assertNotIn('email',directory['users'][0])
        self.assertEqual(landing.get('/api/me/connections').status_code,200)
        directory=admin.get(BASE+'users?kind=all&q=Growth').json
        code=directory['users'][0]['code']
        self.assertTrue(code)
        lookup=admin.get(BASE+'users',query_string={'kind':'all','code':code.lower()[:4]+'-'+code.lower()[4:]})
        self.assertEqual([u['name'] for u in lookup.json['users']],['Growth host'])
        self.assertEqual(admin.get(BASE+'users',query_string={'kind':'account','code':code}).json['users'],[])
        with app.app_context():
            self.assertNotEqual(db().execute("SELECT room_key FROM growth_rooms WHERE source='search_referral'").fetchone()[0],room)

    def test_staff_enrollment_one_use_and_authenticated_transfer(self):
        admin,csrf,_=self.staff()
        fields={'username':'next-staff','confirmed':True,'reason':'Authorized synthetic transfer test'}
        invited=self.edit(admin,csrf,'staff',{**fields,'action':'invite'})
        self.assertEqual(invited.status_code,200,invited.text)
        token=invited.json['token']
        incoming=app.test_client()
        enroll='/ops/solace/enroll'
        csrf_enroll=re.search(r'name="ops-csrf" content="([^"]+)"',incoming.get(enroll).text)[1]
        header={'X-CSRF-Token':csrf_enroll}
        prepare=incoming.post(enroll,json={'action':'prepare','token':token},headers=header)
        secret=prepare.json['authenticator_key']
        code=totp(secret)
        password='Synthetic-Incoming-Staff-26'
        payload={'action':'activate','token':token,'password':password,'code':code}
        self.assertEqual(incoming.post(enroll,json=payload,headers=header).status_code,200)
        self.assertEqual(incoming.post(enroll,json=payload,headers=header).status_code,403)
        self.assertEqual(self.edit(admin,csrf,'staff',{**fields,'action':'transfer'}).status_code,409)
        sign_in=incoming.get('/ops/solace').text
        sign_csrf=re.search(r'name="csrf" value="([^"]+)"',sign_in)[1]
        self.assertEqual(incoming.post('/ops/solace',data=dict(username='next-staff',password=password,otp=code,csrf=sign_csrf)).status_code,303)
        self.assertEqual(self.edit(admin,csrf,'staff',{**fields,'action':'transfer'}).status_code,200)
        self.assertEqual(admin.get(BASE+'site').status_code,401)
        self.assertEqual(incoming.get(BASE+'site').status_code,401)

    def test_google_permissions_and_bounded_provider_queries(self):
        admin,csrf,_=self.staff()
        calls=[]
        class Provider:
            def __init__(self,*args):pass
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def request(self,method,url,**options):
                calls.append((method,url,options))
                return SimpleNamespace(status_code=200,json=lambda:{'rows':[{'clicks':9}]})
        modules={'google.auth':SimpleNamespace(default=lambda **kw:('test',None)),
                 'google.auth.transport.requests':SimpleNamespace(AuthorizedSession=Provider)}
        with patch.dict(app.config,{'SOLACE_SEARCH_PROPERTY':'sc-domain:watch.example.test'}),patch.dict(sys.modules,modules):
            report=self.edit(admin,csrf,'google',{'action':'search'})
            self.assertEqual(report.status_code,200,report.text)
            self.assertEqual(report.json['report']['totals'][0]['clicks'],9)
            self.assertEqual(len(calls),3)
            self.assertTrue(all(call[1].startswith('https://www.googleapis.com/') and not call[2]['allow_redirects'] for call in calls))
            self.assertEqual(self.edit(admin,csrf,'google',{'action':'search'}).status_code,200)
            self.assertEqual(len(calls),3)
