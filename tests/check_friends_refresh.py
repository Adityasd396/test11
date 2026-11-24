from playwright.sync_api import sync_playwright,expect
BASE='http://127.0.0.1:8017'
def post(c,path,data):
 token=c.request.get(BASE+'/api/me').json()['csrf']
 r=c.request.post(BASE+path,data=data,headers={'X-CSRF-Token':token})
 assert r.ok,(path,r.status,r.text())
 return r.json()
with sync_playwright() as p:
 for engine in ('chromium','webkit'):
  b=getattr(p,engine).launch()
  a,c=[b.new_context(viewport={'width':390,'height':844}) for _ in range(2)]
  for ctx,name in ((a,'Moon friend'),(c,'Sunny friend')):post(ctx,'/api/me/profile',{'name':name})
  codeA=a.request.get(BASE+'/api/me/connections').json()['friend_code'];codeC=c.request.get(BASE+'/api/me/connections').json()['friend_code']
  post(a,'/api/me/connections',{'action':'request','code':codeC});post(c,'/api/me/connections',{'action':'accept','code':codeA})
  cid=post(a,'/api/me/conversations',{'code':codeC})['id']
  post(a,'/api/me/conversations/'+cid,{'body':'A new message for you','request_id':'friends-preview-1'})
  page=c.new_page();page.goto(BASE+'/friends')
  row=page.locator('#homeFriends .home-person').filter(has_text='Moon friend')
  expect(row.locator('.friend-preview-text')).to_have_text('A new message for you')
  expect(row.locator('.unread-count')).to_have_text('1')
  expect(row.locator('time')).not_to_be_empty()
  row.locator('img').evaluate('img=>window.savedFriendAvatar=img')
  post(a,'/api/me/conversations/'+cid,{'body':'Latest preview arrives','request_id':'friends-preview-2'})
  expect(row.locator('.friend-preview-text')).to_have_text('Latest preview arrives',timeout=22000)
  expect(row.locator('.unread-count')).to_have_text('2')
  assert row.locator('img').evaluate('img=>img===window.savedFriendAvatar')
  for width,height in ((390,844),(844,390),(1280,800)):
   page.set_viewport_size({'width':width,'height':height})
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
   page.screenshot(path='.openai/friends-new-'+engine+'-'+str(width)+'.png',full_page=True)
  row.locator('.friend-preview-text').click()
  expect(page.locator('#dmName')).to_have_text('Moon friend')
  print(engine,'friends preview, unread, timestamps, avatar retention and three viewport layouts passed',flush=True)
  b.close()
