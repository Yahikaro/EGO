// media-lab.js must load and expose only the public preset groups, without errors.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ctx={document:{readyState:'complete',head:{appendChild(){}},createElement:()=>({addEventListener(){},remove(){}}),
  querySelector:()=>null,querySelectorAll:()=>[],getElementById:()=>null},localStorage:{getItem:()=>null,setItem(){}},console};
ctx.window=ctx;vm.createContext(ctx);
vm.runInContext(fs.readFileSync('js/app/media-lab.js','utf8'),ctx);
const keys=ctx.EGOMediaLab.groups().map(g=>g[0]);
assert.equal(JSON.stringify(keys),JSON.stringify(['gender','style','shot','clothes','pose','body','height','age','race','hairstyle','hairColour','camera','length']));
assert.equal(Object.keys(ctx.EGOMediaLab.presets()).length,0);
console.log('PASS: media-lab tests (public preset groups, loads without extra modules)');
