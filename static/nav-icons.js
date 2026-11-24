'use strict';
window.SolaceIcon = function(name) {
 const ns='http://www.w3.org/2000/svg';
 const svg=document.createElementNS(ns,'svg');
 const use=document.createElementNS(ns,'use');
 svg.setAttribute('class','solace-icon');
 svg.setAttribute('aria-hidden','true');
 svg.setAttribute('focusable','false');
 use.setAttribute('href','/static/nav-icons.svg#'+name);
 svg.append(use);
 return svg;
};
