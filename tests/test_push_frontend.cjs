const fs=require('fs'),vm=require('vm'),assert=require('assert');
(async()=>{
 const handlers={},shown=[],opened=[];
 const self={addEventListener:(name,fn)=>handlers[name]=fn,skipWaiting:async()=>{},location:{origin:'https://bakery.example'},registration:{showNotification:async(...args)=>shown.push(args)},clients:{claim:async()=>{},matchAll:async()=>[],openWindow:async url=>opened.push(url)}};
 vm.runInNewContext(fs.readFileSync('static/sw.js','utf8'),{self,URL});
 let work;handlers.push({data:{json:()=>({title:'Нове замовлення №1',url:'/admin#order-1'})},waitUntil:p=>work=p});await work;
 assert.equal(shown[0][1].data.url,'/admin#order-1');assert(!handlers.fetch);
 handlers.notificationclick({notification:{close(){},data:{url:'https://evil.example'}},waitUntil:p=>work=p});await work;assert.equal(opened[0],'/admin');
 let permissionRequests=0,helpOpened=false;
 const elements=Object.fromEntries(['push-enable','push-test','push-disable','push-status'].map(x=>[x,{}]));
 const context={openModal:()=>{helpOpened=true;},window:{addEventListener(){},isSecureContext:true,PushManager:{},Notification:{}},navigator:{serviceWorker:{}},document:{querySelector:s=>elements[s.slice(1)]},Notification:{permission:'default',requestPermission:()=>{permissionRequests++;}},api:async()=>({configured:false}),location:{pathname:'/admin'}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('static/push.js','utf8'),context);await vm.runInContext('preparePush()',context);
 assert.equal(permissionRequests,0);assert(elements['push-status'].textContent.includes('налаштування'));
 context.window.isSecureContext=false;await vm.runInContext('preparePush()',context);assert(elements['push-status'].textContent.includes('HTTP'));assert.equal(elements['push-enable'].disabled,false);elements['push-enable'].onclick();assert(helpOpened);assert.equal(permissionRequests,0);
 console.log('Push payload, private-cache avoidance, safe click target, setup and permission-gesture checks passed.');
})().catch(e=>{console.error(e);process.exit(1)});
