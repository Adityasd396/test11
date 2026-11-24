'use strict';
(() => {
 const id = name => document.getElementById(name);
 if(document.querySelector('.admin-workspace')?.dataset.named==='false')return;
 const csrf = document.querySelector('meta[name=ops-csrf]').content;
 const base = '/ops/solace/management/';
 const formatDate = value => value ? new Date(value * 1000).toLocaleString() : '—';
 const element = (tag, text) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  return node;
 };
 async function api(path, body) {
  const response = await fetch(base + path, {
   method: body ? 'POST' : 'GET', cache:'no-store', signal:AbortSignal.timeout(35000),
   headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:body ? JSON.stringify(body) : undefined
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.error || (response.status === 403 ? 'A permitted named staff account is required.' : 'Unable to complete this request.'));
  return data;
 }
 async function action(button,status,callback) {
  if (button?.disabled) return;
  if (button) button.disabled=true;
  status.textContent='Loading…';
  try { return await callback(); }
  catch(error) { status.textContent=error.message; }
  finally { if (button) button.disabled=false; }
 }
 function rows(target, data) {
  target.replaceChildren(...data.map(values => {
   const row=element('tr');
   row.append(...values.map(value => element('td',value ?? '—')));
   return row;
  }));
 }
 const overviewStatus=id('managementOverviewStatus');
 const days=id('managementDays');
 const messageLabels={room_messages:'Room messages',direct_messages:'Direct messages',world_messages:'World messages'};
 const activityRefresh=id('managementRefreshActivity');
 async function overview() {
  await action(days,overviewStatus,async () => {
   const data=await api('overview?days='+days.value);
   const metrics=[['Recently active',data.recent_profiles],['Unique in range',data.active_profiles],['New accounts',data.new_accounts],
                  ...Object.entries(messageLabels).map(([key,label])=>[label,data.messages.find(item=>item.event===key)?.count ?? 0])];
   id('managementMetrics').replaceChildren(...metrics.map(([label,value])=>{
    const card=element('article');card.append(element('strong',value),element('p',label));return card;
   }));
   rows(id('managementDaily'),data.daily.map(day=>[day.date,day.accounts,day.guests,...Object.keys(messageLabels).map(key=>day.messages[key] ?? 0)]));
   rows(id('managementFunnel'),data.growth.map(item=>[item.source==='search_referral'?'Search referrals':'Other sources',item.landing_profiles,item.rooms,item.paired,item.playback]));
   overviewStatus.textContent='';
  });
 }
 days.onchange=overview;
 activityRefresh.onclick=overview;
 const site=id('managementSiteForm'),siteStatus=id('managementSiteStatus');
 let revision=0;
 const fields=['name','origin','contact','address','title','description','verification','logo','favicon','image'];
 function sitePayload(actionName) {
  const settings=Object.fromEntries(fields.map(key=>[key,site.elements[key].value]));
  settings.index=site.elements.index.checked;
  settings.footer=[...id('managementFooter').querySelectorAll('input:checked')].map(input=>input.value);
  return {action:actionName,revision,settings,reason:site.elements.reason.value,confirmed:site.elements.confirmed.checked};
 }
 async function loadSite() {
  const data=await api('site');
  revision=data.revision;
  for(const key of fields)site.elements[key].value=data.settings[key];
  site.elements.index.checked=data.settings.index;
  id('managementFooter').replaceChildren(...Object.entries(data.footer).map(([path,name])=>{
   const label=element('label'),input=element('input');input.type='checkbox';input.value=path;
   input.checked=data.settings.footer.includes(path);label.append(input,document.createTextNode(' '+name));return label;
  }));
  id('managementVersions').replaceChildren(...data.history.filter(item=>item.id!==revision).map(item=>{
   const option=element('option','Version '+item.id+' · '+formatDate(item.created));option.value=item.id;return option;
  }));
  id('managementRestore').disabled=!id('managementVersions').options.length;
 }
 for(const input of site.querySelectorAll('[data-artwork]')){
  input.onchange=()=>action(input,siteStatus,async()=>{
   const image=input.files[0];
   if(!image)return;
   const body=new FormData();
   body.set('image',image);body.set('field',input.dataset.artwork);
   const response=await fetch(base+'artwork',{method:'POST',headers:{'X-CSRF-Token':csrf},body,signal:AbortSignal.timeout(20000)});
   const data=await response.json().catch(()=>({}));
   if(!response.ok)throw Error(data.error||'Unable to upload this image.');
   site.elements[input.dataset.artwork].value=data.path;
   input.value='';siteStatus.textContent='Image ready. Publish to apply it.';
  });
 }
 function preview(settings) {
  id('managementPreviewResult').textContent=[settings.title,settings.description,settings.canonical || 'No canonical origin',
   settings.robots, settings.sitemap_urls+' public sitemap URLs'].join(' · ');
 }
 id('managementPreview').onclick=event=>action(event.currentTarget,siteStatus,async()=>{
  preview((await api('site',sitePayload('preview'))).preview);siteStatus.textContent='Preview only';
 });
 async function saveSite(actionName,button) {
  if (!site.reportValidity()) return;
  await action(button,siteStatus,async()=>{
   const data=sitePayload(actionName);
   if(actionName==='restore')data.restore=Number(id('managementVersions').value);
   const result=await api('site',data);preview(result.preview);
   await loadSite();site.elements.confirmed.checked=false;site.elements.reason.value='';
   siteStatus.textContent='Published version '+result.revision;
  });
 }
 site.onsubmit=event=>{event.preventDefault();saveSite('publish',event.submitter);};
 id('managementRestore').onclick=event=>saveSite('restore',event.currentTarget);
 const users=id('managementUsersForm'),usersStatus=id('managementUsersStatus');
 let offset=0,query={kind:'all',q:'',code:''};
 async function loadUsers(next,button) {
  const result=await action(button,usersStatus,async()=>{
   const data=await api('users?'+new URLSearchParams({...query,offset:next}));
   const userRows=id('managementUserRows');
   rows(userRows,data.users.map(user=>[user.name,user.code?.replace(/^(.{4})(.{4})$/,'$1-$2') ?? 'Not assigned',formatDate(user.created),formatDate(user.last_active),
    user.verified===null?'Guest browser profile':user.verified?'Verified email':'Email unverified',
    user.restrictions.map(item=>item.kind+' ('+item.feature+')'+(item.revoked?' · reversed':'')).join(', ')||'—']));
   data.users.forEach((user,index)=>{
    if(!user.code)return;
    const cell=userRows.rows[index].cells[1],button=element('button','Copy');
    button.type='button';button.className='copy-user-code';
    button.setAttribute('aria-label','Copy user code for '+user.name);
    button.onclick=async()=>{try{await navigator.clipboard.writeText(user.code);usersStatus.textContent='User code copied.'}
      catch{usersStatus.textContent='Copy this code manually: '+user.code;}};
    cell.append(button);
   });
   usersStatus.textContent=data.users.length ? 'Showing '+(next+1)+'–'+(next+data.users.length) : 'No profiles in this view.';
   offset=next;
   return data;
  });
  if(result){id('managementPrevious').disabled=offset===0;id('managementNext').disabled=!result.more;}
 }
 users.onsubmit=event=>{
  event.preventDefault(); query=Object.fromEntries(new FormData(users));loadUsers(0,event.submitter);
 };
 id('managementPrevious').onclick=event=>loadUsers(Math.max(0,offset-25),event.currentTarget);
 id('managementNext').onclick=event=>loadUsers(offset+25,event.currentTarget);
 const google=id('managementGoogleForm'),googleStatus=id('managementGoogleStatus');
 google.elements.report.onchange=()=>{id('managementInspectPath').hidden=google.elements.report.value!=='inspect';};
 function reportTable(target,title,entries,headings,values) {
  target.append(element('h3',title));
  if(!entries.length){target.append(element('p','No results in this range.'));return;}
  const wrap=element('div'),table=element('table'),head=element('thead'),tr=element('tr'),body=element('tbody');
  wrap.className='table-scroll';tr.append(...headings.map(x=>element('th',x)));head.append(tr);
  rows(body,entries.map(values));table.append(head,body);wrap.append(table);target.append(wrap);
 }
 google.onsubmit=event=>{
  event.preventDefault();const reportType=google.elements.report.value;
  action(event.submitter,googleStatus,async()=>{
   const {report,fetched}=await api('google',{action:reportType,path:google.elements.path.value});
   const target=id('managementGoogleRows');target.replaceChildren();
   if(reportType==='search'){
    for (const [key,title] of [['totals','Search totals'],['query','Top queries'],['page','Top pages']])
     reportTable(target,title,report[key],['Result','Clicks','Impressions','CTR','Average position'],
      row=>[row.keys?.[0]??'All searches',row.clicks,row.impressions,(row.ctr*100).toFixed(2)+'%',Number(row.position).toFixed(1)]);
   }else if(reportType==='sitemaps'){
    reportTable(target,'Sitemaps',report,['Sitemap','Last downloaded','Pending','Errors'],row=>[row.path,row.lastDownloaded,row.isPending?'Yes':'No',row.errors]);
   }else if(reportType==='inspect'){
    reportTable(target,'Index status',[report],['Verdict','Coverage','Google canonical','Last crawl'],row=>[row.verdict,row.coverageState,row.googleCanonical,row.lastCrawlTime]);
   }else{
    reportTable(target,reportType==='analytics'?'Acquisition · last 28 days':'Realtime · last 30 minutes',report,
     reportType==='analytics'?['Channel','Sessions','Active users']:['Active users'],
     row=>[...(row.dimensionValues||[]),...(row.metricValues||[])].map(item=>item.value));
   }
   googleStatus.textContent='Report fetched '+formatDate(fetched);
  });
 };
 const staff=id('managementStaffForm'),staffStatus=id('managementStaffStatus');
 staff.onsubmit=event=>{
  event.preventDefault();id('managementInviteLink').hidden=true;
  action(event.submitter,staffStatus,async()=>{
   const data=await api('staff',{...Object.fromEntries(new FormData(staff)),confirmed:staff.elements.confirmed.checked});
   if(data.token){const label=id('managementInviteLink');label.hidden=false;label.querySelector('input').value=location.origin+'/ops/solace/enroll#'+data.token;}
   staffStatus.textContent=data.message;staff.elements.confirmed.checked=false;
  });
 };
 const integrationStatus=id('managementIntegrationStatus');
 const integrationRefresh=id('managementRefreshIntegrations');
 function facts(target, items) {
  target.replaceChildren(...items.map(([label,value])=>{
   const item=element('div');
   item.append(element('dt',label),element('dd',value));
   return item;
  }));
 }
 async function checkCrawlerFile(file, preview, status) {
  preview.textContent=file.body.replace(/></g,'>\n<');
  status.textContent='Checking…';
  try {
   const response=await fetch(file.path,{cache:'no-store',signal:AbortSignal.timeout(8000)});
   const matched=response.status===file.status && await response.text()===file.body;
   status.textContent=matched ? 'HTTP '+response.status+' · matches' : 'HTTP '+response.status+' · differs from preview';
  } catch {
   status.textContent='Could not reach public file';
  }
 }
 function loadIntegrations(){return action(integrationRefresh,integrationStatus,async()=>{
  const data=await api('integrations');
  facts(id('managementIntegrationRows'),[['Release',data.release.version],
   ['Latest backup manifest',formatDate(data.latest_backup_manifest)],['Site revision',data.site_revision || 'Environment defaults']]);
  for(const badge of document.querySelectorAll('[data-integration]')){
   const key=badge.dataset.integration;
   const ready=key==='backup' ? Boolean(data.latest_backup_manifest) : Boolean(data[key]);
   badge.textContent=key==='backup' ? (ready ? 'Manifest found' : 'No manifest found') : ready ? 'Configured' : 'Setup required';
   badge.dataset.ready=String(ready);
  }
  const crawler=data.crawler;
  const canonicalNote=crawler.origin && crawler.origin!==location.origin
   ? ' Checking this admin host. Canonical origin is '+crawler.origin+'.' : '';
  id('managementIndexing').textContent=(crawler.indexing
   ? 'Public indexing enabled · '+crawler.sitemap_urls+' canonical URLs'
   : crawler.origin ? 'Public indexing disabled · empty sitemap' : 'Set a public HTTPS origin in Site setup')
   + ' · Google HTML verification tag '+(data.google_site_verified_tag ? 'configured' : 'absent')+'.'+canonicalNote;
  const activity=api('overview?days=1').then(report=>{
   facts(id('managementActivitySummary'),[['Recently active profiles',report.recent_profiles],
    ['Active profiles today (UTC)',report.active_profiles],['New accounts today (UTC)',report.new_accounts]]);
  }).catch(()=>{id('managementActivitySummary').textContent='Activity unavailable. Open the activity report to retry.';});
  await Promise.all([
   checkCrawlerFile(crawler.robots,id('managementRobotsText'),id('managementRobotsStatus')),
   checkCrawlerFile(crawler.sitemap,id('managementSitemapText'),id('managementSitemapStatus')),
   activity
  ]);
  integrationStatus.textContent='Checked '+new Date().toLocaleTimeString();
 });}
 integrationRefresh.onclick=loadIntegrations;
 const providerForm=id('providerSetupForm');
 if(providerForm){
  const choice=id('providerSetupChoice'), editor=id('providerSetupEditor');
  const providerStatus=id('providerSetupStatus'), fields=id('providerSetupFields');
  const notes={
   email:'Use the verified sender address. Custom SMTP hosts can be approved in the hosting environment.',
   push:'Set up the dispatcher first. The hosting plan must support a continuously running worker for timely delivery.',
   google_credentials:'Use a Google service-account JSON key with read access to the reporting properties.',
   search:'Verify ownership in Search Console first. Use Site setup to publish the Google verification tag.',
   analytics:'Use the numeric property ID. Configure tracking in Google Analytics separately.',
   backup:'Choose an absolute private path. This setting points Solace at your existing backup manifests.'
  };
  let providers, loadingProviders;
  function renderProvider(){
   const provider=providers?.[choice.value];
   editor.hidden=!provider;
   if(!provider)return;
   id('providerSetupNote').textContent=notes[choice.value]||'Enable YouTube Data API v3 and restrict the key to that API.';
   fields.replaceChildren(...provider.fields.map(field=>{
    const label=element('label',field.label);
    let input;
    if(field.type==='tls'){
     input=element('select');
     for(const [value,title] of [['0','STARTTLS'],['1','TLS on connection']]){
      const option=element('option',title);option.value=value;input.append(option);
     }
    }else{
     input=element(field.type==='secret_json'?'textarea':'input');
     if(field.type!=='secret_json')input.type=field.type;
     input.maxLength=field.limit;
     input.autocomplete=field.type==='password'?'new-password':'off';
     input.spellcheck=false;
     if(field.configured && field.value===undefined)input.placeholder='Already configured';
    }
    input.name=field.name;
    input.value=field.value??'';
    label.append(input);
    return label;
   }));
   providerForm.elements.confirmed.checked=false;
   id('providerSetupReset').hidden=!provider.overridden;
  }
  async function readProviderEditor(){
   if(loadingProviders)return loadingProviders;
   loadingProviders=api('providers');
   try{providers=(await loadingProviders).providers;renderProvider();}
   finally{loadingProviders=null;}
  }
  choice.onchange=async()=>{
   if(!choice.value){renderProvider();return;}
   await action(null,providerStatus,async()=>{
    if(!providers)await readProviderEditor();
    else renderProvider();
    providerStatus.textContent='';
   });
  };
  providerForm.onsubmit=event=>{
   event.preventDefault();
   const button=event.submitter;
   const actionName=button.value==='reset'?'reset':'save';
   const data=new FormData(providerForm);
   const settings=actionName==='save'?Object.fromEntries([...data].filter(([key])=>key.startsWith('SOLACE_')||key==='YOUTUBE_API_KEY')):{};
   action(button,providerStatus,async()=>{
    const result=await api('providers',{action:actionName,provider:choice.value,settings,confirmed:data.has('confirmed')});
    await readProviderEditor();
    providerStatus.textContent=result.message;
    loadIntegrations();
   });
  };
 }
 id('managementPrevious').disabled=true;id('managementNext').disabled=true;
 const loads={managementOverview:overview,managementSite:()=>action(null,siteStatus,async()=>{await loadSite();siteStatus.textContent='';}),
              managementUsers:()=>loadUsers(0,null),managementIntegrations:loadIntegrations};
 const loaded=new Set();
 document.addEventListener('solace-admin-workspace',event=>{
  const key=event.detail;
  if(loads[key]&&!loaded.has(key)){loaded.add(key);loads[key]();}
 });

})();
