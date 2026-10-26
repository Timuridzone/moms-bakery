self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
 let data={};try{data=event.data?.json()||{};}catch{}
 const url=/^\/admin(?:#order-\d+)?$/.test(data.url)?data.url:'/admin';
 event.waitUntil(self.registration.showNotification(data.title||'Нове замовлення',{
  body:data.body||'Перевірте кабінет пекарні.',icon:'/icon-192.png',badge:'/icon-192.png',tag:data.tag||'bakery',data:{url}
 }));
});
self.addEventListener('notificationclick',event=>{
 event.notification.close();
 const value=event.notification.data?.url,url=/^\/admin(?:#order-\d+)?$/.test(value)?value:'/admin';
 event.waitUntil((async()=>{
  const windows=await self.clients.matchAll({type:'window',includeUncontrolled:true});
  const existing=windows.find(w=>new URL(w.url).origin===self.location.origin&&new URL(w.url).pathname==='/admin');
  if(existing){await existing.navigate(url);return existing.focus();}
  return self.clients.openWindow(url);
 })());
});
// Deliberately no fetch handler: private orders are never stored in an offline cache.
