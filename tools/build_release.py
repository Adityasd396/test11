"""Build and verify public release files only. Never package instance or credentials."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SERVER={'README.md','provider_settings.py','site_management.py','push_notifications.py','media_sources.py','room_video.py','companions.py','community.py','messaging.py','people.py','platform_safety.py','staff_auth.py','setup_staff.py','social.py','requirements.lock','.env.example','tools/maintenance.py','account_mail.py','user_storage.py','production.py','launch.py','game_options.py','ludo_game.py','app.py','room_game.py','admin_panel.py','setup_admin.py','requirements.txt','release.json','tools/storage.py','tools/verify_deployment.py'}

def validate_source(names,root=ROOT):
    for name in sorted(names):
        path=root/name
        if path.name not in {'Dockerfile','.dockerignore','.env.example'} and path.suffix not in {'.py','.js','.cjs','.ts','.tsx','.json','.html','.css','.yml','.sh'}:continue
        content=path.read_text(encoding='utf-8-sig')
        if re.search(r'^(?:<<<<<<< .+|=======|>>>>>>> .+)\r?$',content,re.MULTILINE):
            raise ValueError('Unresolved merge conflict in release source: '+name)
        if path.suffix=='.py':ast.parse(content,filename=name)
        if path.suffix=='.json':json.loads(content)

def build(version):
    if not re.fullmatch(r'[0-9A-Za-z._-]{1,40}',version):raise ValueError('Invalid version')
    release=(ROOT/'release').resolve()
    if release.parent!=ROOT:raise ValueError('Unsafe release directory')
    # A fresh release must be complete even if its old manifest was removed.
    names=set(SERVER)
    names.update(['tools/audit_python_dependencies.py','.github/workflows/verify.yml'])
    names.update(['requirements-audit.txt','tools/benchmark_local.py','tools/measure_startup.py','tools/measure_page_load.py','tools/generate_audit_media.py'])
    names.update(['setup.sh','Dockerfile','.dockerignore','run.py','serve.py','run_mobile_server.py','launch_mobile.py','start.bat','start-mobile.bat'])
    names.update(path.relative_to(ROOT).as_posix() for pattern in ['*.ts','*.tsx','*.cjs','*.json','app.config.js','index.js','.easignore','scripts/*.cjs'] for path in (ROOT/'mobile').glob(pattern) if path.is_file())
    names.add('tools/build_release.py')
    names.add('mobile/GestureComposer.tsx')
    names.add('mobile/ClientDiagnostics.tsx')
    names.add('tests/fixtures/stream-test.mp4')
    for folder,pattern in [('static','**/*'),('templates','**/*'),('tests','*.py'),('mobile/tests','*.test.cjs'),('mobile/assets','**/*.png')]:
        names.update(path.relative_to(ROOT).as_posix() for path in (ROOT/folder).glob(pattern) if path.is_file() and '__pycache__' not in path.parts)
    names={name for name in names if not name.startswith('static/media/')}
    validate_source(names)
    previous_manifest=release/'MANIFEST.json'
    if previous_manifest.is_file():
        for name in json.loads(previous_manifest.read_text(encoding='utf-8')).keys()-names:
            if Path(name).suffix.lower()!='.md':continue
            obsolete=release/name
            if not obsolete.resolve().is_relative_to(release):raise ValueError('Unsafe obsolete documentation path')
            if obsolete.is_file():obsolete.unlink()
    # Remove retired feature files from overlays as well as the new archives.
    for name in ['static/love.js','static/love.html','static/love.css','static/love-film.js','templates/love-public.html','static/media/for-jaanu.mp4','mobile/tests/love.test.cjs','mobile/tests/love-audio.test.cjs']:
        obsolete=(release/name).resolve()
        if not obsolete.is_relative_to(release):raise ValueError('Unsafe obsolete asset path')
        if obsolete.is_file():obsolete.unlink()
    for template in ['templates/index.html','templates/play-session.html','templates/world.html','templates/public-base.html','templates/home.html','templates/room-control.html','templates/room-video-upload.html','templates/room-video-player.html','templates/messages.html','templates/space-settings.html','templates/space-games.html','templates/room-media.html','templates/media-view.html','templates/account.html','templates/email-action.html','templates/admin.html','templates/admin-login.html','static/game.html','static/tictactoe.html','static/ludo.html']:
        path=ROOT/template
        source=path.read_text(encoding='utf-8')
        source=re.sub(r'<meta name="solace-release" content="[^"]*">','',source)
        source=source.replace('</head>','<meta name="solace-release" content="'+version+'"></head>')
        source=re.sub(r'(/static/[^"?#]+)(?:\?v=[^"&#]+)?(#[^"]*)?(?=")',
                      lambda match:match[1]+'?v='+version+(match[2] or ''),source)
        path.write_text(source,encoding='utf-8')
    digest=hashlib.sha256()
    for name in sorted(names-{'release.json'}):
        path=ROOT/name
        if not path.is_file():raise ValueError('Missing public release file: '+name)
        digest.update(name.encode());digest.update(path.read_bytes())
    (ROOT/'release.json').write_text(json.dumps({'version':version,'build':digest.hexdigest()[:16]},indent=2)+'\n',encoding='utf-8')
    manifest={}
    for name in sorted(names):
        source=(ROOT/name).resolve();target=(release/name).resolve()
        if not source.is_relative_to(ROOT) or not target.is_relative_to(release):raise ValueError('Unsafe release path')
        if any(part in {'instance','.git','node_modules','__pycache__','.expo','.openai'} or part.startswith('.export-') for part in Path(name).parts):raise ValueError('Private/generated path in release')
        if source.suffix in {'.db','.key','.hash'} or (source.name.startswith('.env') and source.name!='.env.example'):raise ValueError('Credential/storage file in release')
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);manifest[name]=hashlib.sha256(source.read_bytes()).hexdigest()
    (release/'MANIFEST.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    results={}
    for suffix,files in [('release-source.zip',names|{'MANIFEST.json'}),('pythonanywhere.zip',{n for n in names if n in SERVER or n.startswith(('static/','templates/'))})]:
        destination=ROOT/('Solace-'+suffix)
        with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for name in sorted(files):
                info=zipfile.ZipInfo(name,date_time=(2026,11,14,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
                archive.writestr(info,(release/name).read_bytes(),compresslevel=9)
        with zipfile.ZipFile(destination) as archive:
            if archive.testzip() is not None or set(archive.namelist())!=files:raise ValueError('Archive integrity failed')
            for name in files:
                if archive.read(name)!=(release/name).read_bytes():raise ValueError('Archive/source mismatch')
        results[destination.name]={'files':len(files),'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
    for name,value in manifest.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=value:raise ValueError('Source changed during packaging')
    return {'release':json.loads((ROOT/'release.json').read_text()),'archives':results,'private_files_excluded':True}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--version',default='2026.11.14.1');args=parser.parse_args();print(json.dumps(build(args.version),indent=2))
