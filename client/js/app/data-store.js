/* EGO data gateway / schema 1: preserves existing keys and payloads. */
(()=>{'use strict';
const KEYMAP={characters:'ego-v06-characters',worlds:'ego-v07-worlds',chats:'ego-client-v03-data',preferences:'ego-client-v03-settings'};
const DATA_KEYS=Object.values(KEYMAP);const BACKUP_KEY='ego-safety-backup-v1';const META_KEY='ego-data-schema';
const raw=window.localStorage;
const isObject=x=>x!==null&&typeof x==='object'&&!Array.isArray(x);
function validatePart(part,value){
 if(part==='characters')return isObject(value)&&Object.keys(value).length<=300&&Object.values(value).every(x=>isObject(x)&&typeof x.name==='string'&&x.name.length<=80);
 if(part==='worlds')return Array.isArray(value)&&value.length<=100&&value.every(x=>isObject(x)&&typeof x.id==='string'&&typeof x.name==='string'&&Array.isArray(x.locations)&&x.locations.length<=60);
 if(part==='chats')return Array.isArray(value)&&value.length<=100&&value.every(x=>isObject(x)&&typeof x.id==='string'&&typeof x.person==='string'&&typeof x.title==='string'&&Array.isArray(x.messages)&&x.messages.length<=1000);
 if(part==='preferences')return isObject(value)&&(!('theme' in value)||['violet','blue','rose','mono'].includes(value.theme));
 return false;
}
function snapshot(){const entries={};for(const [part,key] of Object.entries(KEYMAP)){const val=raw.getItem(key);if(val!==null){const parsed=JSON.parse(val);if(!validatePart(part,parsed))throw Error('Неверные данные: '+part);entries[part]=parsed;}}return {app:'EGO',type:'full-backup',schemaVersion:1,createdAt:new Date().toISOString(),entries};}
function validateBackup(doc){if(!isObject(doc)||doc.app!=='EGO'||doc.type!=='full-backup'||doc.schemaVersion!==1||!isObject(doc.entries))throw Error('Неизвестный формат резервной копии');for(const [part,val] of Object.entries(doc.entries)){if(!(part in KEYMAP)||!validatePart(part,val))throw Error('Повреждённый раздел: '+part)}const size=JSON.stringify(doc).length;if(size>12e6)throw Error('Копия превышает лимит 12 МБ');return doc;}
function transactionalRestore(doc){validateBackup(doc);const previous={};for(const key of DATA_KEYS)previous[key]=raw.getItem(key);
 try {for(const [part,key] of Object.entries(KEYMAP)){if(Object.hasOwn(doc.entries,part))raw.setItem(key,JSON.stringify(doc.entries[part]));}raw.setItem(META_KEY,'1');}
 catch(err){for(const [key,value] of Object.entries(previous)){try{value===null?raw.removeItem(key):raw.setItem(key,value)}catch{}}throw Error('Недостаточно места для восстановления. Старые данные сохранены.');}
 return true;
}
function checkpoint(){const doc=snapshot();raw.setItem(BACKUP_KEY,JSON.stringify(doc));return doc;}
function exportFile(doc,name){const blob=new Blob([JSON.stringify(doc,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function report(){return {schemaVersion:Number(raw.getItem(META_KEY)||1),sections:Object.fromEntries(Object.entries(KEYMAP).map(([k,v])=>[k,raw.getItem(v)!==null])),hasCheckpoint:raw.getItem(BACKUP_KEY)!==null};}
try{if(!raw.getItem(META_KEY))raw.setItem(META_KEY,'1')}catch{}
window.EGOData=Object.freeze({KEYMAP,snapshot,validateBackup,transactionalRestore,checkpoint,exportFile,report,restoreCheckpoint(){const rawValue=raw.getItem(BACKUP_KEY);if(!rawValue)throw Error('Резервная копия отсутствует');return transactionalRestore(JSON.parse(rawValue))}});
})();
