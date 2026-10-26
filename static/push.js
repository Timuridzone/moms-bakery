/* Owner-only opt-in. No customer data is cached by the service worker. */
let pushRegistration, pushConfig;
function pushPanel(){return `<section class="card push-panel"><h2>Сповіщення про замовлення</h2><p>Після налаштування отримуйте повідомлення про нові замовлення, навіть коли сторінка пекарні закрита.</p><p id="push-status" role="status">Перевіряємо налаштування…</p><div class="tracking-actions"><button class="btn" id="push-enable" disabled>Увімкнути сповіщення</button><button class="btn secondary" id="push-test" hidden>Надіслати пробне</button><button class="text-btn" id="push-disable" hidden>Вимкнути на цьому пристрої</button></div><p class="small-copy muted">На iPhone або iPad: відкрийте сайт у Safari → «Поділитися» → «На початковий екран». Відкрийте пекарню з її значка та ввімкніть сповіщення. Потрібна iOS 16.4 або новіша. На Android можна встановити сайт через меню браузера.</p><p class="small-copy muted">Сповіщення можуть затримуватися через інтернет або налаштування телефона. Замовлення завжди зберігаються в кабінеті. Вихід із кабінету не вимикає сповіщення; для перегляду деталей потрібен вхід.</p></section>`;}
function pushMessage(text){const el=document.querySelector('#push-status');if(el)el.textContent=text;}
function explainPushSetup(message){
 pushMessage(message);
 const button=document.querySelector('#push-enable');button.hidden=false;button.disabled=false;button.textContent='Як увімкнути сповіщення';
 button.onclick=()=>openModal('Як увімкнути сповіщення',`<p>Зараз сповіщення не ввімкнені. Саме оформлення замовлення не вмикає їх автоматично.</p><ol><li>Потрібна HTTPS-адреса з довіреним сертифікатом. Локальна адреса http://192.168… підходить для перевірки замовлень, але не для сповіщень на iPhone.</li><li>Той, хто налаштовує сайт, має завершити налаштування служби сповіщень на сервері.</li><li>На iPhone відкрийте HTTPS-сайт у Safari, натисніть «Поділитися» → «На початковий екран».</li><li>Відкрийте пекарню з її значка, увійдіть у кабінет і натисніть «Увімкнути сповіщення». Дозвольте їх та надішліть пробне.</li></ol><p class="notice">Лише після цього нові замовлення надсилатимуть сповіщення. Попередні замовлення залишаються в кабінеті. Не потрібно оформлювати їх повторно.</p>`);
}
async function preparePush(){
 const button=document.querySelector('#push-enable');if(!button)return;
 button.disabled=true;button.textContent='Увімкнути сповіщення';
 if(!window.isSecureContext){explainPushSetup('Сповіщення вимкнені: ви відкрили локальний сайт через HTTP. iPhone потребує HTTPS та встановлення на початковий екран. Замовлення працюють, але сповіщення з цієї адреси не надходитимуть.');return;}
 if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){explainPushSetup('У цьому режимі браузера сповіщення недоступні. На iPhone відкрийте HTTPS-сайт у Safari та додайте його на початковий екран.');return;}
 try{
  pushConfig=await api('/api/admin/push');
  if(!pushConfig.configured){explainPushSetup('Сповіщення ще не ввімкнені: потрібно завершити налаштування на сервері.');return;}
  pushRegistration=await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;
  const subscription=await pushRegistration.pushManager.getSubscription();
  const state=subscription?await api('/api/admin/push/status',{subscription:subscription.toJSON()}):{active:false};
  button.disabled=Notification.permission==='denied';button.hidden=state.active;button.onclick=enablePush;
  document.querySelector('#push-test').hidden=!state.active;document.querySelector('#push-disable').hidden=!subscription;
  document.querySelector('#push-test').onclick=async()=>{try{await api('/api/admin/push/test',{subscription:subscription.toJSON()});pushMessage('Пробне сповіщення в черзі. Зачекайте кілька секунд.');setTimeout(()=>preparePush(),5000);}catch(e){pushMessage(e.message);}};
  document.querySelector('#push-disable').onclick=async()=>{try{await api('/api/admin/push/disable',{subscription:subscription.toJSON()});await subscription.unsubscribe();await preparePush();}catch(e){pushMessage(e.message);}};
  const states={pending:'Останнє сповіщення очікує надсилання.',sending:'Останнє сповіщення надсилається.',sent:'Останнє сповіщення прийняте службою доставки. Перевірте телефон.',failed:'Останнє сповіщення не надіслано. Спробуйте пробне або ввімкніть сповіщення повторно.',expired:'Останнє сповіщення застаріло. Перевірте нові замовлення в кабінеті.'};
  pushMessage(Notification.permission==='denied'?'Сповіщення заблоковані. Дозвольте їх у налаштуваннях браузера або телефона й оновіть сторінку.':state.active?'Сповіщення увімкнені на цьому пристрої. '+(states[state.lastStatus]||''):'Сповіщення на цьому пристрої вимкнені.');
 }catch(e){pushMessage(e.message||'Не вдалося перевірити сповіщення. Оновіть сторінку.');}
}
async function enablePush(){
 const button=document.querySelector('#push-enable');button.disabled=true;
 try{
  // Request permission directly in the user's click, before network requests.
  const permission=await Notification.requestPermission();
  if(permission!=='granted'){pushMessage('Дозвіл не надано. Ви можете змінити це в налаштуваннях браузера.');return;}
  const key=Uint8Array.from(atob(pushConfig.publicKey.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-pushConfig.publicKey.length%4)%4)),c=>c.charCodeAt(0));
  let subscription=await pushRegistration.pushManager.getSubscription();
  if(!subscription)subscription=await pushRegistration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
  await api('/api/admin/push/subscribe',{subscription:subscription.toJSON()});await preparePush();
 }catch(e){pushMessage('Не вдалося ввімкнути сповіщення. Перевірте дозвіл та інтернет і спробуйте ще раз.');}
 finally{button.disabled=false;}
}
function openPushOrder(){const id=location.hash.match(/^#order-(\d+)$/)?.[1];if(id&&admin?.orders.some(o=>o.id===+id)){history.replaceState(null,'','/admin');openOrder(id);}}
window.addEventListener('hashchange',()=>{if(location.pathname==='/admin'&&admin)refresh().then(openPushOrder).catch(()=>{});});
