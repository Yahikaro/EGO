/* EGO Quality Gate UI: shared backup, restore, and smoke-check status */
(()=>{'use strict';
const markup=`<div class="setting-row ego-quality-title"><div><b>Защита локальных данных · schema v1</b><p>Общая копия персонажей, миров, историй и настроек. Предыдущие форматы сохраняются.</p></div><span class="quality-tag">ЭТОТ БРАУЗЕР</span></div>
<div class="setting-row"><div><b>Скачать полную копию</b><p>JSON со всеми разделами. Храните файл отдельно от папки клиента.</p></div><button class="primary-small" id="ego-full-backup">Скачать JSON</button></div>
<div class="setting-row"><div><b>Восстановить из файла</b><p>Проверка структуры перед заменой. При ошибке запись откатывается.</p></div><button class="primary-small" id="ego-full-restore">Выбрать файл</button><input id="ego-backup-file" type="file" accept=".json,application/json" hidden></div>
<div class="setting-row"><div><b>Контрольная точка</b><p>Сохранить или восстановить последнюю внутреннюю копию.</p></div><div class="quality-actions"><button class="primary-small" id="ego-create-checkpoint">Сохранить</button><button class="primary-small" id="ego-restore-checkpoint">Восстановить</button></div></div>
<div class="setting-row"><div><b>Диагностика хранилища</b><p id="ego-storage-status">Проверка...</p></div><button class="primary-small" id="ego-storage-check">Проверить</button></div>`;
const status=()=>{const el=document.getElementById('ego-storage-status');if(!el)return;const data=EGOData.report();el.textContent=`Схема ${data.schemaVersion} · Разделы: ${Object.entries(data.sections).filter(x=>x[1]).map(x=>x[0]).join(', ')||'пусто'} · Контрольная точка: ${data.hasCheckpoint?'есть':'нет'}`};
function mount(){if(typeof page==='undefined'||page!=='settings')return;const content=document.getElementById('content');if(!content||content.querySelector('#ego-full-backup'))return;const wrapper=document.createElement('div');wrapper.className='quality-settings';wrapper.innerHTML=markup;content.querySelector('.generic-page')?.append(wrapper);
wrapper.querySelector('#ego-full-backup').onclick=()=>{try{EGOData.exportFile(EGOData.snapshot(),'EGO-full-backup-v1.json');toast('Резервная копия скачана')}catch(e){toast('Ошибка: '+e.message)}};
wrapper.querySelector('#ego-full-restore').onclick=()=>wrapper.querySelector('#ego-backup-file').click();
wrapper.querySelector('#ego-backup-file').onchange=async e=>{const file=e.target.files?.[0];if(!file)return;try{if(file.size>12e6)throw Error('Файл слишком большой');const doc=EGOData.validateBackup(JSON.parse(await file.text()));if(!confirm('Восстановить резервную копию? Существующие разделы из файла будут заменены.'))return;EGOData.transactionalRestore(doc);location.reload()}catch(err){toast('Восстановление: '+err.message)}finally{e.target.value=''}};
wrapper.querySelector('#ego-create-checkpoint').onclick=()=>{try{EGOData.checkpoint();status();toast('Контрольная точка сохранена')}catch(e){toast('Ошибка: '+e.message)}};
wrapper.querySelector('#ego-restore-checkpoint').onclick=()=>{try{if(!confirm('Восстановить контрольную точку?'))return;EGOData.restoreCheckpoint();location.reload()}catch(e){toast('Ошибка: '+e.message)}};
wrapper.querySelector('#ego-storage-check').onclick=()=>{try{EGOData.snapshot();status();toast('Структура данных проверена')}catch(e){toast(e.message)}};
status();}
const observer=new MutationObserver(()=>mount());observer.observe(document.getElementById('content'),{childList:true});mount();
})();
