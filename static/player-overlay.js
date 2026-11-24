let overlayRoom=null;
const overlayVisibility=new SolaceSession.AutoHideControls(3000);
const touchSurface=element('button','player-touch-surface');touchSurface.type='button';touchSurface.setAttribute('aria-label','Show or hide playback controls');videoStage.append(touchSurface);
let ambientEnabled=true,ambientSource=null,ambientVideo=null,ambientSampler=null,lastAmbientSample=0;
function paintRoomAmbient(){
 const visible=ambientEnabled && Boolean(state?.playing && state.video) && !document.hidden;
 videoStage.classList.toggle('ambient-playing',visible);
 if(!visible)return;
 if(ambientSource!==state.video){
  ambientSource=state.video;
  ambientVideo=null;
  ambientSampler=null;
  lastAmbientSample=0;
  for(const name of ['--ambient-left','--ambient-right'])videoStage.style.removeProperty(name);
  if(/^[A-Za-z0-9_-]{11}$/.test(ambientSource)){
   videoStage.style.setProperty('--ambient-thumbnail','url("https://i.ytimg.com/vi/'+ambientSource+'/mqdefault.jpg")');
  }else{
   videoStage.style.removeProperty('--ambient-thumbnail');
  }
 }
}
function sampleRoomAmbient(){
 if(!videoStage.classList.contains('ambient-playing') || !picker.hidden || document.body.dataset.screen!=='watch' ||
    document.body.classList.contains('solace-fullscreen') || document.body.classList.contains('keyboard-open') ||
    document.hidden || performance.now()-lastAmbientSample<650)return;
 const video=videoStage.querySelector('video.direct-video');
 if(!video || video.readyState<2 || video.paused || video.ended)return;
 if(ambientVideo!==video || ambientSampler?.src!==video.currentSrc){
  ambientVideo=video;
  const canvas=document.createElement('canvas');
  canvas.width=12;
  canvas.height=8;
  ambientSampler={src:video.currentSrc,ctx:canvas.getContext('2d',{willReadFrequently:true})};
 }
 if(!ambientSampler.ctx)return;
 lastAmbientSample=performance.now();
 try{
  const {ctx}=ambientSampler;
  ctx.drawImage(video,0,0,12,8);
  for(const [column,name] of [[0,'--ambient-left'],[6,'--ambient-right']]){
   const pixels=ctx.getImageData(column,1,6,6).data;
   const totals=[0,0,0];
   for(let i=0;i<pixels.length;i+=4)
    for(let channel=0;channel<3;channel++)totals[channel]+=pixels[i+channel];
   const color=totals.map(total=>Math.round(total/36));
   videoStage.style.setProperty(name,'rgb('+color.join(',')+')');
  }
 }catch{
  // A cross-origin video can still play even when its frames cannot be read.
  // Keep playback untouched and stop sampling this source.
  ambientSampler.ctx=null;
 }
}
function restoreRoomAmbient(){
 try{ambientEnabled=localStorage.getItem('solace-ambient')!=='false'}catch{ambientEnabled=true}
 paintRoomAmbient();
}
window.addEventListener('solace-preferences-restored',restoreRoomAmbient);
window.addEventListener('storage',event=>{if(event.key==='solace-ambient'||event.key===null)restoreRoomAmbient()});
document.addEventListener('visibilitychange',paintRoomAmbient);
restoreRoomAmbient();
function paintControls(visible){visible=document.body.dataset.screen==='watch'&&picker.hidden&&(window.matchMedia('(orientation: portrait)').matches&&!document.body.classList.contains('solace-fullscreen')||visible);videoStage.classList.toggle('controls-visible',visible);controls.setAttribute('aria-hidden',String(!visible));fullscreen.textContent=document.body.classList.contains('solace-fullscreen')?'⛶ Exit fullscreen':'⛶ Fullscreen'}
function revealControls(){paintControls(overlayVisibility.show())}
touchSurface.onclick=revealControls;
videoStage.addEventListener('pointermove',event=>{if(event.pointerType==='mouse')revealControls()});
videoStage.addEventListener('keydown',revealControls);
controls.addEventListener('pointerdown',()=>{overlayVisibility.focused=false;revealControls()});
controls.addEventListener('focusin',event=>{overlayVisibility.focused=event.target.matches(':focus-visible');revealControls()});
controls.addEventListener('focusout',()=>{overlayVisibility.focused=false;overlayVisibility.show()});
// Let a blocked YouTube player receive the first required media gesture directly.
const overlayRender=render;render=function(data){overlayRender(data);paintRoomAmbient();videoStage.classList.toggle('needs-media-gesture',!playerNotice.hidden);paintControls(overlayVisibility.update(!!data.playing));if(overlayRoom!==data.id){overlayRoom=data.id;syncLandscape()}};
setInterval(()=>{if(document.hidden)return;sampleRoomAmbient();videoStage.classList.toggle('needs-media-gesture',!playerNotice.hidden);paintControls(overlayVisibility.update(!!state?.playing))},250);
let landscapeExpanded=false;
const landscapeQuery=window.matchMedia('(orientation: landscape) and (pointer: coarse)');
function physicalPortrait(){const deviceScreen=window.screen;const type=deviceScreen?.orientation?.type;return type?type.startsWith('portrait'):!!deviceScreen&&deviceScreen.width<deviceScreen.height}
function syncLandscape(){
 if(!state||typeof activeScreen!=='undefined'&&activeScreen!=='watch')return;
 if(window.SolaceExperience?.autoFullscreen!==false&&landscapeQuery.matches&&!physicalPortrait()&&!document.body.classList.contains('solace-fullscreen')&&document.activeElement!==$('message')&&!document.body.classList.contains('keyboard-open')&&!document.body.classList.contains('gesture-picker-open')){landscapeExpanded=true;document.body.classList.add('solace-fullscreen');revealControls()}
 else if((!landscapeQuery.matches||physicalPortrait())&&landscapeExpanded){landscapeExpanded=false;exitSolaceFullscreen();revealControls()}
 fullscreen.textContent=document.body.classList.contains('solace-fullscreen')?'⛶ Exit fullscreen':'⛶ Fullscreen';
}
landscapeQuery.addEventListener('change',syncLandscape);
const existingFullscreenClick=fullscreen.onclick;fullscreen.onclick=async()=>{landscapeExpanded=false;await existingFullscreenClick();fullscreen.textContent=document.body.classList.contains('solace-fullscreen')?'⛶ Exit fullscreen':'⛶ Fullscreen';revealControls()};
revealControls();

// The picker must never share the playback controls' hit-testing layer.
new MutationObserver(()=>paintControls(overlayVisibility.visible)).observe(picker,{attributes:true,attributeFilter:['hidden']});
window.matchMedia('(orientation: portrait)').addEventListener('change',()=>paintControls(overlayVisibility.show()));
