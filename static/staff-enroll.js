'use strict';
(async()=>{
 const token=location.hash.slice(1);
 history.replaceState({},'',location.pathname);
 const csrf=document.querySelector('meta[name=ops-csrf]').content;
 const status=document.getElementById('enrollStatus'),form=document.getElementById('enrollForm');
 async function send(action,values={}){
  const response=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
   body:JSON.stringify({token,action,...values}),signal:AbortSignal.timeout(15000),cache:'no-store'});
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw Error(data.error||'Unable to use this invitation.');
  return data;
 }
 if(!token){status.textContent='Open the private invitation link from your administrator.';return;}
 try{
  const data=await send('prepare');
  document.getElementById('enrollUsername').textContent=data.username;
  document.getElementById('enrollSecret').value=data.authenticator_key;
  form.hidden=false;
 }catch(error){status.textContent=error.message;return;}
 form.onsubmit=async event=>{
  event.preventDefault();
  const button=event.submitter;button.disabled=true;
  try{const data=await send('activate',Object.fromEntries(new FormData(form)));form.reset();form.hidden=true;status.textContent=data.message;}
  catch(error){status.textContent=error.message;}
  finally{button.disabled=false;}
 };
})();
