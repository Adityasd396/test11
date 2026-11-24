'use strict';

(async () => {
 const preference = document.getElementById('pushPreference');
 const status = document.getElementById('pushStatus');
 const enable = document.getElementById('enablePush');
 const disable = document.getElementById('disablePush');
 const csrf = document.querySelector('meta[name=account-csrf]').content;
 const supported = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
 let key;
 let registration;
 let subscription;

 async function api(method = 'GET', body) {
  const response = await fetch('/api/me/push', {
   method, cache: 'no-store', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
   body: body ? JSON.stringify(body) : undefined
  });
  if (!response.ok) throw Error('Could not update notifications. Try again.');
  return response.json();
 }

 function render() {
  enable.hidden = !supported || !key;
  enable.disabled = !preference.checked;
  disable.hidden = !subscription;
  if (!preference.checked) status.textContent = 'Push notifications are off for this profile.';
  else if (!supported) {
   status.textContent = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
    ? 'Add Solace to your Home Screen and open it there to enable device notifications.'
    : 'Device notifications are unavailable in this browser.';
  } else if (!key) status.textContent = 'Device delivery is unavailable on this server.';
  else if (Notification.permission === 'denied') status.textContent = 'Allow notifications in your browser or device settings.';
  else status.textContent = subscription ? 'This device has a notification subscription.' : 'Enable this device to receive notifications.';
  enable.textContent = subscription ? 'Update this device' : 'Enable on this device';
 }

 let settings;
 try {
  settings = await api();
 } catch {
  status.textContent = 'Could not load notification settings. Refresh to try again.';
  return;
 }
 preference.checked = settings.enabled;
 key = settings.public_key;
 if (supported) {
  try {
   registration = await navigator.serviceWorker.getRegistration('/');
   subscription = await registration?.pushManager.getSubscription();
  } catch { /* The profile preference remains editable if device setup is unavailable. */ }
 }
 preference.disabled = false;
 render();

 preference.onchange = async () => {
  const enabled = preference.checked;
  preference.disabled = enable.disabled = disable.disabled = true;
  let error;
  try {
   await api('PATCH', {enabled});
  } catch (failure) {
   preference.checked = !enabled;
   error = failure.message;
  } finally {
   preference.disabled = disable.disabled = false;
   render();
   if (error) status.textContent = error;
  }
 };

 enable.onclick = async () => {
  preference.disabled = enable.disabled = disable.disabled = true;
  let error;
  try {
   if (Notification.permission !== 'granted' && await Notification.requestPermission() !== 'granted') {
    throw Error('Notification permission was not granted.');
   }
   if (!registration) registration = await navigator.serviceWorker.register('/solace-push.js', {scope: '/'});
   if (!registration.active) registration = await navigator.serviceWorker.ready;
   const active = await registration.pushManager.getSubscription()
    || await registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: key});
   await api('POST', active.toJSON());
   subscription = active;
  } catch (failure) {
   error = Notification.permission === 'denied'
    ? 'Allow notifications in your browser or device settings.' : failure.message;
  } finally {
   preference.disabled = disable.disabled = false;
   render();
   if (error) status.textContent = error;
  }
 };

 disable.onclick = async () => {
  preference.disabled = enable.disabled = disable.disabled = true;
  let error;
  try {
   await api('DELETE', {endpoint: subscription.endpoint});
   await subscription.unsubscribe();
   subscription = null;
  } catch (failure) {
   error = failure.message;
  } finally {
   preference.disabled = disable.disabled = false;
   render();
   if (error) status.textContent = error;
  }
 };
})();
