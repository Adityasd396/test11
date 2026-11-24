"""Opt-in web push for private messages. Dispatch runs outside web requests."""
import base64
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from flask import abort, jsonify, request, send_from_directory

PROVIDERS = {
    'fcm.googleapis.com': 'Android / Chromium',
    'web.push.apple.com': 'Apple',
    'updates.push.services.mozilla.com': 'Firefox',
}
TTL = 900


def provider(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        return None
    try:
        url = urlsplit(endpoint)
    except ValueError:
        return None
    if url.scheme != 'https' or url.netloc not in PROVIDERS or not url.path.startswith('/') or url.fragment:
        return None
    if '\\' in endpoint or any(ord(c) < 33 or ord(c) > 126 for c in endpoint):
        return None
    return PROVIDERS[url.netloc]


def valid_key(value, size):
    if not isinstance(value, str) or not re.fullmatch(r'[\w-]{1,100}', value, re.ASCII):
        return False
    try:
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
        return len(raw) == size and (size != 65 or raw[0] == 4)
    except ValueError:
        return False


def register_push(app, db, limited, csrf_valid, config):
    initialized = set()
    social = app.extensions['solace_social']

    def configured():
        return all(config(key) for key in (
            'SOLACE_PUSH_PUBLIC_KEY', 'SOLACE_PUSH_PRIVATE_KEY', 'SOLACE_PUSH_CONTACT'))

    def initialize():
        if app.instance_path in initialized:
            return
        db().executescript("""
        CREATE TABLE IF NOT EXISTS push_preferences (
          owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
        CREATE TABLE IF NOT EXISTS push_subscriptions (
          endpoint TEXT PRIMARY KEY, owner TEXT NOT NULL, subscription TEXT NOT NULL,
          platform TEXT NOT NULL, updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS push_owner ON push_subscriptions(owner, updated);
        CREATE TABLE IF NOT EXISTS push_jobs (
          id TEXT PRIMARY KEY, endpoint TEXT NOT NULL, owner TEXT NOT NULL,
          sender TEXT NOT NULL, conversation TEXT NOT NULL, message INTEGER NOT NULL,
          platform TEXT NOT NULL, created REAL NOT NULL, due REAL NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'queued',
          delivered REAL, opened REAL, UNIQUE(endpoint, message));
        CREATE INDEX IF NOT EXISTS push_due ON push_jobs(state, due);
        CREATE INDEX IF NOT EXISTS push_created ON push_jobs(created);
        """)
        initialized.add(app.instance_path)

    @app.get('/solace-push.js')
    def push_script():
        return send_from_directory(app.static_folder, 'solace-push.js', max_age=0)

    def enabled(owner):
        row=db().execute('SELECT enabled FROM push_preferences WHERE owner=?',(owner,)).fetchone()
        return row is None or bool(row['enabled'])

    @app.route('/api/me/push', methods=['GET', 'POST', 'PATCH', 'DELETE'])
    def subscription():
        owner = social['named']()['id']
        if request.method == 'GET':
            return jsonify(enabled=enabled(owner),public_key=config('SOLACE_PUSH_PUBLIC_KEY') if configured() else None)
        if not csrf_valid():
            abort(403)
        if limited('push-settings:' + owner, 12, 60):
            abort(429)
        form = request.get_json(silent=True)
        if not isinstance(form, dict):
            abort(400)
        if request.method=='PATCH':
            if set(form)!={'enabled'} or type(form['enabled']) is not bool:
                abort(400)
            db().execute('INSERT INTO push_preferences VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET enabled=excluded.enabled',
                         (owner,int(form['enabled'])))
            db().commit()
            return jsonify(enabled=form['enabled'])
        endpoint = form.get('endpoint')
        platform = provider(endpoint)
        if not platform:
            abort(400, description='This push provider is unsupported.')
        if request.method == 'DELETE':
            db().execute('DELETE FROM push_subscriptions WHERE owner=? AND endpoint=?', (owner, endpoint))
        else:
            if not configured():
                abort(503)
            keys = form.get('keys')
            if not isinstance(keys, dict) or not valid_key(keys.get('p256dh'), 65) or not valid_key(keys.get('auth'), 16):
                abort(400)
            value = json.dumps({'endpoint': endpoint, 'keys': {k: keys[k] for k in ('p256dh', 'auth')}})
            db().execute('INSERT INTO push_subscriptions VALUES(?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET owner=excluded.owner,subscription=excluded.subscription,platform=excluded.platform,updated=excluded.updated',
                         (endpoint, owner, value, platform, time.time()))
            db().execute('DELETE FROM push_subscriptions WHERE owner=? AND endpoint NOT IN (SELECT endpoint FROM push_subscriptions WHERE owner=? ORDER BY updated DESC LIMIT 6)', (owner, owner))
        db().commit()
        return jsonify(ok=True)

    @app.post('/api/push/receipt')
    def receipt():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        token, event = data.get('id'), data.get('event')
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32}', token) or event not in ('delivered', 'opened'):
            abort(400)
        # A random per-job capability allows a service worker to report without cookies.
        if event == 'opened':
            db().execute("UPDATE push_jobs SET delivered=COALESCE(delivered,?),opened=COALESCE(opened,?) WHERE id=? AND attempts>0 AND created>?", (time.time(), time.time(), token, time.time()-86400))
        else:
            db().execute("UPDATE push_jobs SET delivered=COALESCE(delivered,?) WHERE id=? AND attempts>0 AND created>?", (time.time(), token, time.time()-86400))
        db().commit()
        return '', 204

    def enqueue(owner, sender, conversation, message):
        if not configured() or not enabled(owner):
            return
        now = time.time()
        for row in db().execute('SELECT endpoint,platform FROM push_subscriptions WHERE owner=? ORDER BY updated DESC LIMIT 6', (owner,)).fetchall():
            db().execute('INSERT OR IGNORE INTO push_jobs(id,endpoint,owner,sender,conversation,message,platform,created,due) VALUES(?,?,?,?,?,?,?,?,?)',
                         (secrets.token_urlsafe(24), row['endpoint'], owner, sender, conversation, message, row['platform'], now, now))

    def run():
        if not configured():
            raise RuntimeError('Configure the three SOLACE_PUSH settings first.')
        from requests import Session, RequestException
        from pywebpush import webpush, WebPushException

        class PushSession(Session):
            def post(self, url, *args, **kwargs):
                if not provider(url):
                    raise ValueError('Unsupported provider')
                return super().post(url, *args, allow_redirects=False, **kwargs)

        initialize()
        now = time.time()
        db().execute("UPDATE push_jobs SET state='failed' WHERE state='queued' AND created<=?", (now-TTL,))
        db().execute('DELETE FROM push_jobs WHERE created<?', (now-7*86400,))
        db().commit()
        with PushSession() as transport:
            for _ in range(12):
                t = time.time()
                job = db().execute("""UPDATE push_jobs SET due=?,attempts=attempts+1
                    WHERE id=(SELECT id FROM push_jobs WHERE state='queued' AND due<=? ORDER BY due LIMIT 1)
                    RETURNING *""", (t+90,t)).fetchone()
                db().commit()
                if not job:
                    break
                row = db().execute('SELECT subscription FROM push_subscriptions WHERE endpoint=? AND owner=?', (job['endpoint'],job['owner'])).fetchone()
                unread = db().execute("""SELECT 1 FROM dm_messages m JOIN dm_inbox i
                    ON i.conversation=m.conversation AND i.owner=?
                    WHERE m.id=? AND m.deleted=0 AND m.id>MAX(i.read_id,i.hidden_until)""",
                    (job['owner'],job['message'])).fetchone()
                safety = app.extensions['solace_safety']
                status = 'skipped'
                if (row and unread and enabled(job['owner']) and social['friends'](job['owner'],job['sender'])
                    and not social['blocked'](job['owner'],job['sender'])
                    and not safety['paused']('messaging') and not safety['restricted'](job['owner'],'messaging')
                    and not safety['restricted'](job['sender'],'messaging')):
                    code = 0
                    try:
                        response = webpush(json.loads(row['subscription']), data=json.dumps({'id':job['id']}),
                                           vapid_private_key=config('SOLACE_PUSH_PRIVATE_KEY'),
                                           vapid_claims={'sub':config('SOLACE_PUSH_CONTACT')},
                                           requests_session=transport, timeout=5, ttl=max(1,int(job['created']+TTL-t)))
                        code = response.status_code
                    except WebPushException as error:
                        code = error.response.status_code if error.response is not None else 0
                    except (ValueError, RequestException):
                        pass
                    status = 'sent' if 200 <= code < 300 else 'failed'
                    if code in (404,410):
                        db().execute('DELETE FROM push_subscriptions WHERE endpoint=? AND subscription=?', (job['endpoint'],row['subscription']))
                    if (code in (0,429) or code >= 500) and job['attempts'] < 4 and t+90 < job['created']+TTL:
                        status = 'queued'
                db().execute('UPDATE push_jobs SET state=? WHERE id=? AND attempts=?', (status,job['id'],job['attempts']))
                db().commit()

    def overview():
        counts = db().execute("""SELECT platform, COUNT(*) AS jobs,
            SUM(state='queued') AS queued, SUM(state='sent') AS sent,
            SUM(state='skipped') AS skipped, SUM(state='failed') AS failed,
            COUNT(delivered) AS delivered, COUNT(opened) AS opened
            FROM push_jobs WHERE created>? GROUP BY platform""", (time.time()-86400,)).fetchall()
        devices = db().execute('SELECT platform,COUNT(*) AS subscriptions,COUNT(DISTINCT owner) AS profiles FROM push_subscriptions GROUP BY platform').fetchall()
        return {'configured': configured(), 'results': list(map(dict, counts)), 'devices': list(map(dict, devices))}

    app.cli.command('dispatch-push')(run)
    app.extensions['solace_push'] = {'initialize': initialize, 'enqueue': enqueue, 'overview': overview}
