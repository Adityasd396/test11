'use strict';
(() => {
 const workspace=document.querySelector('.admin-workspace');
 const nav=document.querySelector('.management-nav');
 const links=[...nav.querySelectorAll('a')];
 if(!links.length)return;
 const groups=new Map(links.map(link=>[link.hash.slice(1),link]));
 const panels=[...workspace.children].filter(panel=>panel.matches('section,[data-admin-group]')&&panel.id!=='adminEnrollmentNotice');
 function groupFor(target){
  for(let panel=target;panel&&panel!==workspace;panel=panel.parentElement){
   if(groups.has(panel.id))return panel.id;
   if(panel.dataset.adminGroup&&groups.has(panel.dataset.adminGroup))return panel.dataset.adminGroup;
  }
 }
 function show(){
  const target=document.getElementById(location.hash.slice(1));
  const selected=groupFor(target)||links[0].hash.slice(1);
  for(const panel of panels){
   const isMain=panel.id===selected||panel.dataset.adminGroup===selected;
   panel.hidden=!isMain||(panel.id==='managementOverview'&&workspace.dataset.named==='false');
  }
  for(const [id,link] of groups){
   if(id===selected)link.setAttribute('aria-current','page');
   else link.removeAttribute('aria-current');
  }
  document.dispatchEvent(new CustomEvent('solace-admin-workspace',{detail:selected}));
  if(target&&!target.hidden&&groupFor(target)===selected&&location.hash!=='#'+selected)target.scrollIntoView({block:'start'});
  else scrollTo({top:0,behavior:'instant'});
 }
 window.addEventListener('hashchange',show);
 show();
})();
