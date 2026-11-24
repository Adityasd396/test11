(() => {
 class SourceError extends Error {
  constructor(message, canTryDirect = false) {
   super(message);
   this.canTryDirect = canTryDirect;
  }
 }

 const csrf = () => document.querySelector('meta[name="account-csrf"]').content;

 async function resolve(url, signal, tryDirect = false) {
  const response = await fetch('/api/media/resolve', {
   method: 'POST', cache: 'no-store', signal,
   headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf()},
   body: JSON.stringify({url, try_direct: tryDirect})
  });
  const data = await response.json();
  if (!response.ok) throw new SourceError(data.error || 'Unable to check the link.', data.can_try_direct);
  return data;
 }

 function connectUpload(form, room, onUploaded) {
  const input = form.querySelector('input[type=file]');
  const status = form.querySelector('[role=status]');
  const submit = form.querySelector('[type=submit]');
  const cancel = form.querySelector('[type=button]');
  const progress = form.querySelector('progress');
  let active = null;
  cancel.onclick = () => active?.abort();
  form.onsubmit = event => {
   event.preventDefault();
   const file = input.files[0], target = room();
   if (!file || !target || active) return;
   const xhr = new XMLHttpRequest();
   active = xhr;
   xhr.open('POST', '/api/me/room-video/' + encodeURIComponent(target));
   xhr.responseType = 'json';
   xhr.setRequestHeader('Content-Type', 'application/octet-stream');
   xhr.setRequestHeader('X-CSRF-Token', csrf());
   submit.disabled = input.disabled = true;
   cancel.hidden = progress.hidden = false;
   progress.value = 0;
   status.textContent = 'Uploading video…';
   xhr.upload.onprogress = event => {
    if (event.lengthComputable) progress.value = event.loaded / event.total;
   };
   const reset = () => {
    active = null;
    submit.disabled = input.disabled = false;
    cancel.hidden = progress.hidden = true;
   };
   xhr.onerror = () => { reset(); status.textContent = 'Upload failed. Check your connection and try again.'; };
   xhr.onabort = () => { reset(); status.textContent = 'Upload canceled.'; };
   xhr.onload = async () => {
    cancel.hidden = true;
    try {
     if (xhr.status !== 201) throw Error(xhr.response?.error || (xhr.status === 413 ? 'This file exceeds the server upload limit.' : 'The video could not be uploaded.'));
     if (room() !== target) throw Error('The room changed. Upload the video again in the current room.');
     await onUploaded(xhr.response.url, file.name, target);
     input.value = '';
     status.textContent = 'Video added.';
    } catch (error) {
     status.textContent = error.message;
    } finally {
     reset();
    }
   };
   xhr.send(file);
  };
 }
 window.SolaceRoomVideo = {connectUpload, resolve};

 const panel = document.querySelector('#roomVideoUploadPage');
 if (!panel) return;
 const room = panel.dataset.room;
 async function enqueue(body) {
  const response = await fetch('/api/rooms/' + encodeURIComponent(room), {
   method: 'POST', headers: {'Content-Type': 'application/json'},
   body: JSON.stringify({action: 'add', ...body})
  });
  if (!response.ok) throw Error((await response.json()).error || 'The video could not be queued.');
 }
 connectUpload(panel.querySelector('form'), () => room, (url, title) => enqueue({url, title}));
 const links = panel.querySelector('#roomVideoLinkForm');
 let direct = false;
 const linkInput = links.querySelector('input');
 const button = links.querySelector('button');
 const status = links.querySelector('[role=status]');
 linkInput.addEventListener('input', () => { direct = false; button.textContent = 'Add link'; });
 links.onsubmit = async event => {
  event.preventDefault();
  button.disabled = true;
  status.textContent = 'Checking link…';
  try {
   const source = await resolve(linkInput.value.trim(), AbortSignal.timeout(25000), direct);
   await enqueue({url: source.url, source_token: source.source_token});
   linkInput.value = '';
   direct = false;
   status.textContent = source.warning || 'Video added.';
  } catch (error) {
   direct = !!error.canTryDirect;
   status.textContent = error.name === 'TimeoutError' ? 'The source took too long to respond.' : error.message;
  } finally {
   button.textContent = direct ? 'Try direct playback' : 'Add link';
   button.disabled = false;
  }
 };
})();
