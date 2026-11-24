const providerPages = new Set(['youtu.be','www.youtu.be','youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','netflix.com','www.netflix.com']);
function isLocalVideo(value){return typeof value==='string'&&/^\/api\/room-video\/[a-f0-9]{32}$/.test(value)}
function isDirectUrl(value){
 if(isLocalVideo(value))return true;
 if(typeof value!=='string'||value.length>8192||!value.startsWith('https://'))return false;
 try{const url=new URL(value);return !url.username&&!url.password&&!url.hash&&!providerPages.has(url.hostname)}catch{return false}
}
function localPlayerPage(source,origin){
 if(!isLocalVideo(source))return null;
 return new URL('/room-video-player/'+source.split('/').pop(),origin).href;
}
function mediaPage(value){return isDirectUrl(value)?value:'https://m.youtube.com/watch?v='+value}
function directHtml(value){if(!isDirectUrl(value)||isLocalVideo(value))throw Error('Use a direct HTTPS video URL');const encoded=JSON.stringify(value).replace(/</g,'\\u003c');return '<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{margin:0;width:100%;height:100%;background:#000}video{width:100%;height:100%;object-fit:contain}</style><video playsinline controls preload="metadata"></video><script>window.__solaceDirectSource='+encoded+';document.querySelector("video").src=window.__solaceDirectSource;</script></html>'}
module.exports={isDirectUrl,isLocalVideo,localPlayerPage,mediaPage,directHtml};
