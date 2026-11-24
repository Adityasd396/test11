"""Named MFA staff access layered onto the existing operations dashboard."""
import base64,hashlib,hmac,json,re,secrets,struct,time
from flask import abort,g,jsonify,redirect,render_template,request,session
from werkzeug.security import check_password_hash,generate_password_hash

BASE='/ops/solace'
PERMISSIONS={'ops:view','ops:sensitive','settings:write','storage:view','storage:write','reports:view','reports:review','evidence:read','users:view','users:restrict','users:ban','content:remove','rooms:close','switches:write','appeals:review','staff:manage','audit:view'}
ROLES={'administrator':PERMISSIONS,'moderator':{'ops:view','reports:view','reports:review','evidence:read','users:view','users:restrict','content:remove','rooms:close','appeals:review','audit:view'},'operations':{'ops:view','storage:view','audit:view'}}

def totp(secret,at=None,digits=6):
    raw=base64.b32decode(secret.upper(),casefold=True);counter=int((time.time() if at is None else at)//30)
    value=hmac.new(raw,struct.pack('>Q',counter),hashlib.sha1).digest();offset=value[-1]&15;number=struct.unpack('>I',value[offset:offset+4])[0]&0x7fffffff
    return str(number%(10**digits)).zfill(digits)

def register_staff(app,ops):
    ready=set();storage=ops['storage']
    def initialize():
        if app.instance_path in ready:return
        with storage(timeout=5) as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('''CREATE TABLE IF NOT EXISTS ops_staff(id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,password TEXT NOT NULL,otp TEXT NOT NULL,role TEXT NOT NULL,permissions TEXT NOT NULL DEFAULT '[]',enabled INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1,last_counter INTEGER NOT NULL DEFAULT -1,created REAL NOT NULL)''')
            c.execute('CREATE TABLE IF NOT EXISTS staff_security(id INTEGER PRIMARY KEY,staff TEXT,event TEXT NOT NULL,signal TEXT,created REAL NOT NULL)')
            c.execute('CREATE INDEX IF NOT EXISTS staff_security_time ON staff_security(created)')
            c.execute('CREATE TABLE IF NOT EXISTS staff_invites(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,expires REAL NOT NULL)')
            if 'staff_id' not in {r['name'] for r in c.execute('PRAGMA table_info(admin_sessions)')}:c.execute('ALTER TABLE admin_sessions ADD COLUMN staff_id TEXT')
        ready.add(app.instance_path)
    def has_staff():
        initialize()
        with storage() as c:return bool(c.execute('SELECT 1 FROM ops_staff LIMIT 1').fetchone())
    def version(row):return hashlib.sha256((row['password']+':'+str(row['revision'])).encode()).hexdigest()
    def permissions(row):return set(PERMISSIONS) if row['role']=='administrator' else (set(ROLES.get(row['role'],()))|set(json.loads(row['permissions'])))&PERMISSIONS
    def authenticate(digest):
        initialize()
        with storage() as c:
            r=c.execute('SELECT s.*,u.username,u.password,u.otp,u.role,u.permissions,u.enabled,u.revision FROM admin_sessions s JOIN ops_staff u ON u.id=s.staff_id WHERE s.token_hash=? AND s.expires>?',(digest,time.time())).fetchone()
            if not r or not r['enabled'] or r['version']!=version(r):return None
            return {**dict(r),'permissions':sorted(permissions(r))}
    def require(permission=None):
        if not ops['authorized']():abort(401)
        auth=g.ops_auth
        if not auth.get('staff_id'):abort(403,description='Enroll a named staff identity with two-factor authentication first.')
        if permission and permission not in auth['permissions']:abort(403,description='Your staff role does not permit this action.')
        return auth
    def security(c,staff,event,signal=''):
        c.execute('INSERT INTO staff_security(staff,event,signal,created) VALUES(?,?,?,?)',(staff,event,signal,time.time()));c.execute('DELETE FROM staff_security WHERE created<?',(time.time()-30*86400,))
    def login():
        initialize();username=request.form.get('username','').strip().casefold();password=request.form.get('password','');code=request.form.get('otp','');now=time.time();ip=ops['client_ip']();signal=hmac.new(str(app.secret_key).encode(),('staff-ip:'+ip).encode(),hashlib.sha256).hexdigest()[:24]
        buckets=['staff-ip:'+signal,'staff-name:'+hashlib.sha256(username.encode()).hexdigest()];error='Staff credentials or authentication code not accepted.'
        with storage(timeout=5) as c:
            c.execute('BEGIN IMMEDIATE');limited=False
            for bucket in buckets:
                r=c.execute('SELECT attempts,until FROM login_attempts WHERE ip=?',(bucket,)).fetchone()
                if r and r['until']>now and r['attempts']>=5:limited=True
            if limited:security(c,None,'staff_login_throttled',signal);return render_template('admin-login.html',csrf=session['ops_csrf'],error='Too many attempts. Try again in 15 minutes.',named_staff=True),429
            row=c.execute('SELECT * FROM ops_staff WHERE username=?',(username,)).fetchone();digest=row['password'] if row else app.extensions['solace_dummy_password']
            accepted=isinstance(password,str) and len(password)<=128 and check_password_hash(digest,password);counter=None
            if accepted and row and row['enabled'] and re.fullmatch(r'\d{6}',code):
                for step in [-1,0,1]:
                    candidate=int(now//30)+step
                    if candidate>row['last_counter'] and hmac.compare_digest(totp(row['otp'],candidate*30),code):counter=candidate;break
            if counter is None:
                for bucket in buckets:c.execute('INSERT INTO login_attempts VALUES(?,1,?) ON CONFLICT(ip) DO UPDATE SET attempts=CASE WHEN until<? THEN 1 ELSE attempts+1 END,until=CASE WHEN until<? THEN excluded.until ELSE until END',(bucket,now+900,now,now))
                security(c,row['id'] if row else None,'staff_login_failed',signal)
                return render_template('admin-login.html',csrf=session['ops_csrf'],error=error,named_staff=True),401
            c.execute('UPDATE ops_staff SET last_counter=? WHERE id=?',(counter,row['id']));c.executemany('DELETE FROM login_attempts WHERE ip=?',[(x,) for x in buckets]);token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
            c.execute('INSERT INTO admin_sessions(token_hash,expires,version,csrf,staff_id) VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),now+1800,version(row),csrf,row['id']));security(c,row['id'],'staff_login_success',signal)
            actor={'id':row['id'],'username':row['username']}
        session['ops_csrf']=csrf
        safety=app.extensions.get('solace_safety')
        if safety:safety['audit'](actor,'staff_login',actor['id'],'Multi-factor staff sign-in','success')
        response=redirect(BASE,303);response.set_cookie('solace_ops',token,max_age=1800,httponly=True,secure=request.is_secure,samesite='Strict',path=BASE);return response
    def create(username,password,role='administrator',otp=None):
        initialize();username=username.strip().casefold()
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,31}',username) or not 14<=len(password)<=128 or role not in ROLES:raise ValueError('Use a 3–32 character staff username, 14–128 character password and valid role.')
        secret=otp or base64.b32encode(secrets.token_bytes(20)).decode();identifier=secrets.token_urlsafe(18)
        with storage(timeout=5) as c:
            c.execute('BEGIN IMMEDIATE')
            if role!='administrator' and not c.execute("SELECT 1 FROM ops_staff WHERE role='administrator' AND enabled=1").fetchone():raise ValueError('Enroll the first administrator before other staff roles.')
            c.execute('INSERT INTO ops_staff(id,username,password,otp,role,created) VALUES(?,?,?,?,?,?)',(identifier,username,generate_password_hash(password,method='scrypt'),secret,role,time.time()));security(c,identifier,'staff_created_locally')
        return identifier,secret
    def actor():
        auth=require();return {'id':auth['staff_id'],'username':auth['username']}
    @app.before_request
    def guard_existing_ops():
        if not request.path.startswith(BASE) or request.path==BASE or request.path==BASE+'/logout' or not has_staff():return
        requirements={BASE+'/data':'ops:view',BASE+'/settings':'settings:write' if request.method=='POST' else 'ops:view',BASE+'/storage':'storage:write' if request.method=='POST' else 'storage:view'}
        if request.path in requirements:
            auth=require(requirements[request.path])
            if request.method=='POST':
                d=request.get_json(silent=True)
                if not isinstance(d,dict):abort(400)
                why=d.get('reason','')
                if d.get('confirmed') is not True or not isinstance(why,str) or not 12<=len(why)<=500:abort(400,description='Confirm this change and give a reason of 12–500 characters.')
                g.ops_change_reason=why
        if request.path==BASE+'/chat-backup.json':abort(403,description='Use justified case evidence review; unrestricted private chat exports are disabled.')
    @app.route(BASE+'/staff',methods=['GET','POST'])
    def staff_directory():
        auth=require('staff:manage')
        if request.method=='POST':
            if auth['role']!='administrator' or not ops['csrf_valid']():abort(403)
            d=request.get_json(silent=True)
            if not isinstance(d,dict):abort(400)
            reason=d.get('reason','');role=d.get('role');enabled=d.get('enabled');extra=d.get('permissions',[])
            if d.get('confirmed') is not True or not isinstance(reason,str) or not 12<=len(reason)<=500 or not isinstance(role,str) or role not in ROLES or type(enabled) is not bool or not isinstance(extra,list) or any(not isinstance(x,str) or x not in PERMISSIONS for x in extra):return jsonify(error='Confirm the change, provide a reason and choose valid staff permissions.'),400
            staff_actor=actor();safety=app.extensions.get('solace_safety');target=d.get('id')
            if not isinstance(target,str) or len(target)>100:abort(400)
            if safety:safety['audit'](staff_actor,'staff_change_intent',str(target),reason,'pending')
            with storage(timeout=5) as c:
                c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM ops_staff WHERE id=?',(target,)).fetchone()
                if not old:abort(404)
                if role=='administrator' and old['role']!='administrator' and old['last_counter']<0:
                    return jsonify(error='The staff member must sign in with MFA before promotion.'),409
                if old['role']=='administrator' and old['enabled'] and (role!='administrator' or not enabled) and c.execute("SELECT COUNT(*) FROM ops_staff WHERE role='administrator' AND enabled=1").fetchone()[0]<=1:return jsonify(error='The last enabled administrator cannot be removed.'),409
                c.execute('UPDATE ops_staff SET role=?,enabled=?,permissions=?,revision=revision+1 WHERE id=?',(role,int(enabled),json.dumps(sorted(set(extra))),target));c.execute('DELETE FROM admin_sessions WHERE staff_id=?',(target,));security(c,target,'staff_permissions_changed')
            if safety:safety['audit'](staff_actor,'staff_change',str(target),reason,'success')
        with storage() as c:rows=[{k:r[k] for k in ['id','username','role','enabled','created']}|{'permissions':sorted(permissions(r)),'custom_permissions':json.loads(r['permissions'])} for r in c.execute('SELECT * FROM ops_staff ORDER BY created LIMIT 100')]
        return jsonify(staff=rows,permissions=sorted(PERMISSIONS))
    def management_audit(auth, action, target, reason):
        app.extensions['solace_safety']['audit'](
            {'id':auth['staff_id'],'username':auth['username']},action,target,reason,'success')

    @app.post(BASE+'/management/staff')
    def management_staff():
        auth=require('staff:manage')
        if not ops['csrf_valid']():
            abort(403)
        if auth['role']!='administrator':
            abort(403)
        if auth['expires']-time.time()<1500:
            return jsonify(error='Sign in again with your authenticator before making staff changes.'),403
        data=request.get_json(silent=True)
        if not isinstance(data,dict):
            abort(400)
        username=data.get('username')
        action=data.get('action')
        reason=data.get('reason')
        if (not isinstance(username,str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,31}',username.casefold())
            or action not in ('invite','revoke','transfer') or data.get('confirmed') is not True
            or not isinstance(reason,str) or not 12<=len(reason.strip())<=500):
            abort(400)
        username=username.casefold()
        limited=app.extensions['solace_launch']['limited']
        if limited('staff-admin-action:'+auth['staff_id'],8,60):
            abort(429)
        with storage(timeout=5) as c:
            c.execute('BEGIN IMMEDIATE')
            target=c.execute('SELECT * FROM ops_staff WHERE username=?',(username,)).fetchone()
            token=None
            if action=='invite':
                if target:
                    return jsonify(error='This username already has a staff profile.'),409
                token=secrets.token_urlsafe(32)
                c.execute('DELETE FROM staff_invites WHERE expires<=? OR username=?',(time.time(),username))
                c.execute('INSERT INTO staff_invites VALUES(?,?,?)',
                          (hashlib.sha256(token.encode()).hexdigest(),username,time.time()+1800))
                message='Enrollment link created. Share it privately with the intended staff member.'
            else:
                if not target:
                    abort(404)
                if action=='transfer':
                    if target['id']==auth['staff_id'] or not target['enabled'] or target['last_counter']<0:
                        return jsonify(error='Choose another enabled staff member who has signed in with MFA.'),409
                    c.execute("UPDATE ops_staff SET role='administrator',revision=revision+1 WHERE id=?",(target['id'],))
                    c.execute("UPDATE ops_staff SET role='operations',permissions='[]',revision=revision+1 WHERE id=?",(auth['staff_id'],))
                    c.execute('DELETE FROM admin_sessions WHERE staff_id IN (?,?)',(auth['staff_id'],target['id']))
                    message='Administrator access transferred. Both staff members must sign in again.'
                else:
                    c.execute('DELETE FROM admin_sessions WHERE staff_id=?',(target['id'],))
                    message='Staff sessions revoked.'
            security(c,auth['staff_id'],'staff_'+action,username)
        management_audit(auth,'staff_'+action,username,reason.strip())
        return jsonify(message=message,token=token)

    @app.route(BASE+'/enroll',methods=['GET','POST'])
    def management_enroll():
        if not request.is_secure and not app.testing:
            abort(403)
        if request.method=='GET':
            session['staff_enroll_csrf']=secrets.token_urlsafe(24)
            return render_template('staff-enroll.html',csrf=session['staff_enroll_csrf'])
        expected=session.get('staff_enroll_csrf')
        supplied=request.headers.get('X-CSRF-Token','')
        if not expected or not hmac.compare_digest(expected,supplied):
            abort(403)
        limited=app.extensions['solace_launch']['limited']
        if limited('staff-enroll',12,120):
            abort(429)
        data=request.get_json(silent=True)
        token=data.get('token') if isinstance(data,dict) else None
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token):
            abort(400)
        action=data.get('action')
        if action not in ('prepare','activate'):
            abort(400)
        digest=hashlib.sha256(token.encode()).hexdigest()
        secret=base64.b32encode(hmac.new(str(app.secret_key).encode(),('staff-enroll:'+token).encode(),hashlib.sha256).digest()[:20]).decode()
        initialize()
        with storage(timeout=5) as c:
            if action=='activate':
                c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM staff_invites WHERE token_hash=? AND expires>?',(digest,time.time())).fetchone()
            if not row:
                return jsonify(error='This invitation has expired or has already been used.'),404
            username=row['username']
            if action=='prepare':
                return jsonify(username=username,authenticator_key=secret)
            password=data.get('password')
            code=data.get('code')
            now=time.time()
            if not isinstance(password,str) or not 14<=len(password)<=128:
                return jsonify(error='Use a password of 14–128 characters.'),400
            if not isinstance(code,str) or not any(hmac.compare_digest(code,totp(secret,now+step)) for step in (-30,0,30)):
                return jsonify(error='The authenticator code was not accepted.'),400
            if c.execute('SELECT 1 FROM ops_staff WHERE username=?',(username,)).fetchone():
                abort(409)
            identifier=secrets.token_urlsafe(18)
            c.execute("INSERT INTO ops_staff(id,username,password,otp,role,created) VALUES(?,?,?,?,?,?)",
                      (identifier,username,generate_password_hash(password,method='scrypt'),secret,'operations',now))
            c.execute('DELETE FROM staff_invites WHERE token_hash=?',(digest,))
            security(c,identifier,'staff_enrolled')
        session.pop('staff_enroll_csrf',None)
        return jsonify(message='Staff profile enrolled. Sign in with your authenticator.')

    app.extensions['solace_staff']={'initialize':initialize,'has_staff':has_staff,'authenticate':authenticate,'require':require,'login':login,'create':create,'actor':actor,'permissions':PERMISSIONS}
