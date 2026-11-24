'use strict';
(() => {
 const palettes = {
  solace: ['#15111d','#231b2e','#30253e','#d8b9ee','#211629','#f7effb','#c7b6d4','#755589','#1f1828'],
  rose: ['#1c121b','#30202c','#462b3e','#f1bad5','#321b2b','#fff0f6','#d9b8ca','#95617f','#2a1c27'],
  ocean: ['#111c29','#1b2d40','#263e56','#abd9f6','#142b3c','#edf7ff','#adc7dd','#4f7e9f','#182838'],
  forest: ['#121e19','#1d3027','#294236','#b1dfc3','#163123','#f0faf3','#b2cdbd','#527e63','#192a22']
 };
 function apply(value) {
  const key = Object.hasOwn(palettes,value) ? value : 'solace';
  const names = ['bg','panel','raised','accent','ink','text','muted','edge','chrome'];
  document.documentElement.dataset.siteTheme = key;
  palettes[key].forEach((color,index) => document.documentElement.style.setProperty('--site-'+names[index],color));
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content',palettes[key][names.indexOf('chrome')]);
 }
 function restore() {
  try { apply(localStorage.getItem('solace-chat-theme')); } catch { apply('solace'); }
 }
 window.SolaceSiteTheme = {apply};
 window.addEventListener('storage',event => {
  if (event.key==='solace-chat-theme' || event.key===null) restore();
 });
 window.addEventListener('solace-preferences-restored',restore);
 window.addEventListener('pageshow',restore);
 restore();
})();
