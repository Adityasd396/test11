'use strict';

async function receipt(id, event) {
 try {
  await fetch('/api/push/receipt', {
   method:'POST', credentials:'omit',
   headers:{'Content-Type':'application/json'}, body:JSON.stringify({id,event})
  });
 } catch { /* Receipts are best effort and never block opening Solace. */ }
}

self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
 let id;
 try { id = event.data.json().id; } catch { return; }
 if (typeof id !== 'string' || !/^[A-Za-z0-9_-]{32}$/.test(id)) return;
 event.waitUntil((async () => {
  await self.registration.showNotification('Solace', {
   body:'You have a new message.', icon:'/static/icon-192.png',
   tag:'solace-private-messages', data:{id}
  });
  await receipt(id,'delivered');
 })());
});

self.addEventListener('notificationclick', event => {
 event.notification.close();
 event.waitUntil(Promise.all([
  receipt(event.notification.data?.id,'opened'),
  (async () => {
   const target = new URL('/messages',self.location.origin).href;
   const tabs = await clients.matchAll({type:'window',includeUncontrolled:true});
   const inbox = tabs.find(tab => tab.url === target);
   if (inbox) await inbox.focus();
   else await clients.openWindow(target);
  })()
 ]));
});
