"""Message text direction and avatar node stability in two browser engines."""
import io
import os

from PIL import Image
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get('SOLACE_TEST_BASE', 'http://127.0.0.1:8017')
FIRST = 'Hi 😄✨'
SECOND = '2 smiles 👩🏽‍🚀🇮🇳'
RTL = 'أهلا 😄✨'


def post(context, path, data):
    csrf = context.request.get(BASE+'/api/me').json()['csrf']
    response = context.request.post(BASE+path, data=data, headers={'X-CSRF-Token':csrf})
    assert response.ok, (path,response.status)
    return response.json()


def check_text(bubble, text, direction):
    expect(bubble).to_have_text(text, use_inner_text=False)
    assert bubble.evaluate('p => getComputedStyle(p).direction') == direction
    assert bubble.evaluate('p => getComputedStyle(p).unicodeBidi') == 'isolate'
    assert bubble.evaluate("""p => {
      const node = p.firstChild;
      const initial = document.createRange();
      initial.setStart(node, 0);
      initial.setEnd(node, 1);
      const emoji = document.createRange();
      emoji.setStart(node, p.textContent.indexOf('😄'));
      emoji.setEnd(node, p.textContent.length);
      const a = initial.getBoundingClientRect(), b = emoji.getBoundingClientRect();
      return Math.abs(a.top-b.top) < 6 && (getComputedStyle(p).direction==='ltr' ? a.right <= b.left+1 : a.left >= b.right-1);
    }"""), 'Emoji must draw after the text in the message’s own direction'


def exercise(sender, receiver, selectors):
    input_selector, send_selector, rows_selector = selectors
    for page in [sender, receiver]:
        page.evaluate("document.documentElement.dir='rtl'")
    def send(text):
        matching=receiver.locator(rows_selector).filter(has_text=text)
        previous_count=matching.count()
        editor=sender.locator(input_selector)
        editor.fill('')
        editor.focus()
        editor.dispatch_event('compositionstart')
        # Native insertion uses UTF-16 offsets, including ZWJ and skin-tone sequences.
        for index,piece in enumerate((text[:3],text[3:])):
            sender.keyboard.insert_text(piece)
            assert editor.input_value()==(text[:3] if index==0 else text)
            assert editor.evaluate('e=>e.selectionStart===e.value.length && e.selectionEnd===e.value.length')
        editor.dispatch_event('compositionend')
        if input_selector=='#message':sender.wait_for_timeout(550) # Existing room flood limit is one send per 500 ms.
        sender.locator(send_selector).click()
        expect(editor).to_have_value('',timeout=10000)
        expect(matching).to_have_count(previous_count+1,timeout=10000)
        return matching.last
    first = send(FIRST)
    expect(first).to_be_visible()
    check_text(first.locator('p').first, FIRST, 'ltr')
    avatar = first.locator('.chat-photo')
    expect(avatar).to_be_visible(timeout=10000)
    avatar.evaluate('img => img.decode()')
    avatar.evaluate('img => { window.originalAvatar = img; window.originalAvatarSrc = img.getAttribute("src"); }')
    source=avatar.get_attribute('data-avatar-source')
    reloads=[]
    if source:receiver.on('request',lambda request: reloads.append(request.url) if request.url.endswith(source) else None)
    second = send(SECOND)
    expect(second.locator('p').first).to_have_text(SECOND)
    third = send(RTL)
    check_text(third.locator('p').first, RTL, 'rtl')
    expect(third.locator('.chat-photo')).to_be_visible(timeout=10000)
    assert avatar.evaluate('img => img===window.originalAvatar && img.complete && img.getAttribute("src")===window.originalAvatarSrc')
    if source:assert not reloads, 'New text messages must reuse the already loaded avatar'


