'use strict';

window.SolaceSocialChat = (() => {
 const actions = new WeakMap();

 function mount(surface, history, input, form, send, latest) {
  let following = true;
  let frame = 0;
  let settling = false;
  let timers = [];
  let viewportWidth = innerWidth;
  let restingHeight = innerHeight;
  const editing = () => document.activeElement === input;
  const nearBottom = () => history.scrollHeight - history.scrollTop - history.clientHeight < 48;

  function paintLatest() {
   if (latest) latest.hidden = following || editing();
  }

  function scroll() {
   if (frame) return;
   frame = requestAnimationFrame(() => {
    frame = 0;
    if ((following || editing()) && !surface.hidden) history.scrollTop = history.scrollHeight;
    paintLatest();
   });
  }

  function layout() {
   const viewport = window.visualViewport;
   if (innerWidth !== viewportWidth) {
    viewportWidth = innerWidth;
    restingHeight = innerHeight;
   }
   const height = viewport?.height || innerHeight;
   if (!editing()) restingHeight = Math.max(innerHeight, height);
   const keyboard = editing() && Math.max(restingHeight, innerHeight) - height > 90;
   document.body.classList.toggle('social-keyboard', keyboard);
   document.documentElement.style.setProperty('--social-height', Math.round(height) + 'px');
   document.documentElement.style.setProperty('--social-top', Math.max(0, viewport?.offsetTop || 0) + 'px');
   if (!input.solaceIME?.composing) {
   input.style.height = 'auto';
   input.style.height = Math.min(94, Math.max(44, input.scrollHeight + 2)) + 'px';
   }
   scroll();
  }

  function settle() {
   settling = true;
   timers.forEach(clearTimeout);
   layout();
   timers = [120, 300, 600].map(delay => setTimeout(() => {
    layout();
    if (delay === 600) settling = false;
   }, delay));
  }

  function jump() {
   following = true;
   layout();
  }

  function composing(active) {
   if (window.parent !== window) parent.postMessage({type:'solace-composing', active}, location.origin);
  }

  input.addEventListener('focus', () => { following = true; composing(true); settle(); });
  input.addEventListener('blur', () => { composing(false); settle(); });
  input.addEventListener('input', event => { if (!event.isComposing && !input.solaceIME?.composing) jump(); });
  input.addEventListener('solace-commit', jump);
  form.addEventListener('pointerdown', event => {
   if (editing() && send.contains(event.target)) event.preventDefault();
  });
  form.addEventListener('submit', () => requestAnimationFrame(layout));
  for (const event of ['wheel','touchstart','pointerdown','keydown']) {
   history.addEventListener(event, () => { settling = false; }, {passive:true});
  }
  history.addEventListener('scroll', () => {
   if (!frame && !editing() && !(settling && following)) following = nearBottom();
   paintLatest();
  }, {passive:true});
  history.addEventListener('load', scroll, true);
  const changes = new MutationObserver(scroll);
  changes.observe(history, {childList:true, subtree:true, characterData:true});
  const sizes = window.ResizeObserver && new ResizeObserver(scroll);
  sizes?.observe(history);
  sizes?.observe(form);
  window.visualViewport?.addEventListener('resize', settle);
  window.visualViewport?.addEventListener('scroll', settle);
  window.addEventListener('resize', settle);
  window.addEventListener('pageshow', settle);
  window.addEventListener('pagehide', event => {
   timers.forEach(clearTimeout);
   cancelAnimationFrame(frame);
   frame = 0;
   if (!event.persisted) { changes.disconnect(); sizes?.disconnect(); }
  });
  latest?.addEventListener('click', jump);
  layout();

  return {
   layout, jump,
   pause() { following = false; paintLatest(); },
   capture() {
    if (following || editing()) return null;
    const top = history.getBoundingClientRect().top;
    const row = [...history.querySelectorAll('[data-id]')].find(node => node.getBoundingClientRect().bottom > top);
    return {id:row?.dataset.id, offset:row ? row.getBoundingClientRect().top - top : 0};
   },
   restore(anchor) {
    if (anchor?.id && !following && !editing()) {
     const row = history.querySelector('[data-id="' + anchor.id + '"]');
     if (row) history.scrollTop += row.getBoundingClientRect().top - history.getBoundingClientRect().top - anchor.offset;
    }
    scroll();
   }
  };
 }

 return {
  mount,
  setActions(row, message, attachment, status) { actions.set(row, {message, attachment, status}); },
  clearActions(row) { actions.delete(row); },
  clearAttachmentAction(row) { const entry = actions.get(row); if (entry) entry.attachment = null; },
  addActions(row, menu, close) {
   const entry = actions.get(row);
   if (!entry) return;
   for (const [label, action] of [['Delete message', entry.message], ['Delete attachment', entry.attachment]]) {
    if (!action) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'social-delete';
    button.textContent = label;
    button.onclick = async () => {
     close();
     try { await action(); } catch (error) { entry.status(error.message); }
    };
    menu.append(button);
   }
  }
 };
})();
