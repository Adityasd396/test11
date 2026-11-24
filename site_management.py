"""Site publishing, privacy-minimized usage reports, and read-only Google reporting."""
import datetime as dt
import hashlib
import io
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape

from flask import abort, g, jsonify, request, send_file, session
from PIL import Image, ImageOps, UnidentifiedImageError
from provider_settings import PROVIDERS, SECRET_TYPES, describe_fields, read_overrides, validate as validate_provider, write_overrides

BASE = '/ops/solace/management'
DAY = 86400
FOOTER = {'/about':'About','/faq':'FAQ','/contact':'Contact','/privacy':'Privacy',
          '/terms':'Terms','/community-guidelines':'Guidelines'}


def https_origin(value):
    """Accept only an HTTPS origin with a normal host and default port."""
    if not isinstance(value, str) or not re.fullmatch(r'https://[A-Za-z0-9.-]+/?', value):
        return ''
    host = urlsplit(value).hostname
    if not host or not all(re.fullmatch(r'[a-zA-Z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part)
                           for part in host.split('.')) or '.' not in host:
        return ''
    return 'https://' + host


def indexable_query(args):
    return all(re.fullmatch(r'utm_[a-z0-9_]{1,32}|gclid|fbclid|msclkid', key) for key in args)


def crawler_files(site, public):
    """Build the public files and their admin previews from the same settings."""
    origin = site['origin']
    enabled = bool(origin and site['index'])
    private_paths = (
        '/api/', '/ops/', '/messages', '/settings', '/friends', '/rooms', '/games',
        '/room-media', '/media-view', '/home', '/welcome', '/invite', '/room-control',
        '/account', '/history', '/signin', '/signup', '/app', '/create', '/join',
        '/love', '/static/love', '/*?room=',
    )
    urls = [origin + path for path in public] if enabled else []
    rules = ['User-agent: *']
    if enabled:
        rules.extend('Disallow: ' + path for path in private_paths)
        rules.append('Sitemap: ' + origin + '/sitemap.xml')
    else:
        rules.append('Disallow: /')
    sitemap = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    sitemap += ''.join('<url><loc>' + escape(url) + '</loc></url>' for url in urls) + '</urlset>'
    return {
        'indexing': enabled, 'sitemap_urls': len(urls), 'origin': origin,
        'robots': {'path': '/robots.txt', 'status': 200, 'body': '\n'.join(rules) + '\n'},
        'sitemap': {'path': '/sitemap.xml', 'status': 200 if origin else 503, 'body': sitemap},
    }


def register_management(app, db, config, limited, public):
    ops = app.extensions['solace_ops']
    staff = app.extensions['solace_staff']
    initialized = set()
    sightings = {}
    started_rooms = set()
    report_cache = {}
    last_cleanup = {}

    def initialize():
        if app.instance_path in initialized:
            return
        db().executescript("""
        CREATE TABLE IF NOT EXISTS site_revisions (
            id INTEGER PRIMARY KEY, settings TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS site_artwork (
            id TEXT PRIMARY KEY, media BLOB NOT NULL, mime TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS activity_days (
            day INTEGER NOT NULL, identity TEXT NOT NULL, kind TEXT NOT NULL,
            last REAL NOT NULL, PRIMARY KEY(day,identity));
        CREATE INDEX IF NOT EXISTS activity_recent ON activity_days(last);
        CREATE INDEX IF NOT EXISTS activity_identity ON activity_days(identity, day);
        CREATE TABLE IF NOT EXISTS growth_landings (
            day INTEGER NOT NULL, identity TEXT NOT NULL, source TEXT NOT NULL,
            PRIMARY KEY(day,identity,source));
        CREATE TABLE IF NOT EXISTS growth_rooms (
            room_key TEXT PRIMARY KEY, created REAL NOT NULL, source TEXT NOT NULL,
            paired INTEGER NOT NULL DEFAULT 0, started INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS growth_rooms_date ON growth_rooms(created);
        CREATE TABLE IF NOT EXISTS activity_counts (
            day INTEGER NOT NULL, event TEXT NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY(day,event));
        """)
        initialized.add(app.instance_path)

    def defaults():
        origin = https_origin(config('SOLACE_PUBLIC_URL'))
        return dict(name=str(config('SOLACE_SITE_NAME','Solace'))[:60], origin=origin,
                    index=bool(origin), contact=str(config('SOLACE_CONTACT_EMAIL')),
                    address='', title='', description='', verification='',
                    logo='/static/default-avatar.svg', favicon='/static/default-avatar.svg',
                    image='/static/social-preview.png', footer=list(FOOTER))

    def current():
        row = db().execute('SELECT * FROM site_revisions ORDER BY id DESC LIMIT 1').fetchone()
        return (row['id'],json.loads(row['settings']),row['created']) if row else (0,defaults(),None)

    def validate(raw):
        if not isinstance(raw,dict) or set(raw)!=set(defaults()):
            raise ValueError('Submit every site field.')
        data = dict(raw)
        for field, maximum in {'name':60,'origin':254,'contact':254,'address':400,
                               'title':80,'description':180,'verification':160,
                               'logo':150,'favicon':150,'image':150}.items():
            value = data[field]
            if not isinstance(value,str) or len(value)>maximum or any(ord(c)<32 and (field!='address' or c!='\n') for c in value):
                raise ValueError('Check the value for '+field+'.')
            data[field]=value.strip()
        if not data['name']:
            raise ValueError('Enter a site name.')
        origin = https_origin(data['origin'])
        trusted = {https_origin(config('SOLACE_PUBLIC_URL'))}
        trusted.update(https_origin(x.strip()) for x in str(config('SOLACE_SITE_ORIGINS')).split(','))
        if request.is_secure:
            trusted.add(https_origin(request.host_url))
        trusted.discard('')
        if data['origin'] and (not origin or origin not in trusted):
            raise ValueError('Use this HTTPS site or an origin approved in SOLACE_SITE_ORIGINS.')
        data['origin']=origin
        if type(data['index']) is not bool or (data['index'] and not origin):
            raise ValueError('Indexing requires a configured HTTPS origin.')
        if data['contact'] and not re.fullmatch(r'[A-Za-z0-9._+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',data['contact']):
            raise ValueError('Enter a valid public email address.')
        if data['verification'] and not re.fullmatch(r'[A-Za-z0-9_-]+',data['verification']):
            raise ValueError('Enter only the Google verification value.')
        asset_root = Path(app.static_folder).resolve()
        for field in ('logo','favicon','image'):
            asset = data[field]
            uploaded=re.fullmatch(r'/site-artwork/([0-9a-f]{32})',asset)
            if uploaded and db().execute('SELECT 1 FROM site_artwork WHERE id=?',(uploaded[1],)).fetchone():
                continue
            if not re.fullmatch(r'/static/[A-Za-z0-9_/-]+\.(?:png|jpg|jpeg|webp|svg|ico)',asset):
                raise ValueError('Use a public image path under /static/ for '+field+'.')
            path = (asset_root/asset.removeprefix('/static/')).resolve()
            if not path.is_relative_to(asset_root) or not path.is_file():
                raise ValueError('Image file is missing: '+field+'.')
            if field=='image' and path.suffix in ('.svg','.ico'):
                raise ValueError('Use a raster image for social previews.')
        if not isinstance(data['footer'],list) or any(not isinstance(link,str) or link not in FOOTER for link in data['footer']):
            raise ValueError('Choose valid footer destinations.')
        data['footer']=list(dict.fromkeys(data['footer']))
        return data

    def mutation():
        if not ops['csrf_valid']():
            abort(403)
        body=request.get_json(silent=True)
        if not isinstance(body,dict):
            abort(400)
        return body

    @app.route(BASE+'/site',methods=['GET','POST'])
    def management_site():
        actor=staff['require']('settings:write')
        revision,settings,updated=current()
        if request.method=='GET':
            past=[dict(row) for row in db().execute('SELECT id,created FROM site_revisions ORDER BY id DESC LIMIT 20')]
            return jsonify(revision=revision,settings=settings,updated=updated,history=past,footer=FOOTER)
        body=mutation()
        if limited('site-edit:'+actor['staff_id'],30,60):
            abort(429)
        action=body.get('action')
        if action not in ('preview','publish','restore'):
            abort(400)
        try:
            if action=='restore':
                source=body.get('restore')
                if type(source) is not int:
                    abort(400)
                row=db().execute('SELECT settings FROM site_revisions WHERE id=?',(source,)).fetchone()
                if not row:
                    abort(404)
                settings=validate(json.loads(row[0]))
            else:
                settings=validate(body.get('settings'))
        except ValueError as error:
            return jsonify(error=str(error)),400
        enabled=settings['index']
        preview=dict(title=(settings['title'] or public['/'][0])+' · '+settings['name'],
                     description=settings['description'] or public['/'][1],
                     canonical=settings['origin']+'/' if settings['origin'] else None,
                     robots='index, follow' if enabled else 'noindex, nofollow',
                     sitemap_urls=len(public) if enabled else 0)
        if action=='preview':
            return jsonify(preview=preview)
        reason=body.get('reason')
        if body.get('confirmed') is not True or not isinstance(reason,str) or not 12<=len(reason.strip())<=500 or type(body.get('revision')) is not int:
            return jsonify(error='Confirm and provide a reason of 12–500 characters.'),400
        db().execute('BEGIN IMMEDIATE')
        if current()[0]!=body['revision']:
            return jsonify(error='Settings changed. Reload the current revision and try again.'),409
        if not revision:
            db().execute('INSERT INTO site_revisions(settings,created) VALUES(?,?)',(json.dumps(defaults()),time.time()))
        new=db().execute('INSERT INTO site_revisions(settings,created) VALUES(?,?)',(json.dumps(settings),time.time())).lastrowid
        app.extensions['solace_safety']['audit']({'id':actor['staff_id'],'username':actor['username']},
                                                 'site_'+action,str(new),reason.strip(),'success',False)
        db().commit()
        return jsonify(revision=new,settings=settings,preview=preview)

    @app.get('/site-artwork/<asset_id>')
    def management_artwork(asset_id):
        if not re.fullmatch(r'[0-9a-f]{32}',asset_id):
            abort(404)
        row=db().execute('SELECT media,mime FROM site_artwork WHERE id=?',(asset_id,)).fetchone()
        if not row:
            abort(404)
        return send_file(io.BytesIO(row['media']),mimetype=row['mime'],etag=asset_id,max_age=86400)

    @app.post(BASE+'/artwork')
    def management_upload_artwork():
        actor=staff['require']('settings:write')
        if not ops['csrf_valid']():
            abort(403)
        if limited('site-artwork:'+actor['staff_id'],12,86400):
            abort(429)
        request.max_content_length=2*1024*1024+16384
        image=request.files.get('image')
        field=request.form.get('field')
        if not image or field not in ('logo','favicon','image'):
            abort(400)
        raw=image.stream.read(2*1024*1024+1)
        if len(raw)>2*1024*1024:
            abort(413)
        try:
            opened=Image.open(io.BytesIO(raw),formats=('PNG','JPEG','WEBP'))
            if not (1<=opened.width*opened.height<=12_000_000):
                raise ValueError()
            if getattr(opened,'n_frames',1)!=1:
                raise ValueError()
            image=ImageOps.exif_transpose(opened)
            output=io.BytesIO()
            if field=='image':
                image=ImageOps.fit(image.convert('RGB'),(1200,630))
                image.save(output,'WEBP',quality=85)
                mime='image/webp'
            else:
                size=192 if field=='favicon' else 512
                image.thumbnail((size,size),Image.Resampling.LANCZOS)
                image.convert('RGBA').save(output,'PNG',optimize=True)
                mime='image/png'
        except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError):
            return jsonify(error='Choose a still PNG, JPEG or WebP up to 2 MiB and 12 megapixels.'),400
        content=output.getvalue()
        identifier=hashlib.sha256(content).hexdigest()[:32]
        db().execute('BEGIN IMMEDIATE')
        if not db().execute('SELECT 1 FROM site_artwork WHERE id=?',(identifier,)).fetchone():
            total=db().execute('SELECT COALESCE(SUM(length(media)),0) FROM site_artwork').fetchone()[0]
            if total+len(content)>30*1024*1024:
                return jsonify(error='The site artwork storage limit has been reached.'),413
            db().execute('INSERT INTO site_artwork VALUES(?,?,?,?)',(identifier,content,mime,time.time()))
            app.extensions['solace_safety']['audit']({'id':actor['staff_id'],'username':actor['username']},
                                                     'site_artwork',identifier,'Uploaded site artwork','success',False)
        db().commit()
        return jsonify(path='/site-artwork/'+identifier),201

    def key(kind,value):
        return hmac.new(str(app.secret_key).encode(),('growth:'+kind+':'+value).encode(),hashlib.sha256).hexdigest()

    def acquisition():
        try:
            host=(urlsplit(request.referrer or '').hostname or '').lower()
        except ValueError:
            host=''
        return 'search_referral' if (re.fullmatch(r'(?:www\.)?google\.(?:com|[a-z]{2}|co\.[a-z]{2})',host)
                                     or host in ('www.bing.com','bing.com','duckduckgo.com','search.yahoo.com')) else 'other'

    def expire_activity(day,now):
        if now-last_cleanup.get(app.instance_path,0)<21600:
            return
        for table in ('activity_days','growth_landings','activity_counts'):
            db().execute('DELETE FROM '+table+' WHERE day<?',(day-30,))
        db().execute('DELETE FROM growth_rooms WHERE created<?',((day-30)*DAY,))
        db().commit()
        last_cleanup[app.instance_path]=now

    @app.after_request
    def collect(response):
        owner=session.get('uid')
        if not owner or not 200<=response.status_code<300:
            return response
        path=request.path
        read=request.method=='GET'
        room=(request.view_args or {}).get('room_id')
        landing=read and path in public and indexable_query(request.args)
        data=request.get_json(silent=True) if not read and path.startswith('/api/rooms') and request.is_json else None
        action=data.get('action') if isinstance(data,dict) else None
        created=path=='/api/rooms' and request.method=='POST'
        joined=room and path=='/api/rooms/'+room and action=='join'
        watch=getattr(g,'active_watch_room',None) if path=='/api/me/watch' else None
        dm=path.startswith('/api/me/conversations/') and response.status_code==201
        world=path=='/api/me/world/messages' and response.status_code==201
        room_message=bool(room and path=='/api/rooms/'+room and action=='chat')
        message_event=('direct_messages' if dm else 'world_messages' if world else 'room_messages' if room_message else None)
        activity=landing or created or joined or watch or message_event or (read and path in ('/home','/friends','/messages','/world','/account','/games'))
        if not activity:
            return response
        now=time.time()
        day=int(now//DAY)
        identity=key('profile',owner)
        kind='account' if getattr(g,'account',None) else 'guest'
        named=kind=='account'
        if landing:
            prior=session.get('growth_attribution')
            if not prior or now-prior[1]>1800:
                session['growth_attribution']=[acquisition(),now]
        if len(sightings)>3000:
            sightings.clear()
        stamp=(app.instance_path,identity)
        due=now-sightings.get(stamp,0)>=75
        if not (due or landing or created or joined or (watch and (app.instance_path,key('room',watch)) not in started_rooms) or message_event):
            return response
        try:
            expire_activity(day,now)
            if not named:
                named=bool(db().execute('SELECT 1 FROM profiles WHERE id=?',(owner,)).fetchone())
            room_key=key('room',room or watch) if room or watch else ''
            pair=joined and len((response.get_json(silent=True) or {}).get('participants',[]))>=2
            if due and named:
                db().execute('INSERT INTO activity_days VALUES(?,?,?,?) ON CONFLICT(day,identity) DO UPDATE SET last=excluded.last,kind=excluded.kind',(day,identity,kind,now))
            if landing:
                db().execute('INSERT OR IGNORE INTO growth_landings VALUES(?,?,?)',(day,identity,acquisition()))
            if created:
                result=response.get_json(silent=True) or {}
                attribution=session.get('growth_attribution')
                source=attribution[0] if attribution and now-attribution[1]<=1800 else 'other'
                if result.get('id'):
                    db().execute('INSERT OR REPLACE INTO growth_rooms(room_key,created,source) VALUES(?,?,?)',(key('room',result['id']),now,source))
            if pair:
                db().execute('UPDATE growth_rooms SET paired=1 WHERE room_key=? AND paired=0',(room_key,))
            if watch and (app.instance_path,room_key) not in started_rooms:
                changed=db().execute('UPDATE growth_rooms SET started=1 WHERE room_key=? AND paired=1 AND started=0',(room_key,)).rowcount
                if changed:
                    if len(started_rooms)>=3000:started_rooms.clear()
                    started_rooms.add((app.instance_path,room_key))
            if message_event:
                db().execute('INSERT INTO activity_counts VALUES(?,?,1) ON CONFLICT(day,event) DO UPDATE SET count=count+1',
                             (day,message_event))
            db().commit()
            if due and named:
                sightings[stamp]=now
        except sqlite3.Error:
            db().rollback()
        return response

    @app.get(BASE+'/overview')
    def management_overview():
        staff['require']('ops:view')
        days=request.args.get('days',7,type=int)
        if days not in (1,7,30):
            abort(400)
        now=time.time()
        day=int(now//DAY)
        cutoff=day-days+1
        expire_activity(day,now)
        rows=db().execute('SELECT day, SUM(kind=\'account\') accounts,SUM(kind=\'guest\') guests FROM activity_days WHERE day>=? GROUP BY day',(cutoff,)).fetchall()
        series={row['day']:dict(row) for row in rows}
        active=db().execute('SELECT COUNT(DISTINCT identity) FROM activity_days WHERE day>=?',(cutoff,)).fetchone()[0]
        online=db().execute('SELECT COUNT(DISTINCT identity) FROM activity_days WHERE last>?',(now-120,)).fetchone()[0]
        accounts=db().execute('SELECT COUNT(*) FROM accounts WHERE created>=?',(cutoff*DAY,)).fetchone()[0]
        growth=[]
        for source in ('search_referral','other'):
            visits=db().execute('SELECT COUNT(DISTINCT identity) FROM growth_landings WHERE day>=? AND source=?',(cutoff,source)).fetchone()[0]
            row=db().execute('SELECT COUNT(*) rooms, COALESCE(SUM(paired),0) paired, COALESCE(SUM(started),0) playback FROM growth_rooms WHERE created>=? AND source=?',(cutoff*DAY,source)).fetchone()
            growth.append(dict(source=source,landing_profiles=visits,**dict(row)))
        counts=db().execute('SELECT day,event,count FROM activity_counts WHERE day>=? ORDER BY day,event',(cutoff,)).fetchall()
        daily_messages={}
        totals={}
        for row in counts:
            event=row['event']
            daily_messages.setdefault(row['day'],{})[event]=row['count']
            totals[event]=totals.get(event,0)+row['count']
        return jsonify(active_profiles=active,recent_profiles=online,new_accounts=accounts,growth=growth,
                       daily=[dict(date=dt.datetime.fromtimestamp(d*DAY,dt.timezone.utc).date().isoformat(),
                                   accounts=series.get(d,{}).get('accounts',0),guests=series.get(d,{}).get('guests',0), messages=daily_messages.get(d,{}))
                              for d in range(cutoff,day+1)],messages=[dict(event=event,count=count) for event,count in totals.items()])

    @app.get(BASE+'/users')
    def management_users():
        actor=staff['require']('users:view')
        if limited('staff-directory:'+actor['staff_id'],30,60):
            abort(429)
        kind=request.args.get('kind','account')
        offset=request.args.get('offset',0,type=int)
        term=request.args.get('q','').strip()
        code=request.args.get('code','').strip()
        if kind not in ('account','guest','all') or not 0<=offset<=5000 or len(term)>60 or len(code)>32:
            abort(400)
        condition={'all':'1=1','account':'a.id IS NOT NULL','guest':'a.id IS NULL'}[kind]
        params=[term]
        code_filter=''
        if code:
            compact=re.sub(r'[\s-]','',code).upper()
            legacy=re.sub(r'\s','',code).upper()
            code_filter=''' AND p.id IN (
                SELECT owner FROM social_profiles WHERE code=?
                UNION SELECT owner FROM friend_code_aliases WHERE code=?)'''
            params.extend((compact,legacy))
        params.append(offset)
        rows=db().execute('''SELECT p.id, COALESCE(a.name,p.name) AS name, a.created, a.verified, s.code
                            FROM (SELECT id,name FROM accounts UNION ALL SELECT id,name FROM profiles
                                  WHERE id NOT IN (SELECT id FROM accounts)) p
                            LEFT JOIN accounts a ON a.id=p.id
                            LEFT JOIN social_profiles s ON s.owner=p.id
                            WHERE '''+condition+''' AND instr(lower(COALESCE(a.name,p.name)),lower(?))>0'''+code_filter+
                          ' ORDER BY COALESCE(a.name,p.name) COLLATE NOCASE,p.id LIMIT 26 OFFSET ?',params).fetchall()
        results=[]
        for row in rows[:25]:
            last=db().execute('SELECT MAX(last) FROM activity_days WHERE identity=? AND day>=?',(key('profile',row['id']),int(time.time()//DAY)-30)).fetchone()[0]
            restrictions=db().execute('SELECT kind,feature,created,expires,revoked FROM safety_restrictions WHERE owner=? ORDER BY created DESC LIMIT 5',(row['id'],)).fetchall()
            results.append(dict(name=row['name'],created=row['created'],verified=row['verified'],
                                last_active=last,code=row['code'],restrictions=list(map(dict,restrictions))))
        return jsonify(users=results,more=len(rows)>25,offset=offset)

    @app.get(BASE+'/integrations')
    def management_integrations():
        staff['require']('ops:view')
        backup_root=Path(config('SOLACE_BACKUP_DIRECTORY') or Path(app.instance_path).parent/'backups')
        try:
            manifests = (*backup_root.glob('*/backup.json'), *backup_root.glob('*/database/backup.json'))
            latest = max((manifest.stat().st_mtime for manifest in manifests if manifest.is_file()), default=None)
        except OSError:
            latest=None
        try:
            release=json.loads((Path(app.root_path)/'release.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):
            release={'version':'development'}
        revision,site,updated=current()
        return jsonify(release=release, crawler=crawler_files(site,public), site_revision=revision,
                       site_updated=updated, google_site_verified_tag=bool(site['verification']),
                       latest_backup_manifest=latest,
                       youtube_configured=app.extensions['solace_youtube_enabled'](),
                       email_configured=app.extensions['solace_account_mail']['ready'](),
                       push_configured=app.extensions['solace_push']['overview']()['configured'],
                       search_property=bool(config('SOLACE_SEARCH_PROPERTY')),
                       analytics_property=bool(config('SOLACE_GA4_PROPERTY')),
                       google_credentials_configured=bool(config('SOLACE_GOOGLE_SERVICE_ACCOUNT') or config('GOOGLE_APPLICATION_CREDENTIALS')))

    @app.route(BASE+'/providers',methods=['GET','POST'])
    def management_providers():
        actor=staff['require']('settings:write')
        if request.method=='GET':
            return jsonify(providers=describe_fields(app))
        if not request.is_secure and not app.testing:
            abort(403,description='Use HTTPS to save provider settings.')
        if limited('provider-settings:'+actor['staff_id'],10,60):
            abort(429)
        body=mutation()
        group=body.get('provider')
        action=body.get('action')
        if not isinstance(group,str) or group not in PROVIDERS or action not in ('save','reset') or body.get('confirmed') is not True:
            abort(400)
        fields={field[0]:field[2] for field in PROVIDERS[group][1]}
        submitted=body.get('settings')
        if not isinstance(submitted,dict) or set(submitted)-fields.keys():
            abort(400)
        try:
            changes={key:validate_provider(app,key,value) for key,value in submitted.items()
                     if fields[key] not in SECRET_TYPES or value!=''} if action=='save' else {}
        except (ValueError,OSError) as error:
            return jsonify(error=str(error) if isinstance(error,ValueError) else 'Check the backup directory.'),400
        connection=db()
        connection.execute('BEGIN IMMEDIATE')
        try:
            overrides=read_overrides(app)
            if action=='reset':
                for field in fields:
                    overrides.pop(field,None)
            else:
                overrides.update(changes)
            app.extensions['solace_safety']['audit'](
                {'id':actor['staff_id'],'username':actor['username']},
                'provider_settings_'+action,group,'Owner confirmed provider configuration change','success',False)
            write_overrides(app,overrides)
            connection.commit()
        except (OSError,ValueError,sqlite3.Error):
            connection.rollback()
            return jsonify(error='Could not save provider configuration.'),503
        report_cache.clear()
        return jsonify(message='Provider settings saved.' if action=='save' else 'Host settings restored.')

    @app.post(BASE+'/google')
    def management_google():
        actor=staff['require']('ops:view')
        body=mutation()
        if limited('google-report:'+actor['staff_id'],8,60):
            abort(429)
        action=body.get('action')
        if action not in ('search','sitemaps','inspect','analytics','realtime'):
            abort(400)
        site_property=str(config('SOLACE_SEARCH_PROPERTY'))
        ga_property=str(config('SOLACE_GA4_PROPERTY'))
        origin=current()[1]['origin']
        path=body.get('path','/')
        if action=='inspect' and (not isinstance(path,str) or path not in public or not origin):
            return jsonify(error='Choose a configured public page.'),400
        if action in ('analytics','realtime') and not re.fullmatch(r'\d{1,20}',ga_property):
            return jsonify(error='Set SOLACE_GA4_PROPERTY first.'),503
        if action in ('search','sitemaps','inspect') and not site_property:
            return jsonify(error='Set SOLACE_SEARCH_PROPERTY first.'),503
        cache_key=(app.instance_path,site_property,ga_property,origin,action,path if action=='inspect' else '')
        saved=report_cache.get(cache_key)
        if saved and time.time()-saved[0] < (30 if action=='realtime' else 300):
            return jsonify(fetched=saved[0],report=saved[1])
        try:
            from google.auth import default
            from google.auth.transport.requests import AuthorizedSession
            ga=action in ('analytics','realtime')
            scope='https://www.googleapis.com/auth/'+('analytics.readonly' if ga else 'webmasters.readonly')
            credential_json=config('SOLACE_GOOGLE_SERVICE_ACCOUNT')
            if credential_json:
                from google.oauth2.service_account import Credentials
                credentials=Credentials.from_service_account_info(json.loads(credential_json),scopes=[scope])
            else:
                credentials,_=default(scopes=[scope])
            search_url='https://www.googleapis.com/webmasters/v3/sites/'+quote(site_property,safe='')
            end=dt.date.today()-dt.timedelta(days=3)
            dates={'startDate':(end-dt.timedelta(days=27)).isoformat(),'endDate':end.isoformat()}
            with AuthorizedSession(credentials) as client:
                def fetch(method,url,payload=None):
                    response=client.request(method,url,json=payload,timeout=7,allow_redirects=False)
                    if not 200<=response.status_code<300:
                        raise ValueError()
                    return response.json()
                if action=='search':
                    report={dimension or 'totals':fetch('POST',search_url+'/searchAnalytics/query',
                                                        dict(dates,dimensions=[dimension] if dimension else [],rowLimit=20)).get('rows',[])
                            for dimension in ('','query','page')}
                elif action=='sitemaps':
                    report=fetch('GET',search_url+'/sitemaps').get('sitemap',[])[:30]
                elif action=='inspect':
                    report=fetch('POST','https://searchconsole.googleapis.com/v1/urlInspection/index:inspect',
                                 {'inspectionUrl':origin+path,'siteUrl':site_property}).get('inspectionResult',{}).get('indexStatusResult',{})
                else:
                    endpoint='https://analyticsdata.googleapis.com/v1beta/properties/'+ga_property
                    payload={'metrics':[{'name':'activeUsers'}],'limit':20}
                    if action=='analytics':
                        payload.update(dateRanges=[{'startDate':'28daysAgo','endDate':'yesterday'}],
                                       dimensions=[{'name':'sessionDefaultChannelGroup'}],
                                       metrics=[{'name':'sessions'},{'name':'activeUsers'}])
                    report=fetch('POST',endpoint+(':runReport' if action=='analytics' else ':runRealtimeReport'),payload).get('rows',[])
        except ImportError:
            return jsonify(error='Install Google reporting dependencies in the server environment.'),503
        except Exception:
            return jsonify(error='Google reporting failed. Check property access, credentials and hosting network access.'),502
        if len(report_cache)>=40:
            report_cache.clear()
        fetched=time.time()
        report_cache[cache_key]=(fetched,report)
        return jsonify(fetched=fetched,report=report)

    app.extensions['solace_management']={'initialize':initialize,'current':current,'footer':FOOTER,'public_paths':tuple(public)}
