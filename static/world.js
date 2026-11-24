'use strict';
(()=>{document.body.classList.add('world-page');const $=id=>document.getElementById(id),{request:req,make,id,mediaNode,attachment}=SolaceMedia;let queuedReset=false,viewingHistory=false,awaitingChoice=false,canSend=false,preferences=null,busy=false,sending=false,after=0,oldest=0,revision=null,pending=null,gone=false,visible=true,pulseBusy=false;const tab=id(),attach=attachment($('worldAttachment'),$('worldPreview'),{scope:'world',scopeId:()=> 'community'});function status(text){$('worldStatus').textContent=text}const viewport = SolaceSocialChat.mount(document.querySelector('.world-chat'), $('worldHistory'), $('worldText'), $('worldComposer'), $('worldSend'));
function row(m) {
 const el = make('article', undefined, 'world-message '+(m.you?'mine':'received'));
 el.dataset.id = m.id;
 const avatar=make('img',undefined,'chat-photo');avatar.alt='';avatar.width=28;avatar.height=28;avatar.loading='eager';
 if(m.code)avatar.dataset.profileCode=m.code;
 if(window.SolaceAvatars)SolaceAvatars.paint(avatar,m.avatar||'/static/default-avatar.svg');else avatar.src=m.avatar||'/static/default-avatar.svg';
 el.append(avatar);

 el.dataset.contentKey = contentKey(m);
 const bubble = make('div', undefined, 'social-bubble');
 if (!m.you) {
  const name = make('button', m.name, 'profile-link');
  name.type = 'button';
  if (m.code) name.dataset.profileCode = m.code; else name.disabled = true;
  bubble.append(name);
 }
 const body = make('p', m.deleted?'Message removed':m.body);
 body.dir = 'auto';
 bubble.append(body);
 if (m.media && !m.deleted) bubble.append(mediaNode(m.media));
 const meta = make('footer');
 const time = make('time', new Date(m.created*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}));
 time.dateTime = new Date(m.created*1000).toISOString();
 meta.append(time);
 bubble.append(meta);
 el.append(bubble);
 if (m.you && !m.deleted) SolaceSocialChat.setActions(el, async () => {
  await req('/api/me/world/messages/'+m.id,null,'DELETE');
  await load(true);
 }, m.media && !m.media.deleted ? async () => {
  await req('/api/me/media/'+m.media.id,null,'DELETE');
  await load(true);
 } : null, status);
 return el;
}
function contentKey(m) {
 return JSON.stringify([m.body,m.deleted,m.name,m.code,m.media?.id,m.media?.deleted,m.you]);
}
function paint(messages, reset=false, prepend=false) {
 const anchor = viewport.capture();
 const list = $('worldMessages');
 const existing = new Map([...list.querySelectorAll('[data-id]')].map(node => [node.dataset.id,node]));
 list.querySelector('.world-empty')?.remove();
 if (reset) {
  viewingHistory = false;
  $('worldLatest').hidden = true;
  after = 0;
  const wanted = new Set(messages.map(m => String(m.id)));
  for (const [id, node] of existing) if (!wanted.has(id)) node.remove();
 }
 const batch = document.createDocumentFragment();
 for (const m of messages) {
  const old = existing.get(String(m.id));
  if (!old) batch.append(row(m));
  else if (old.dataset.contentKey !== contentKey(m)) old.replaceWith(row(m));
  after = Math.max(after,m.id);
 }
 if (prepend) list.prepend(batch);
 else if (!reset) list.append(batch);
 else {
  // Full snapshots can overlap the retained rows. Merge new rows in server order.
  const added = new Map([...batch.children].map(node => [node.dataset.id,node]));
  const current = new Map([...list.children].map(node => [node.dataset.id,node]));
  messages.forEach((m, index) => {
   const id = String(m.id);
   const node = current.get(id) || added.get(id);
   if (list.children[index] !== node) list.insertBefore(node,list.children[index] || null);
  });
 }
 while (list.children.length > 250) {
  (prepend ? list.lastElementChild : list.firstElementChild).remove();
 }
 oldest = Number(list.firstElementChild?.dataset.id || 0);
 if (!list.children.length) list.append(make('p','No messages yet.','world-empty'));
 viewport.restore(anchor);
}
async function load(reset=false){if(reset)queuedReset=true;if(gone||busy||awaitingChoice||document.hidden||!visible||!preferences||preferences.world_choice==='unset')return;reset=queuedReset;queuedReset=false;busy=true;try{const full=reset||!after,d=await req('/api/me/world/messages'+(!reset&&after?'?after='+after:''));if(revision!==null&&revision!==d.revision&&!reset){queuedReset=true;return}revision=d.revision;canSend=!!d.can_send;if(preferences&&['joined','skipped','unset'].includes(d.participation))preferences.world_choice=d.participation;if(viewingHistory&&!reset){for(const m of d.messages)after=Math.max(after,m.id)}else paint(d.messages,reset);$('worldOnline').textContent=d.online+' online in Open World';if(full)$('worldOlder').hidden=!d.has_more;if(preferences){$('worldComposer').hidden=preferences.world_choice!=='joined';$('worldJoinLater').hidden=preferences.world_choice==='joined';$('worldSend').disabled=!d.can_send||sending;$('worldText').disabled=!d.can_send;attach.lock(!d.can_send||sending)}if(!pending)status(!d.can_send&&preferences?.world_choice==='joined'?'Sending is paused. You can still read.':'')}catch(e){status(navigator.onLine?e.message:'You’re offline. Your draft stays here.');if(e.status===403){canSend=false;$('worldSend').disabled=true;$('worldText').disabled=true;attach.lock(true)}$('worldOnline').textContent='Reconnecting…'}finally{busy=false;if(queuedReset&&!gone&&visible&&!document.hidden)queueMicrotask(()=>load())}}
async function pulse(active=!document.hidden&&visible){if(gone||pulseBusy||!preferences||preferences.world_choice!=='joined')return;pulseBusy=true;try{await req('/api/me/world/presence',{tab,active})}catch{}finally{pulseBusy=false}}
async function choose(choice){try{preferences=(await req('/api/me/preferences',{world_choice:choice})).preferences;awaitingChoice=false;$('worldChoice').hidden=true;await pulse();await load(true)}catch(e){status(e.message)}}$('worldJoin').onclick=()=>choose('joined');$('worldSkip').onclick=()=>choose('skipped');$('worldJoinLater').onclick=()=>choose('joined');
$('worldLatest').onclick=()=>{viewport.jump();load(true)};$('worldOlder').onclick=async()=>{if(busy)return;busy=true;viewingHistory=true;viewport.pause();$('worldLatest').hidden=false;try{const d=await req('/api/me/world/messages?before='+oldest);paint(d.messages,false,true);$('worldOlder').hidden=!d.has_more}catch(e){status(e.message)}finally{busy=false}};
async function send(){if(sending||!canSend||!preferences||preferences.world_choice!=='joined')return;const text=$('worldText').value.trim();if(!pending&&!text&&!attach.has())return;sending=true;attach.lock(true);$('worldSend').disabled=true;try{if(!pending){const media=await attach.upload();pending={body:text,media_id:media||null,request_id:id()}}const sent=pending;await req('/api/me/world/messages',sent);if($('worldText').value.trim()===sent.body)$('worldText').value='';attach.sent(sent.media_id);pending=null;$('worldRetry').hidden=true;viewport.jump();await load()}catch(e){status(e.message);$('worldRetry').hidden=!pending}finally{sending=false;attach.lock(!canSend);$('worldSend').disabled=!canSend;viewport.layout()}}
$('worldComposer').onsubmit=e=>{e.preventDefault();send()};$('worldRetrySend').onclick=send;$('worldDiscard').onclick=()=>{pending=null;$('worldRetry').hidden=true;status('')};$('worldText').addEventListener('focus',()=>{if(viewingHistory)load(true)});document.addEventListener('visibilitychange',()=>{pulse();if(!document.hidden)load()});window.addEventListener('solace-surface',e=>{visible=e.detail.visible;pulse();if(visible)load()});window.addEventListener('offline',()=>{status('You’re offline. Your draft stays here.');$('worldOnline').textContent='Offline'});window.addEventListener('online',()=>{load();pulse()});const timer=setInterval(()=>{load()},1200),heartbeat=setInterval(()=>pulse(),15000);window.addEventListener('pagehide',()=>{gone=true;clearInterval(timer);clearInterval(heartbeat);navigator.sendBeacon?.('/api/me/world/presence',new URLSearchParams({csrf:document.querySelector('meta[name=account-csrf]').content,tab,active:'0'}))});
req('/api/me/preferences').then(async d=>{preferences=d.preferences;if(preferences.world_choice==='unset'){const intro=await req('/api/me/preferences/world-intro',{});preferences=intro.preferences;$('worldChoice').hidden=!intro.show_intro;if(intro.show_intro){awaitingChoice=true;status('');$('worldOnline').textContent='Your space, your choice.';return}}awaitingChoice=false;$('worldChoice').hidden=true;await pulse();await load(true)}).catch(e=>status(e.message));viewport.layout();})();
