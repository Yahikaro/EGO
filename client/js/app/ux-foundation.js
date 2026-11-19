/* EGO UX Foundation — additive, mock-only UI enhancements */
(()=>{
  const applyViewport=()=>document.documentElement.style.setProperty('--viewport-height',window.innerHeight+'px');
  window.addEventListener('resize',applyViewport,{passive:true});applyViewport();
  const accessibility=()=>{
    document.querySelectorAll('button:not([type])').forEach(button=>button.type='button');
    document.querySelectorAll('.rail-btn').forEach(button=>button.setAttribute('aria-current',button.classList.contains('selected')?'page':'false'));
  };
  const observe=new MutationObserver(accessibility);observe.observe(document.getElementById('app'),{attributes:true,attributeFilter:['class'],childList:true,subtree:true});accessibility();
})();
