/* Keep the native IME in charge of selection and composition. */
'use strict';
window.SolaceComposer = {
 mount(input) {
  input.dir = 'ltr';
  let composing = false;
  input.addEventListener('compositionstart', () => { composing = true; });
  input.addEventListener('compositionend', () => { composing = false; input.dispatchEvent(new CustomEvent('solace-commit')); });
  return {get composing() { return composing; }};
 }
};
document.querySelectorAll('#message,#dmText,#worldText').forEach(input => {
 input.solaceIME = SolaceComposer.mount(input);
 const form=input.form;
 form?.addEventListener('submit',event=>{
  if(input.solaceIME.composing){event.preventDefault();event.stopImmediatePropagation();}
 },true);
 if(input.id==='message')input.addEventListener('keydown',event=>{
  if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&!input.solaceIME.composing&&event.keyCode!==229){event.preventDefault();form.requestSubmit();}
 });
});