with sync_playwright() as playwright:
    for engine in ('chromium','webkit'):
        browser = getattr(playwright,engine).launch()
        options = {**playwright.devices['Pixel 7']} if engine == 'chromium' else {**playwright.devices['iPhone 13']}
        options.pop('default_browser_type', None)
        one, two = [browser.new_context(**options) for _ in range(2)]
        sender, receiver = [ctx.new_page() for ctx in (one,two)]
        for page in (sender, receiver):
            page.route('https://www.youtube.com/**',lambda route:route.abort())
            page.add_init_script("Object.defineProperty(document,'hidden',{get:()=>false});")
        codes = []
        picture = io.BytesIO()
        Image.new('RGB',(48,48),'pink').save(picture,'PNG')
        for context,name in ((one,'Peach'),(two,'Berry')):
            post(context,'/api/me/profile',{'name':name})
            post(context,'/api/me/privacy',{'share_avatar':True})
            post(context,'/api/me/discovery',{'opted':True,'visible':False,'avatar':True,'stats':False,'bio':'','interests':''})
            post(context,'/api/me/preferences',{'world_choice':'joined','world_visible':False,'in_app_alerts':False})
            csrf=context.request.get(BASE+'/api/me').json()['csrf']
            assert context.request.post(BASE+'/api/me/avatar',headers={'X-CSRF-Token':csrf},multipart={'picture':{'name':'avatar.png','mimeType':'image/png','buffer':picture.getvalue()}}).ok
            codes.append(context.request.get(BASE+'/api/me/connections').json()['friend_code'])
        post(one,'/api/me/connections',{'action':'request','code':codes[1]})
        post(two,'/api/me/connections',{'action':'accept','code':codes[0]})
        for page,code in ((sender,codes[1]),(receiver,codes[0])):
            page.goto(BASE+'/messages?friend='+code)
        exercise(sender,receiver,('#dmText','#dmSend','.dm-message'))
        # Open World is shared within this disposable test server. Remove our prior run’s rows
        # from the locator matches by using only the latest matching messages.
        for page in (sender,receiver):
            page.goto(BASE+'/world')
            expect(page.locator('#worldOnline')).to_contain_text('online in Open World')
        exercise(sender,receiver,('#worldText','#worldSend','.world-message'))
        previous = receiver.locator('.world-message').filter(has_text=FIRST).last.locator('.chat-photo')
        removed=sender.locator('.world-message.mine').filter(has_text=SECOND).last
        removed_id=removed.get_attribute('data-id')
        removed.dispatch_event('contextmenu')
        sender.get_by_role('button',name='Delete message',exact=True).click()
        expect(receiver.locator('.world-message[data-id="'+removed_id+'"]').filter(has_text=SECOND)).to_have_count(0)
        assert previous.evaluate('img => img===window.originalAvatar && img.complete')

        room = post(one,'/api/rooms',{})['id']
        for page in (sender,receiver):
            # The new room bubble must render its avatar without the separate metadata poll.
            page.route('**/api/me/chat/actions?scope=room&*', lambda route: route.abort())
            page.goto(BASE+'/?room='+room)
            page.wait_for_selector('body.room-connected')
            page.evaluate("showScreen('watch')")
        exercise(sender,receiver,('#message','.send','.chat-message'))
        assert receiver.locator('.chat-message .chat-photo').count() == 3
        assert receiver.locator('.chat-message .chat-photo').first.evaluate(
            "img => img.getAttribute('src').startsWith('/api/me/chat/avatar/') && img.naturalWidth > 0"
        )
        post(one,'/api/me/privacy',{'share_avatar':False})
        sender.wait_for_timeout(550) # Respect the room's existing send cooldown.
        sender.locator('#message').fill('Private picture fallback')
        sender.locator('.send').click()
        fallback = receiver.locator('.chat-message').filter(has_text='Private picture fallback').locator('.chat-photo')
        expect(fallback).to_have_attribute('src','/static/default-avatar.svg')
        assert fallback.evaluate('img => img.getBoundingClientRect().width') == 28
        for context in (one,two):post(context,'/api/rooms/'+room+'/leave',{})
        print(engine, 'stable message order, room avatars without metadata polls, and private fallback passed',flush=True)
        browser.close()
