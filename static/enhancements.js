// Keep mutations in order and ignore room snapshots older than the current one.
const renderBase=render;
render=function(data){if(SolaceSession.isOlderSnapshot(state,data))return;renderBase(data);renderPeople(data);renderReorder(data);$('next').disabled=!data.can_control||data.queue.length===0;$('toggle').textContent=data.playing?'Ⅱ Pause':'▶ Play';$('claimHost').hidden=true;};
function renderReorder(data){if(!data.can_control)return;const rows=$('queue').querySelectorAll('.queue-item');rows.forEach((row,index)=>{if(row.querySelector('.queue-move'))return;const item=data.queue[index];const controls=element('div','queue-move');for(const direction of ['up','down']){const button=element('button','subtle',direction==='up'?'↑':'↓');button.setAttribute('aria-label','Move '+item.title+' '+direction);button.title='Move '+direction;button.disabled=direction==='up'?index===0:index===data.queue.length-1;button.onclick=async()=>{button.disabled=true;await action({action:'move',id:item.id,direction});button.disabled=direction==='up'?index===0:index===data.queue.length-1};controls.append(button)}row.insertBefore(controls,row.querySelector('button'))})}
let pendingActions=Promise.resolve(),queuedCommands=0;
action=function(data){if(!state||!roomId){openSetup();return Promise.resolve(null)}if(queuedCommands>=12){toast('Please wait for the current updates to finish.');return Promise.resolve(null)}queuedCommands++;const task=pendingActions.then(async()=>{try{const result=await api('/api/rooms/'+encodeURIComponent(roomId),data);render(result);return result}catch(error){toast(error.message);return null}finally{queuedCommands--}});pendingActions=task.catch(()=>{});return task};
const claim=element('button','subtle','Become host');claim.id='claimHost';claim.hidden=true;claim.onclick=()=>action({action:'claim_host'});document.querySelector('.toolbar>div').append(claim);
let peopleSignature='';
function renderPeople(data){const signature=JSON.stringify(data.members)+data.is_host;if(signature===peopleSignature&&$('people').childElementCount)return;peopleSignature=signature;$('people').replaceChildren(...data.members.map(member=>{const row=element('div','person');row.append(element('span','mini-avatar',member.name.slice(0,2).toUpperCase()),element('span','',member.name+(member.you?' (you)':'')+''));if(false){const button=element('button','subtle','Make host');button.onclick=()=>action({action:'transfer_host',member_id:member.member_id});row.append(button)}return row}))}
// The older text-only member summary is replaced after every render.
const peopleButton=$('peopleButton');peopleButton.setAttribute('aria-expanded','false');peopleButton.onclick=()=>{$('people').hidden=!$('people').hidden;peopleButton.setAttribute('aria-expanded',String(!$('people').hidden))};

// A persistent, actionable playback notice rather than a disappearing toast.
const playerNotice=element('div','player-notice');playerNotice.id='playerNotice';playerNotice.hidden=true;
playerNotice.innerHTML='<div><strong id="playerNoticeTitle"></strong><p id="playerNoticeText"></p></div><button class="subtle" id="retryYoutube">Retry player</button><a id="watchOnYoutube" class="subtle" target="_blank" rel="noopener">Open YouTube ↗</a>';
document.querySelector('.player-wrap').after(playerNotice);
function playbackNotice(title,message){$('playerNoticeTitle').textContent=title;$('playerNoticeText').textContent=message;$('watchOnYoutube').href='https://www.youtube.com/watch?v='+encodeURIComponent(state?.video||'');playerNotice.hidden=false}
initPlayer=function(){if(player||!state)return;player=new YT.Player('player',{videoId:state.video,playerVars:{playsinline:1,rel:0,controls:0,disablekb:1,fs:0,iv_load_policy:3,origin:location.origin},events:{
 onReady:()=>{if(isDirectSource(state?.video)){savedYoutubeReady=true;return}ready=true;$('playerPlaceholder').style.display='none';playerNotice.hidden=true;synchronize(true)},
 onStateChange:event=>{if(isDirectSource(state?.video))return;if(event.data===1)playerNotice.hidden=true;if(event.data===0){advanceFinishedVideo();return}if(event.data===3||event.data===-1)youtubeSync.stableSince=null;if(event.data===1)youtubeSync.autoplayBlocked=false},
 onAutoplayBlocked:()=>{youtubeSync.autoplayBlocked=true;if(!isDirectSource(state?.video))playbackNotice('Click the video to start','Your browser needs a click before playing sound. Use the play button in the video.')},
 onError:event=>{if(isDirectSource(state?.video))return;const errors={2:['Invalid video','Check the video link or choose another video.'],5:['Playback could not start','Retry the player or try another browser.'],100:['Video unavailable','This video may be private or removed. Choose another video.'],101:['This video needs the phone app','YouTube blocked embedded playback in this browser. Open it in the installed Solace app or on YouTube.'],150:['This video needs the phone app','YouTube blocked embedded playback in this browser. Open it in the installed Solace app or on YouTube.'],153:['YouTube could not verify this player','Open the app at its local web address, rather than opening its HTML file.']};playbackNotice(...(errors[event.data]||['Playback unavailable','Retry or choose another video.']))}
}})};
window.onYouTubeIframeAPIReady=()=>initPlayer();
let youtubeTimer;
loadYoutube=function(){if(window.YT?.Player){initPlayer();return}if(document.getElementById('yt-script'))return;const script=element('script');script.id='yt-script';script.src='https://www.youtube.com/iframe_api';script.onerror=()=>{if(!isDirectSource(state?.video))playbackNotice('YouTube could not load','Check your internet connection or content blocker, then retry.')};document.head.append(script);clearTimeout(youtubeTimer);youtubeTimer=setTimeout(()=>{if(!ready&&!isDirectSource(state?.video))playbackNotice('Still waiting for YouTube','Check your connection or content blocker, then retry.')},18000)};
$('retryYoutube').onclick=()=>{if(!state)return;ready=false;lastVideo='';if(player){player.destroy();player=null}if(!$('player')){const node=element('div');node.id='player';document.querySelector('.player-wrap').prepend(node)}$('playerPlaceholder').style.display='flex';playerNotice.hidden=true;document.getElementById('yt-script')?.remove();loadYoutube()};
const youtubeSync = {
 command: null, stableSince: null, lastSeek: -Infinity,
 lastPlay: -Infinity, autoplayBlocked: false
};

