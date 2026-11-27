"""Visual regression: theme buttons must not paint over the video iframe.

Run against the disposable server from tests/run_local_smoke.py --serve.
This tests compositing and controls, not YouTube network availability or codecs.
"""
import io
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('SOLACE_TEST_URL', 'http://127.0.0.1:8017')


def main():
    with sync_playwright() as p:
        for engine in ('chromium', 'webkit'):
            browser = getattr(p, engine).launch()
            context = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True)
            page = context.new_page()
            clip=(Path(__file__).with_name('fixtures')/'stream-test.mp4').read_bytes()
            def serve_clip(route):
                headers={'Content-Type':'video/mp4','Accept-Ranges':'bytes'}
                requested=route.request.headers.get('range','')
                if not requested.startswith('bytes='):
                    route.fulfill(body=clip,headers=headers)
                    return
                first,last=requested[6:].split('-',1)
                start=int(first or 0)
                end=min(int(last) if last else len(clip)-1,len(clip)-1)
                if start>end:
                    route.fulfill(status=416,headers={**headers,'Content-Range':f'bytes */{len(clip)}'})
                    return
                headers['Content-Range']=f'bytes {start}-{end}/{len(clip)}'
                route.fulfill(status=206,body=clip[start:end+1],headers=headers)
            page.route(BASE+'/ambient-test.mp4',serve_clip)
            page.route('https://www.youtube.com/**', lambda route: route.fulfill(content_type='text/html', body='<html style="height:100%;background:rgb(37,175,99)"><body style="margin:0;height:100%"></body></html>'))

            def post(path, data):
                csrf = context.request.get(BASE + '/api/me').json()['csrf']
                response = context.request.post(BASE + path, data=data, headers={'X-CSRF-Token': csrf})
                assert response.ok, (path, response.status)
                return response.json()

            post('/api/me/profile', {'name': 'Visibility QA'})
            room = post('/api/rooms', {})['id']
            post('/api/rooms/' + room, {'action': 'join'})
            try:
                page.goto(BASE + '/?room=' + room, wait_until='domcontentloaded')
                page.wait_for_selector('body.room-connected')
                page.evaluate("showScreen('watch')")
                post('/api/rooms/' + room, {'action': 'playback', 'playing': True, 'position': 1})
                page.locator('.video-stage.ambient-playing').wait_for(timeout=5000)
                with page.expect_request('https://www.youtube.com/embed/**') as embed:
                    page.evaluate("""() => {
                        window.YT = {Player: class {}};
                        createYoutubePlayer({});
                        document.getElementById('playerPlaceholder').style.display = 'none';
                    }""")
                frame = page.locator('#player')
                expect(frame).to_have_attribute('referrerpolicy', 'strict-origin-when-cross-origin')
                assert parse_qs(urlsplit(embed.value.url).query)['origin'] == [BASE]
                assert embed.value.all_headers().get('referer') == BASE + '/'
                page.frame_locator('#player').locator('body').wait_for()
                for theme in ('solace', 'rose', 'ocean', 'forest'):
                    page.evaluate('(theme) => SolaceSiteTheme.apply(theme)', theme)
                    for width, height in ((390, 844), (844, 390)):
                        page.set_viewport_size({'width': width, 'height': height})
                        page.evaluate("document.body.classList.toggle('solace-fullscreen', innerWidth > innerHeight)")
                        stage = page.locator('.video-stage')
                        assert stage.evaluate("e => getComputedStyle(e).boxShadow === 'none'")
                        wrap=page.locator('.player-wrap')
                        assert wrap.evaluate("e => getComputedStyle(e, '::before').content!=='none'") == (height > width)
                        assert '/vi/aqz-KE-bpKQ/' in stage.evaluate("e => e.style.getPropertyValue('--ambient-thumbnail')")
                        assert page.locator('meta[name=theme-color]').get_attribute('content') == page.evaluate(
                            "() => document.documentElement.style.getPropertyValue('--site-chrome')")
                        if height > width:
                            chrome = page.locator('.watch-header').evaluate("e => getComputedStyle(e).backgroundColor")
                            assert page.locator('.bottom-nav').evaluate("e => getComputedStyle(e).backgroundColor") == chrome
                        surface = page.locator('.player-touch-surface')
                        expect(surface).to_be_visible()
                        assert surface.evaluate("e => getComputedStyle(e).backgroundColor === 'rgba(0, 0, 0, 0)'")
                        frame = page.locator('#player')
                        expect(frame).to_be_visible()
                        rect = frame.bounding_box()
                        picture = Image.open(io.BytesIO(page.screenshot()))
                        color = picture.getpixel((int(rect['x'] + rect['width'] / 2), int(rect['y'] + rect['height'] / 2)))[:3]
                        assert color == (37, 175, 99), (engine, theme, width, color)
                        surface.click()
                        expect(page.locator('.compact-player-controls')).to_have_attribute('aria-hidden', 'false')
                page.set_viewport_size({'width':390,'height':844})
                if engine=='chromium': # The Windows WebKit test runtime cannot decode this MP4 fixture.
                    page.evaluate("""async () => {
                        document.body.classList.remove('solace-fullscreen');
                        const direct = document.createElement('video');
                        direct.className = 'direct-video';
                        direct.muted = true;
                        direct.playsInline = true;
                        direct.src = '/ambient-test.mp4';
                        document.querySelector('.player-wrap').append(direct);
                        await direct.play();
                    }""")
                    page.wait_for_function("document.querySelector('.video-stage').style.getPropertyValue('--ambient-left') !== ''",timeout=7000)
                assert page.locator('.bottom-nav .solace-icon').first.evaluate('e => e.getBBox().width > 0')
                post('/api/rooms/' + room, {'action': 'playback', 'playing': False, 'position': 2})
                page.locator('.video-stage.ambient-playing').wait_for(state='hidden',timeout=5000)
                for width in (320,390):
                    page.set_viewport_size({'width':width,'height':720})
                    page.evaluate("showScreen('watch')")
                    page.locator('.bottom-nav [data-target=room]').click()
                    iframe=page.frame_locator('.inline-room-tools')
                    expect(iframe.locator('#hostControls')).to_be_visible()
                    assert iframe.locator('html').evaluate("e => e.scrollWidth<=e.clientWidth+1")
                    assert iframe.locator('#roomSettingsForm select').first.evaluate(
                        "e => e.getBoundingClientRect().left>=0 && e.getBoundingClientRect().right<=innerWidth")
                    assert page.locator('.inline-room-tools').evaluate(
                        "e => e.getBoundingClientRect().left>=0 && e.getBoundingClientRect().right<=innerWidth")
                page.evaluate("showScreen('watch')")

                Path('.openai').mkdir(exist_ok=True)
                page.screenshot(path='.openai/video-visible-' + engine + '.png')
                print(engine + ': four themes, portrait/landscape, theme chrome, ambient playback and transparent controls passed', flush=True)
            finally:
                post('/api/rooms/' + room + '/leave', {})
                browser.close()


if __name__ == '__main__':
    main()
