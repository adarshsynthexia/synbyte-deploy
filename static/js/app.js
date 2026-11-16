(()=>{
 const menu=document.querySelector('#mobileMenu'); const sidebar=document.querySelector('#sidebar');
 if(menu&&sidebar){menu.addEventListener('click',()=>{sidebar.classList.toggle('open');menu.setAttribute('aria-expanded',sidebar.classList.contains('open'))});}
 document.querySelectorAll('[data-password-toggle]').forEach(btn=>btn.addEventListener('click',()=>{let input=document.getElementById(btn.dataset.passwordToggle);if(input){input.type=input.type==='password'?'text':'password';btn.textContent=input.type==='password'?'Show':'Hide';}}));
 const pollOrder=document.querySelector('[data-track-order]');
 if(pollOrder){let last=pollOrder.dataset.status;setInterval(async()=>{try{const r=await fetch('/api/orders/'+pollOrder.dataset.trackOrder+'/status',{credentials:'same-origin'});if(!r.ok)return;const d=await r.json();if(d.status!==last){last=d.status;location.reload();}}catch(e){}},6000);}
 if(document.body.dataset.authenticated==='true'){
 let lastId=null;
 setInterval(async()=>{try{const r=await fetch('/api/notifications',{credentials:'same-origin'});if(!r.ok)return;const rows=await r.json();const unread=rows.filter(x=>!x.read_at);const badge=document.querySelector('[data-notification-count]');if(badge)badge.textContent=unread.length?String(unread.length):'';
 if(unread.length&&lastId!==null&&unread[0].id>lastId&&document.visibilityState==='visible'){
   const t=document.createElement('div');t.className='toast';t.textContent=unread[0].title+': '+unread[0].body;document.body.appendChild(t);setTimeout(()=>t.remove(),6500);
   if('Notification' in window&&Notification.permission==='granted')new Notification(unread[0].title,{body:unread[0].body});}
 if(rows.length)lastId=rows[0].id;
 }catch(e){}},6000);
 const btn=document.querySelector('#allowNotifications');if(btn)btn.addEventListener('click',async()=>{if('Notification' in window){const p=await Notification.requestPermission();btn.textContent='Notification permission: '+p;}});
 }
 const razorButton=document.querySelector('[data-razorpay]');
 if(razorButton)razorButton.addEventListener('click',()=>{
  if(!window.Razorpay){alert('Payment gateway not available. Retry after checking your internet.');return;}
  const opts={key:razorButton.dataset.key,amount:Number(razorButton.dataset.amount),currency:'INR',name:'SYNBYTE',description:'Campus food pre-order (TEST MODE)',order_id:razorButton.dataset.razorpay,
  theme:{color:'#d50c2f'},handler(response){let form=document.getElementById('verifyGateway');['razorpay_payment_id','razorpay_order_id','razorpay_signature'].forEach(k=>form.querySelector('[name='+k+']').value=response[k]||'');form.submit();}};
  new Razorpay(opts).open();
 });
})();