synchronize = function(force = false) {
 if (!ready || !state || !pageRecovery.active) return;
 const direct = isDirectSource(state.video);
 const target = Math.max(0, state.position + (state.playing ? Math.max(0, state.server_time - state.updated) : 0));
 if (lastVideo !== state.video || lastPlaybackId !== state.playback_id) {
  applyingUntil = Date.now() + 650;
  lastVideo = state.video;
  lastPlaybackId = state.playback_id;
  Object.assign(youtubeSync, {command: state.command_revision,
   stableSince: null, lastSeek: -Infinity, lastPlay: performance.now(), autoplayBlocked: false});
  playerNotice.hidden = true;
  $('videoTitle').textContent = 'Loading video…';
  const options = {videoId: state.video, startSeconds: target};
  if (state.playing) player.loadVideoById(options);
  else player.cueVideoById(options);
  return;
 }
 if (!force && (controlBusy || Date.now() < localControlUntil && state.updated < (confirmedControlUpdated || Infinity))) return;
 const mode = player.getPlayerState();
 const video = player.getVideoData();
 const duration = player.getDuration();
 const position = player.getCurrentTime();
 if (!direct && video?.video_id !== state.video) return;
 const now = performance.now();
 const command = state.command_revision !== youtubeSync.command;
 if (!direct && (mode === 3 || mode === -1)) {
  youtubeSync.stableSince = null;
  if (!state.playing && mode === 3) player.pauseVideo();
  return;
 }
 if (!state.playing && mode === 0 && (!direct || !command && !force)) {
  youtubeSync.command = state.command_revision;
  return;
 }
 if (!direct && mode === 1) youtubeSync.stableSince ??= now;
 else youtubeSync.stableSince = null;
 const drift = Math.abs(position - target);
 const steady = youtubeSync.stableSince !== null && now - youtubeSync.stableSince >= 4000;
 const correctiveSeek = direct
  ? video?.canCorrect !== false && drift > (video?.driftTolerance || .5)
  : !state.is_host && steady && drift > 4 && now - youtubeSync.lastSeek >= 12000;
 const requestedSeek = (force || command) && drift > (direct ? .15 : .75);
 if (!video?.isLive && video?.canSeek !== false && duration > 0 && Number.isFinite(position)
     && (correctiveSeek || requestedSeek)) {
  applyingUntil = Date.now() + 750;
  youtubeSync.lastSeek = now;
  youtubeSync.stableSince = null;
  player.seekTo(Math.min(Math.max(0, duration - .1), target), true);
 }
 if ((command || force) && (duration <= 0 || direct && drift > .15 && video?.canSeek === false)) {
  if (direct && !state.playing) player.pauseVideo();
  return;
 }
 youtubeSync.command = state.command_revision;
 if (!state.playing && (mode === 1 || direct && mode === 3)) {
  applyingUntil = Date.now() + 750;
  player.pauseVideo();
 } else if (state.playing && ![1, 3].includes(mode)
     && (direct || !youtubeSync.autoplayBlocked && now - youtubeSync.lastPlay >= 5000)) {
  applyingUntil = Date.now() + 750;
  youtubeSync.lastPlay = now;
  player.playVideo();
 }
};

// Restore the room on refresh without asking for the same name again.
if(roomId&&displayName){api('/api/rooms/'+encodeURIComponent(roomId),{action:'join',name:displayName}).then(result=>{joining=false;$('setup').hidden=true;render(result);loadYoutube()}).catch(error=>{toast(error.message);if(error.status===404){roomId=null;history.replaceState({},'','/');openSetup()}})}
window.addEventListener('offline',()=>{$('connection').textContent='● Offline'});
// Categories query YouTube when connected; featured categories remain usable offline.

$('chatForm').onsubmit=async event=>{event.preventDefault();if(!state){openSetup();return}const body=$('message').value.trim();if(!body)return;const send=$('chatForm').querySelector('button');send.disabled=true;try{if(await action({action:'chat',body})){if($('message').value.trim()===body)$('message').value='';$('messages').scrollTop=$('messages').scrollHeight}}finally{send.disabled=false}};
$('toggle').onclick=()=>{if(!ready){toast('Wait for the player to load.');return}if(!state?.can_control)return;if(player.getPlayerState()===1)player.pauseVideo();else player.playVideo()};
$('sync').onclick=()=>{if(!ready){toast('Wait for the player to load.');return}synchronize(true);toast('Synced to the room.')};
document.querySelectorAll('.reactions button').forEach(button=>{const labels={'❤️':'Send heart','🔥':'Send fire','😂':'Send laugh','👏':'Send applause','✨':'Send sparkles'};button.setAttribute('aria-label',labels[button.textContent]||'Send reaction');button.onclick=async()=>{if(button.disabled)return;button.disabled=true;await action({action:'chat',body:button.textContent});setTimeout(()=>button.disabled=false,650)}});
