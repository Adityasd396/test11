"""Browser regression for synchronized uploaded video in a disposable local room."""
import logging
import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright, expect
from waitress import create_server


def main():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    with TemporaryDirectory(prefix='solace-room-video-browser-') as folder:
        os.environ['SOLACE_INSTANCE_PATH']=folder
        os.environ['SOLACE_ENV']='development'
        from app import app
        check(app,root)


def check(app,root):
    logging.getLogger('waitress.queue').setLevel(logging.ERROR)
    server=create_server(app,host='127.0.0.1',port=0)
    Thread(target=server.run,daemon=True).start()
    base='http://127.0.0.1:'+str(server.effective_port)
    try:
        with sync_playwright() as pw,pw.chromium.launch(args=['--autoplay-policy=no-user-gesture-required']) as browser:
            host,peer=[browser.new_context(viewport={'width':390,'height':844},has_touch=True) for _ in range(2)]
            def post(c,path,data):
                csrf=c.request.get(base+'/api/me').json()['csrf']
                r=c.request.post(base+path,data=data,headers={'X-CSRF-Token':csrf})
                assert r.ok,(path,r.status,r.text())
                return r.json()
            post(host,'/api/me/profile',{'name':'Host QA'})
            post(peer,'/api/me/profile',{'name':'Guest QA'})
            room=post(host,'/api/rooms',{})['id']
            route='/api/rooms/'+room
            for c in (host,peer):post(c,route,{'action':'join'})
            a,b=host.new_page(),peer.new_page()
            for page in (a,b):
                page.route('https://www.youtube.com/**',lambda r:r.abort())
                page.goto(base+'/?room='+room,wait_until='domcontentloaded')
                expect(page.locator('.bottom-nav [data-target=queue]')).to_be_visible()
            a.locator('.bottom-nav [data-target=queue]').click()
            a.locator('.queue-add').click()
            a.get_by_role('button',name='Local file',exact=True).click()
            a.locator('.source-panel:visible input[type=file]').set_input_files(str(root/'tests/fixtures/stream-test.mp4'))
            a.get_by_role('button',name='Add file',exact=True).click()
            queued=a.get_by_role('button',name='Play stream-test.mp4')
            expect(queued).to_be_visible(timeout=14000)
            item=host.request.get(base+route).json()['queue'][0]
            assert peer.request.get(base+item['video'],headers={'Range':'bytes=10-19'}).status==206
            queued.click()
            a.locator('.task-screen[data-screen=queue] .screen-back').click(timeout=8000)
            for page in (a,b):
                page.wait_for_function("document.querySelector('.direct-video')?.currentTime > .1",timeout=14000)
            post(host,route,{'action':'playback','playing':False,'position':.6})
            for page in (a,b):
                page.wait_for_function("""()=>{const video=document.querySelector('.direct-video');return video?.paused&&Math.abs(video.currentTime-.6)<.3}""",timeout=12000)
            print('Browser upload, shared playback, byte-range fetching, pause and seek verified for two participants.')
            for c in (peer,host):post(c,route+'/leave',{})
    finally:
        server.close()


if __name__=='__main__':
    main()
