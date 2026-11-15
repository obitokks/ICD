'use strict';
const $ = id => document.getElementById(id);
const labels = {queued:'排队',running:'运行中',completed:'完成',failed:'失败',paused:'已暂停',interrupted:'已中断'};
let page = 'config';
let runs = [];
let active = '';
let shown = '';
const text = (id,value) => { $(id).textContent = value ?? ''; };
const pct = n => n == null ? '—' : (100*n).toFixed(2)+'%';
function node(tag,value,cls) {
  const item=document.createElement(tag);
  if(value!=null) item.textContent=value;
  if(cls) item.className=cls;
  return item;
}
async function api(path, options) {
  const response=await fetch('/api/runs'+path,options);
  const data=await response.json();
  if(!response.ok) throw Error(data.error || response.statusText);
  return data;
}
function report(error) { text('notice',error.message); }
function openPage(id) {
  page=id;
  for(const part of document.querySelectorAll('main>section')) part.hidden=part.id!==id;
  for(const button of document.querySelectorAll('[data-page]')) button.setAttribute('aria-current',String(button.dataset.page===id));
  refresh().catch(report);
}
for(const button of document.querySelectorAll('[data-page]')) button.onclick=()=>openPage(button.dataset.page);
function runLabel(run){
  return `${run.description.title} ｜ ${run.description.details} ｜ ${labels[run.status]} ｜ ${run.created.slice(0,19).replace('T',' ')} ｜ ${run.id.slice(0,8)}`;
}
function runCard(run){
  const box=node('span',null,'run-card');
  box.append(node('strong',run.description.title),node('span',run.description.details,'muted'),
    node('small',`${labels[run.status]} · ${run.created} · ${run.id.slice(0,8)}`));
  return box;
}
async function history() {
  runs=await api('');
  const before=active;
  $('run').replaceChildren(new Option('选择已创建的实验',''));
  for(const run of runs) {
    $('run').append(new Option(runLabel(run),run.id));
  }
  active=runs.some(run=>run.id===before) ? before : runs[0]?.id || '';
  $('run').value=active;
  $('history-list').replaceChildren(...runs.map(run=>{
    const button=node('button');button.append(runCard(run));
    button.onclick=()=>{active=run.id;$('run').value=active;shown='';refresh().catch(report);};
    return button;
  }));
  $('history-list').className='run-list';
  const checked=new Set([...$('pdf-choices').querySelectorAll(':checked')].map(input=>input.value));
  $('pdf-choices').replaceChildren(...runs.map(run=>{
    const label=node('label',null,'choice');
    const input=node('input');
    input.type='checkbox';input.value=run.id;input.checked=checked.has(run.id);
    label.append(input,runCard(run));
    return label;
  }));
}
$('run').onchange=()=>{active=$('run').value;shown='';$('sample-detail').replaceChildren();refresh().catch(report);};
$('setup').onsubmit=async event=>{
  event.preventDefault();
  const form=new FormData(event.target);
  const data=Object.fromEntries(form);
  for(const key of ['sources','algorithms']) data[key]=form.getAll(key);
  for(const key of ['count','seed','dict_size']) data[key]=Number(data[key]);
  const button=event.submitter;
  const batch=button.id==='batch-start';
  if(batch){
    data.sources=['ROAD','MAVLink','DataFlash'];
    data.algorithms=['zlib-1','zlib-6','zstd-1','zstd-dict'];
  }
  button.disabled=true;
  try{
    text('notice','');
    active=(await api(batch?'/batch':'',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})).id;
    await history();
    openPage('live');
  }catch(error){report(error);}
  finally{button.disabled=false;}
};
for(const action of ['pause','resume']) $(action).onclick=async()=>{
  try{await api('/'+active+'/'+action,{method:'POST'});await history();await refresh();}
  catch(error){report(error);}
};
function detail(title,value) {
  const item=node('details');
  item.append(node('summary',title),node('pre',typeof value==='string'?value:JSON.stringify(value,null,2)));
  return item;
}
function bytes(title,item) {
  const group=node('div');
  group.append(node('h3',`${title} · ${item.length} bit`),detail('十六进制',item.hex),detail('二进制 0/1',item.bits));
  return group;
}
function sampleView(sample, brief=false) {
  const root=node('div');
  const p=sample.provenance;
  root.append(node('h3',`样本 ${sample.id} · ${p.source}`),
    node('p',`${p.file} · [${p.start}, ${p.end}) Byte · 首部 ${p.header_bits} bit · 校验旁路 ${p.trailer_bits} bit`),
    detail('原始文件 SHA-256、记录编号、来源范围与原始记录组成',p),
    bytes('原始数据',sample.raw));
  if(brief) return root;
  if(!sample.results.length) root.append(node('p','样本已准备，尚无已提交的编码结果。'));
  for(const r of sample.results) {
    const group=node('details');
    group.append(node('summary',`${r.algorithm} · ${r.ok?'验证通过':'验证失败'} · ${r.sent_bits ?? '无'} bit`));
    const {compressed,restored,wire,decoded,...metrics}=r;
    group.append(node('p',`发送比例 ${pct(r.sent_bits==null?null:r.sent_bits/r.original_bits)} · 节省 ${pct(r.sent_bits==null?null:1-r.sent_bits/r.original_bits)} · 不一致 ${r.bit_error_count} bit`),
      bytes('编码器完整输出',compressed),bytes('实际恢复的压缩载荷',decoded),
      bytes('还原的完整样本',restored),bytes('实际发送块（分发字典单独计费）',wire),
      detail('验证、SHA-256、差异位置与发送开销',metrics));
    root.append(group);
  }
  return root;
}
$('lookup').onsubmit=async event=>{
  event.preventDefault();
  try{
    if(!active) throw Error('请先选择实验');
    text('notice','');
    $('sample-detail').replaceChildren(sampleView(await api('/'+active+'/samples/'+$('sample-number').value)));
  }catch(error){report(error);}
};
function drawLive(data) {
  const values={'测试任务':data.total,'已完成':data.completed,'剩余':data.total-data.completed,'成功':data.success,'失败':data.failures};
  $('progress-cards').replaceChildren(...Object.entries(values).map(([k,v])=>{
    const item=node('div',k,'card');item.append(node('strong',v));return item;
  }));
  $('progress').max=Math.max(1,data.total);$('progress').value=data.completed;
  const elapsed=Math.max(0,Math.round(((data.finished?Date.parse(data.finished):Date.now())-Date.parse(data.created))/1000));
  const current=data.current.sample ? `当前 ${data.current.sample} / ${data.current.algorithm}` : '无正在运行的样本';
  text('progress-details',`${current} · 平均发送比例 ${pct(data.average_ratio)} · 累计达标 ${pct(data.completed?data.achieved/data.completed:null)} · 正确 ${pct(data.completed?data.success/data.completed:null)} · ${elapsed} s`);
  text('live-note',[data.note,data.latest?`最近完成 ${data.latest.sample_id} / ${data.latest.algorithm} · ${pct(data.latest.sent_bits==null?null:data.latest.sent_bits/data.latest.original_bits)} · ${data.latest.ok?'验证通过':'验证失败'}`:''].filter(Boolean).join(' · '));
  $('pause').disabled=!['running','queued'].includes(data.status);
  $('resume').disabled=!['interrupted','paused','failed','queued'].includes(data.status);
  const sid=data.current.sample || data.latest?.sample_id;
  $('inspect-current').disabled=!sid;
  $('inspect-current').onclick=()=>{$('sample-number').value=sid;openPage('inspect');$('lookup').requestSubmit();};
  const key=data.id+'/'+sid;
  if(!sid) $('current-sample').replaceChildren();
  if(sid && shown!==key){
    shown=key;
    api('/'+data.id+'/samples/'+sid).then(sample=>{
      if(shown===key && page==='live') $('current-sample').replaceChildren(sampleView(sample,true));
    }).catch(report);
  }
}
function table(rows,fields) {
  const wrap=node('div',null,'scroll'), t=node('table'), header=node('tr');
  for(const [,label] of fields) header.append(node('th',label));
  const head=node('thead');head.append(header);t.append(head);
  const body=node('tbody');
  for(const row of rows){
    const tr=node('tr');
    for(const [key,,format] of fields){const val=row[key];tr.append(node('td',val==null?'—':format?format(val):val));}
    body.append(tr);
  }
  t.append(body);wrap.append(t);return wrap;
}
function sampleIndex(run) {
  const item=node('details');
  item.append(node('summary','已冻结样本编号'));
  item.ontoggle=async()=>{
    if(!item.open || item.children.length>1) return;
    try{
      const rows=await api('/'+run+'/samples');
      item.append(node('pre',rows.map(row=>row.id+' · '+row.file).join('\n') || '尚未冻结样本'));
    }catch(error){report(error);}
  };
  return item;
}
function deleteButton(data) {
  const button=node('button','删除此实验记录','danger');
  button.disabled=['queued','running'].includes(data.status);
  button.onclick=async()=>{
    if(!window.confirm(`永久删除此实验的结果、样本及日志？\n${data.description.title}\n${data.id}`)) return;
    button.disabled=true;
    try{
      await api('/'+data.id,{method:'DELETE'});
      shown='';
      $('sample-detail').replaceChildren();
      $('history-detail').replaceChildren();
      $('result-detail').replaceChildren();
      delete $('result-detail').dataset.fingerprint;
      $('result-links').replaceChildren();
      text('run-state','');
      await history();
      await refresh();
      text('notice','实验记录已删除');
    }catch(error){report(error);}
    finally{button.disabled=false;}
  };
  return button;
}
let refreshing=false;
async function refresh() {
  if(!active || refreshing) return;
  const run=active, tab=page;
  refreshing=true;
  try {
    const more=page==='results';
    const data=await api('/'+run+(more?'?summary=1':''));
    if(run!==active || tab!==page) return;
    text('run-state',`模式 ${data.config.mode} · ${labels[data.status] || data.status}`);
    if(tab==='live'){
      const jobs=data.batch?.jobs;
      if(jobs){
        const next=jobs.find(job=>job.status==='running');
        if(next && next.id!==run && ['completed','failed'].includes(data.status)){
          active=next.id;shown='';await history();return;
        }
      }
      drawLive(data);
      if(jobs) $('live-note').append(document.createTextNode(
        ` · 批次第 ${data.batch.position}/${jobs.length} 项 · 完成 ${jobs.filter(job=>job.status==='completed').length} 项 · 失败 ${jobs.filter(job=>job.status==='failed').length} 项`));
    }
    if(tab==='results'){
      const fingerprint=JSON.stringify([run,data.status,data.completed,data.environment]);
      const panel=$('result-detail');
      if(panel.dataset.fingerprint===fingerprint) return;
      const opened=new Set([...panel.querySelectorAll('details[open]')].map(item=>item.firstChild.textContent));
      const scroll=[...panel.querySelectorAll('.scroll')].map(item=>item.scrollLeft);
      panel.dataset.fingerprint=fingerprint;
      const links=['csv','json','jsonl','lengths.csv'].map(ext=>{
        const a=node('a',ext==='lengths.csv'?'数据源长度 CSV':ext.toUpperCase()+(ext==='jsonl'?' 逐样本导出':' 汇总导出'),'button');
        a.href='/api/runs/'+run+'/export/'+ext;return a;
      });
      $('result-links').replaceChildren(...links);
      const number=n=>Number(n).toFixed(2);
      const settings=data.description.parameters;
      const parameters=detail('完整实验参数','');
      parameters.lastChild.replaceWith(table(settings,[['key','参数'],['value','设定']]));
      if(!panel.children.length) opened.add('完整实验参数');
      panel.replaceChildren(node('h3',data.description.title),
        node('p',`${data.id} · ${data.created} → ${data.finished || '进行中'} · ${labels[data.status]} · ${data.completed}/${data.total}`),
        parameters,node('h3','各数据源平均原始长度'),
        table(data.source_lengths,[
          ['source','来源'],['samples','已测试样本数'],['average_original_bits','平均原始长度（bit）',number]
        ]),node('h3','按数据源与算法统计'),
        node('p','总压缩率是逐条计算「实际发送长度 ÷ 原始长度」后的算术平均。实际发送长度含模式标记及适用的首部、校验；大于 100% 表示平均膨胀。','muted'),
        table(data.summary,[
        ['source','来源'],['algorithm','算法'],['samples','样本'],['success','成功'],['failures','失败'],
        ['average_original_bits','平均原始 bit'],['average_sent_bits','平均发送 bit',number],
        ['average_ratio','总压缩率',pct],['average_saving','节省率',pct],
        ['achieved','达标数量'],['hit_rate','达标率',pct],['correctness','正确率',pct],
        ['sha_mismatch','SHA 不一致样本'],['bit_errors','错误 bit'],
        ['encode_mean_us','编码均值 μs',number],['encode_p50_us','编码 P50',number],['encode_p95_us','编码 P95',number],
        ['decode_mean_us','解码均值 μs',number],['decode_p50_us','解码 P50',number],['decode_p95_us','解码 P95',number],
        
       ]));
      const headers=detail('首部长度分组','');
      headers.lastChild.replaceWith(data.header_summary?.length?table(data.header_summary,[
        ['source','来源'],['algorithm','算法'],['header_bits_min','首部起点 bit'],['header_bits_max','首部上限 bit'],
        ['samples','样本数'],['average_ratio','平均发送比',pct],['hit_rate','达标率',pct]
      ]):node('p',data.config.mode==='B'?'模式 B 无首部，适用结果见上表。':'暂无已提交的首部统计。'));
      const environment=detail('环境与资源',data.environment ?? '暂无环境记录');
      panel.append(headers,environment);
      for(const item of panel.querySelectorAll('details')) item.open=opened.has(item.firstChild.textContent);
      [...panel.querySelectorAll('.scroll')].forEach((item,i)=>item.scrollLeft=scroll[i]||0);
    }
    if(tab==='history'){
      const info=await api('/'+run+'/records');
      if(run===active && tab===page) $('history-detail').replaceChildren(
        node('h3',data.description.title),
        node('p',`${labels[data.status]} · ${data.created} → ${data.finished || '进行中'} · ${data.id}`),
        table(data.description.parameters,[['key','参数'],['value','设定']]),
        deleteButton(data),sampleIndex(run),detail('运行日志',info.logs),detail('数据边界异常',data.audit),detail('字典版本与训练来源',info.dictionaries));
    }
  }finally{refreshing=false;}
}
$('pdf-export').ontoggle=()=>{if($('pdf-export').open) history().catch(report);};
for(const id of ['pdf-all','pdf-selected']) $(id).onclick=async()=>{
  const button=$(id);
  try{
    const ids=id==='pdf-all'?null:[...$('pdf-choices').querySelectorAll(':checked')].map(item=>item.value);
    if(ids && !ids.length) throw Error('请勾选至少一个实验');
    button.disabled=true;text('notice','正在生成 PDF…');
    const response=await fetch('/api/runs/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
    if(!response.ok) throw Error((await response.json()).error || 'PDF 生成失败');
    const url=URL.createObjectURL(await response.blob());
    const link=node('a');link.href=url;link.download='ICD-实验结果汇总.pdf';link.click();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    text('notice','PDF 已生成');
  }catch(error){report(error);}
  finally{button.disabled=false;}
};
history().then(()=>openPage('config')).catch(report);
setInterval(()=>{if(['live','results'].includes(page)) refresh().catch(report);},1200);
