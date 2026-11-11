/* === arb.js === */
/* Pure ARB and ICU processing. No external network/services. */
const hashText = str => {
  let h = 2166136261;
  for (const ch of str) { h ^= ch.codePointAt(0); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(16).padStart(8, '0');
};
const textFingerprint = value => hashText(value ?? '\u0000');
function parseArb(text, name='file.arb') {
  let data;
  try { data = JSON.parse(text.replace(/^\uFEFF/,'')); } catch (err) { throw new Error(`${name}: invalid JSON — ${err.message}`); }
  if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error(`${name}: ARB must contain a JSON object.`);
  const messages = Object.create(null);
  for (const [key, value] of Object.entries(data)) {
    if (key.startsWith('@')) continue;
    if (typeof value !== 'string') throw new Error(`${name}: message “${key}” must contain a string.`);
    messages[key] = value;
  }
  for (const [key,value] of Object.entries(data)) {
    if (!key.startsWith('@') || key.startsWith('@@')) continue;
    if (value !== null && (typeof value !== 'object' || Array.isArray(value))) throw new Error(`${name}: metadata “${key}” must be an object or null.`);
  }
  return {name,raw:text,data,messages,locale: typeof data['@@locale'] === 'string' ? data['@@locale'] : null, hash:hashText(text)};
}
function localeCompatible(locale,expected) {
  if (!locale) return true;
  return locale.toLowerCase().replace(/-/g,'_') === expected.toLowerCase().replace(/-/g,'_');
}

/* Recursive parser for ICU MessageFormat messages, selectors, offsets, nested arguments,
   doubled apostrophes and ICU syntax quoting. Emits syntax errors with locations. */
function parseICU(input) {
  let p=0;
  const errors=[];
  const length=input.length;
  const fail=message=>{ throw new Error(`${message} at character ${p+1}`); };
  const ws=()=>{ while(p<length && /\s/.test(input[p])) p++; };
  const readWord=()=>{ const start=p; while(p<length && !/[\s,{}]/.test(input[p])) p++; return input.slice(start,p); };
  const parseMessage=(nested=false)=>{
    const nodes=[];
    while(p<length) {
      if (input[p]==='}') { if(nested) {p++;return nodes;} fail('Unexpected closing brace'); }
      if (input[p]==="'") {
        if(input[p+1]==="'") {p+=2;continue;}
        if (/[{}#]/.test(input[p+1]||'')) {
          p++;
          let closed=false;
          while(p<length){ if(input[p]==="'") { if(input[p+1]==="'"){p+=2;continue;} p++;closed=true;break;} p++; }
          if(!closed) fail('Unterminated quoted literal');
          continue;
        }
      }
      if(input[p]==='{') {p++; nodes.push(parseArgument());}
      else p++;
    }
    if(nested) fail('Missing closing brace');
    return nodes;
  };
  const parseArgument=()=>{
    ws(); const name=readWord();
    if(!name) fail('Missing argument name');
    ws();
    if(input[p]==='}') {p++;return {name,kind:'simple'};}
    if(input[p]!==',') fail(`Expected comma or closing brace after ${name}`);
    p++;ws();const kind=readWord().toLowerCase();ws();
    if(!kind) fail('Missing argument format');
    if(input[p]==='}') {p++;return {name,kind};}
    if(input[p]!==',') fail('Expected comma after argument format');
    p++;ws();
    if(['plural','selectordinal','select'].includes(kind)) {
      let offset=0;
      if(kind!=='select' && input.slice(p).startsWith('offset:')){
        p+=7;const value=readWord();
        if(!/^\d+$/.test(value)) fail('Invalid plural offset');
        offset=Number(value);ws();
      }
      const options=Object.create(null);
      while(p<length && input[p]!=='}'){
        const selector=readWord();
        if(!selector) fail('Missing selector');
        if(Object.hasOwn(options,selector)) fail(`Duplicate selector ${selector}`);
        if(kind!=='select' && !(/^(zero|one|two|few|many|other)$/.test(selector)||/^=\d+$/.test(selector))) fail(`Invalid plural selector ${selector}`);
        ws();if(input[p]!=='{') fail(`Missing block for selector ${selector}`);
        p++; options[selector]=parseMessage(true);ws();
      }
      if(input[p]!=='}') fail('Missing argument closing brace');
      p++;
      if(!Object.hasOwn(options,'other')) fail(`${kind} requires an other branch`);
      return {name,kind,options,offset};
    }
    // Format styles can contain apostrophe literals but not an unquoted brace.
    let style='';
    while(p<length && input[p]!=='}') {
      if(input[p]==='{') fail('Unexpected nested brace in format style');
      style+=input[p++];
    }
    if(input[p]!=='}') fail('Missing argument closing brace');
    p++;
    return {name,kind,style:style.trim()};
  };
  try {return {nodes:parseMessage(),errors};}
  catch(e) {errors.push(e.message);return {nodes:[],errors};}
}
function inspectICU(text) {
  const parsed=parseICU(text ?? '');
  const args=new Map(), plurals=new Map();
  function visit(nodes){for(const n of nodes){
    if(!args.has(n.name)) args.set(n.name,new Set());
    args.get(n.name).add(n.kind);
    if(n.options){if(['plural','selectordinal'].includes(n.kind)) plurals.set(n.name,{kind:n.kind,selectors:Object.keys(n.options),offset:n.offset});
      Object.values(n.options).forEach(visit);}
  }}
  if(!parsed.errors.length) visit(parsed.nodes);
  return {errors:parsed.errors,arguments:Object.fromEntries([...args].map(([k,v])=>[k,[...v]])),plurals:Object.fromEntries(plurals),nodes:parsed.nodes};
}
function validatePair(source,target,key) {
  const problems=[];
  if(target===undefined){problems.push({key,kind:'missing',message:'No target translation for this source key.'});return problems;}
  const a=inspectICU(source??''), b=inspectICU(target);
  a.errors.forEach(x=>problems.push({key,kind:'source_icu',message:`Source ICU: ${x}`}));
  b.errors.forEach(x=>problems.push({key,kind:'icu',message:`Target ICU: ${x}`}));
  if(a.errors.length||b.errors.length)return problems;
  const sk=Object.keys(a.arguments),tk=Object.keys(b.arguments);
  const missing=sk.filter(x=>!tk.includes(x)),extra=tk.filter(x=>!sk.includes(x));
  if(missing.length||extra.length)problems.push({key,kind:'placeholder',message:[missing.length&&`Missing: ${missing.map(x=>`{${x}}`).join(', ')}`,extra.length&&`Extra: ${extra.map(x=>`{${x}}`).join(', ')}`].filter(Boolean).join(' · '),missing,extra});
  for(const name of sk.filter(x=>tk.includes(x))){
    const srcKinds=a.arguments[name], dstKinds=b.arguments[name];
    if(srcKinds.some(x=>!dstKinds.includes(x)) || dstKinds.some(x=>!srcKinds.includes(x))) problems.push({key,kind:'icu',message:`{${name}} uses ${srcKinds.join('/')} in source but ${dstKinds.join('/')} in target.`});
    const selectNodes=nodes=>nodes.flatMap(n=>[...(n.name===name && n.kind==='select'?[n]:[]),...Object.values(n.options||{}).flatMap(selectNodes)]);
    const sourceSelect=selectNodes(a.nodes),targetSelect=selectNodes(b.nodes);
    for(let i=0;i<sourceSelect.length;i++){const missing=Object.keys(sourceSelect[i].options).filter(s=>!targetSelect[i]?.options?.[s]);if(missing.length)problems.push({key,kind:'icu',message:`{${name}} lacks select branches: ${missing.join(', ')}.`});}
    if(a.plurals[name]){
      const sp=a.plurals[name],tp=b.plurals[name];
      if(!tp)continue;
      const omitted=sp.selectors.filter(x=>x.startsWith('=') && !tp.selectors.includes(x));
      if(omitted.length) problems.push({key,kind:'plural',message:`{${name}} lacks source plural branches: ${omitted.join(', ')}.`});
    }
  }
  return problems;
}
function validateSelected(sourceData,selectedTarget){
  const problems=[];
  for(const [key,source] of Object.entries(sourceData)) problems.push(...validatePair(source,selectedTarget[key],key));
  return problems;
}
function parseCSV(input) {
  const result=[];let row=[],cell='',quoted=false;
  const text=input.replace(/^\uFEFF/,'');
  for(let i=0;i<text.length;i++){
    const c=text[i];
    if(quoted){if(c==='"'&&text[i+1]==='"'){cell+='"';i++;}else if(c==='"')quoted=false;else cell+=c;}
    else if(c==='"'){if(cell)throw Error('Unexpected quote inside CSV cell.');quoted=true;}
    else if(c===','){row.push(cell);cell='';}
    else if(c==='\n'){row.push(cell);result.push(row);row=[];cell='';}
    else if(c==='\r'){if(text[i+1]==='\n')continue;row.push(cell);result.push(row);row=[];cell='';}
    else cell+=c;
  }
  if(quoted)throw Error('Unclosed CSV quotation.');
  if(cell||row.length){row.push(cell);result.push(row);}
  if(!result.length)return [];
  const headers=result.shift().map(h=>h.trim().toLowerCase());
  for(const required of ['ru','kk','en']) if(!headers.includes(required))throw Error(`Glossary CSV is missing the ${required} column.`);
  return result.filter(r=>r.some(c=>c.trim())).map((r,i)=>{
    const term=Object.fromEntries(headers.map((h,j)=>[h,r[j]||'']));
    return {...term,id:`import-term-${i}`,notes:term.notes||term.comment||term.usage||'',scope:term.scope||'',
      status:(!headers.includes('status')||['agreed','consumer_agreed','approved','human_approved'].includes(term.status))?'agreed':'proposed',
      variants:(term.variants||term.kk_variants||'').split('|').map(x=>x.trim()).filter(Boolean)};
  });
}
function csvEncode(rows){return rows.map(r=>r.map(x=>{let s=String(x??'');return /[",\n\r]/.test(s)?`"${s.replace(/"/g,'""')}"`:s;}).join(',')).join('\r\n')+'\r\n';}

/* === checks.js === */

const ISSUE_TYPES = [
  ['untranslated','Missing translation','high','The target file has no message for this source key.'],
  ['same_as_source','Matches source','medium','Determine whether matching Russian text is intentional (brand, name, number) or untranslated content.'],
  ['placeholder_mismatch','Placeholder mismatch','high','Required named placeholders differ from the Russian message.'],
  ['plural_mismatch','ICU / plural structure','high','A plural branch or ICU message structure is invalid or differs from the source.'],
  ['glossary_violation','Terminology conflict','medium','Compare wording against the agreed glossary, allowing inflection and game slang.'],
  ['length_overflow','Possible length risk','medium','Long text may not fit a short UI label. Actual clipping has not been measured.'],
  ['machine_translation_suspect','Unnatural phrasing','medium','Review the cited grammar or literal translation; no automated linguistic judgment is definitive.'],
  ['inconsistent_term','Inconsistent terminology','medium','Compare usage and context across related entries.'],
  ['suffix_harmony','Dynamic suffix','high','A suffix after a placeholder may be wrong for some runtime values. Rephrase or require code handling.'],
  ['orthography','Kazakh spelling','medium','Inspect suspect Kazakh letters or misspellings, including ә ғ қ ң ө ұ ү һ і.'],
  ['wrong_language','Possible wrong language','high','Check whether the content is from another locale rather than an accepted name or borrowed word.']
];
const issueInfo = Object.fromEntries(ISSUE_TYPES.map(([code,label,severity,help])=>[code,{code,label,severity,help}]));
const localMethods = ['Missing keys','Identical strings','Placeholder contracts','ICU plural syntax','Dynamic suffix patterns','Length heuristics','Agreed glossary literal-match heuristics'];
function make(key,issue_type,evidence,suggestion=null,severity,confidence=null){
  return {id:`local:${hashText(key+':'+issue_type+':'+evidence)}`,key,issue_type,severity:severity||issueInfo[issue_type]?.severity||'medium',evidence,suggestion,confidence,origin:'local',judgments:[]};
}
function count(s){return [...(s||'')].length;}
function scanEntry(entry,glossary=[],allEntries={}) {
  const {key,source}=entry;
  const target=entry.workingText;
  const findings=[];
  if(source===undefined)return findings;
  if(target===undefined||target===null){findings.push(make(key,'untranslated','The source file has this key, but the target ARB does not.',null,'high'));return findings;}
  if(!entry.sourceMeta?.['x-locqa']?.allow_same_as_source && source.trim() && source.trim()===target.trim() && /[A-Za-zА-Яа-яӘәҒғҚқҢңӨөҰұҮүҺһІі]/u.test(source))findings.push(make(key,'same_as_source','Source and target text are identical. This could be an intentional brand, name, number, or borrowed term.',null,'medium'));
  const structural=validatePair(source,target,key);
  const ph=structural.filter(x=>x.kind==='placeholder');
  if(ph.length)findings.push(make(key,'placeholder_mismatch',ph.map(x=>x.message).join(' · '),null,'high'));
  const icu=structural.filter(x=>['plural','icu','source_icu'].includes(x.kind));
  if(icu.length)findings.push(make(key,'plural_mismatch',icu.map(x=>x.message).join(' · '),null,'high'));
  if((!entry.targetLocale||entry.targetLocale.toLowerCase().startsWith('kk')) && /\{[a-zA-Z_][\w]*\}[-–](?:ға|ге|қа|ке|ды|ді|ты|ті|ның|нің|дың|дің|дан|ден|тан|тен)(?=$|[\s,.!?;:])/u.test(target))
    findings.push(make(key,'suffix_harmony','The suffix immediately after a runtime placeholder is fixed. The correct Kazakh suffix may depend on the player name.',typeof entry.sourceMeta?.['x-dalel']?.proposal==='string'?entry.sourceMeta['x-dalel'].proposal:null,'high'));
  if(/(?:button|tab|title|label|cta|badge|action|menu)/i.test(key) && count(source)>=4 && count(target)>Math.max(count(source)*1.65,count(source)+7))
    findings.push(make(key,'length_overflow',`${count(target)} target characters vs ${count(source)} source characters; the key suggests a compact UI element. Layout dimensions are unavailable.`,null,'medium'));
  for(const term of glossary.filter(x=>x.status==='agreed' && x.ru.trim() && x.kk.trim())) {
    if(term.scope && entry.sourceMeta?.['x-dalel']?.concept!==term.scope)continue;
    const forms=[term.ru,...String(term.ru_variants||'').split('|')].filter(Boolean);
    const hasSource=forms.some(v=>source.toLocaleLowerCase().includes(v.toLocaleLowerCase()));
    if(!hasSource)continue;
    const accepted=[term.kk,...(term.variants||[])].filter(Boolean);
    const hasTarget=accepted.some(x=>target.toLocaleLowerCase().includes(x.toLocaleLowerCase()));
    if(!hasTarget){
      findings.push(make(key,'glossary_violation',`The Russian text contains “${term.ru}”, but the target lacks the agreed form “${term.kk}”. This is a literal-match heuristic: inflections and slang require human judgment.`,null,'medium'));
      const others=Object.values(allEntries).filter(e=>e.key!==key&&e.source?.toLocaleLowerCase().includes(term.ru.toLocaleLowerCase()) && accepted.some(v=>e.workingText?.toLocaleLowerCase().includes(v.toLocaleLowerCase())));
      if(others.length)findings.push(make(key,'inconsistent_term',`“${term.ru}” has the agreed form in ${others.length} other entr${others.length===1?'y':'ies'}, including ${others.slice(0,2).map(x=>x.key).join(', ')}. Compare the contexts before standardizing.`,null,'medium'));
    }
  }
  return findings;
}
function manualFinding(key,issue_type,evidence,severity='medium',suggestion='') {
  return {id:`manual:${cryptoId()}`,key,issue_type,severity,evidence:evidence.trim(),suggestion:suggestion.trim()||null,confidence:null,origin:'manual',judgments:[]};
}
function cryptoId(){return Math.random().toString(36).slice(2,10)+Date.now().toString(36);}
function checkAll(ws){
  const startedAt=new Date().toISOString();let count=0;
  for(const entry of Object.values(ws.entries)) {
    const old=new Map((entry.localFindings||[]).map(f=>[`${f.issue_type}|${f.evidence}`,f]));
    entry.localFindings=scanEntry(entry,ws.glossary,ws.entries).map(f=>{
      const prev=old.get(`${f.issue_type}|${f.evidence}`);
      return {...f,revisionId:entry.currentRevisionId,sourceHash:entry.sourceHash,judgments:prev?.judgments||[]};
    });
    entry.checkedRevisionId=entry.currentRevisionId;
    entry.checkedGlossaryVersion=ws.glossaryVersion;
    entry.terminologyStale=false;
    count+=entry.localFindings.length;
  }
  ws.checks={status:'complete',checkedAt:startedAt,scope:'all',localFindingCount:count,glossaryVersion:ws.glossaryVersion,methods:localMethods};
  return ws.checks;
}
function currentFindings(ws,entry) {
  const importedCodes=new Set((entry.importedFindings||[]).filter(f=>findingCurrent(ws,entry,f)).map(f=>f.issue_type));
  const current=[...(entry.localFindings||[]).filter(f=>!importedCodes.has(f.issue_type)),...(entry.importedFindings||[]),...(entry.manualFindings||[])];
  return current;
}
function findingJudgment(entry,f){
  const values=(f.judgments||[]).filter(j=>j.revisionId===entry.currentRevisionId && j.sourceHash===entry.sourceHash);
  return values.length?values.at(-1).value:'unassessed';
}
function findingsStatus(ws,entry){
  return entry.checkedRevisionId!==entry.currentRevisionId || entry.terminologyStale?'stale':ws.checks.status==='complete'?'checked':'unchecked';
}

/* === model.js === */



const now=()=>new Date().toISOString();
const uniq=()=>`${Date.now().toString(36)}-${cryptoId()}`;
function createWorkspace({source,target,references=[],glossary=[],report=[],sourceLocale='ru',targetLocale='kk',name='Localization review',demo=false,history=[]}) {
  if(sourceLocale===targetLocale)throw Error('Source and target locales must differ.');
  if(!localeCompatible(source.locale,sourceLocale))throw Error(`Source file declares ${source.locale}, but its role is ${sourceLocale}.`);
  if(!localeCompatible(target.locale,targetLocale))throw Error(`Target file declares ${target.locale}, but its role is ${targetLocale}.`);
  const seen=new Set([sourceLocale.toLowerCase(),targetLocale.toLowerCase()]);
  for(const ref of references){
    if(!ref.selectedLocale)throw Error(`Set a locale for reference ${ref.name}.`);
    if(seen.has(ref.selectedLocale.toLowerCase()))throw Error(`Duplicate locale ${ref.selectedLocale} in reference files.`);
    if(!localeCompatible(ref.locale,ref.selectedLocale))throw Error(`${ref.name} declares ${ref.locale}, not ${ref.selectedLocale}.`);
    seen.add(ref.selectedLocale.toLowerCase());
  }
  const workspace={schema:1,id:uniq(),createdAt:now(),name,demo,sourceLocale,targetLocale,
    snapshots:{source,target,references},historicImports:history,entries:Object.create(null),order:[],glossary:glossary.map(t=>({...t,id:t.id||uniq(),status:t.status==='agreed'?'agreed':'proposed',variants:t.variants||[]})),glossaryVersion:1,
    checks:{status:'not_run',checkedAt:null,scope:'none'},releases:[],
    session:{id:`local-${cryptoId().slice(0,8)}`,label:'',nativeSelfDeclared:false},
    ui:{view:'review',filter:'attention',query:'',selectedKey:null,sort:'source',inspector:'issues',glossaryQuery:'',glossarySelected:null,releaseMode:'validated',queueScroll:0},
    processing:{externalCalls:0,externalCost:0,provider:'None — local-only'},
    createdFrom:demo?'Synthetic demonstration':'Local ARB files'};
  const keys=[...new Set([...Object.keys(source.messages),...Object.keys(target.messages)])];
  for(const key of keys){
    const sourceText=source.messages[key], original=target.messages[key]??null;
    const revId=`import-${hashText(key+'\0'+(original??'missing'))}`;
    const entry={key,targetLocale,source:sourceText,sourceHash:hashText(key+'\0'+(sourceText??'')),originalTarget:original,workingText:original,
      sourceMeta:source.data['@'+key]??null,targetMeta:target.data['@'+key]??null,
      description:source.data['@'+key]?.description||target.data['@'+key]?.description||'',
      contextOrigin:source.data['@'+key]?.description?'provided':'inferred',
      refs:Object.fromEntries(references.map(r=>[r.selectedLocale,r.messages[key]??null])),
      revisions:[{id:revId,text:original,createdAt:now(),reason:'Imported target',author:'Imported file'}],currentRevisionId:revId,
      decisions:[],undoStack:[],redoStack:[],manualFindings:[],importedFindings:[],localFindings:[],checkedRevisionId:null,checkedGlossaryVersion:0,terminologyStale:false};
    workspace.entries[key]=entry;
  }
  workspace.order=keys;
  for(const f of report){
    if(!f||!f.key||!workspace.entries[f.key])continue;
    if(!f.issue_type)continue;
    const entry=workspace.entries[f.key];
    entry.importedFindings.push({id:f.id||`imported-${cryptoId()}`,key:f.key,issue_type:f.issue_type,
      severity:['high','medium','low'].includes(f.severity)?f.severity:'medium',
      evidence:String(f.evidence??'Imported finding. Inspect the entry.'),suggestion:typeof f.suggestion==='string'?f.suggestion:null,
      confidence:typeof f.confidence==='number'&&f.confidence>=0&&f.confidence<=1?f.confidence:null,
      origin:demo?'synthetic':'imported',basisRevisionId:entry.currentRevisionId,sourceHash:entry.sourceHash,
      source:f.source??entry.source,target:f.target??entry.workingText,judgments:[]});
  }
  checkAll(workspace);
  workspace.ui.selectedKey=(report[0]&&workspace.entries[report[0].key]?report[0].key:null) || keys.find(k=>currentFindings(workspace,workspace.entries[k]).length) || keys[0] || null;
  return workspace;
}
const isChanged=e=>e.workingText!==e.originalTarget;
function currentDecision(e){return [...e.decisions].reverse().find(x=>x.revisionId===e.currentRevisionId && x.sourceHash===e.sourceHash && (typeof Backend==='undefined'||(typeof e.glossaryFingerprint==='string'&&x.glossary_sha256===e.glossaryFingerprint)))||null;}
const isApproved=e=>currentDecision(e)?.action==='approve';
const isDeferred=e=>currentDecision(e)?.action==='defer';
const isCodeDependency=e=>currentDecision(e)?.action==='code_dependency';
const isRequested=e=>currentDecision(e)?.action==='request_changes';
function entryTechnical(e){if(typeof Backend!=='undefined'){const v=e.backendValidation;if(v&&v.revisionId===e.currentRevisionId&&v.text===e.workingText)return v.errors;return [{key:e.key,kind:'pending',message:'Save this revision to validate it with dalel.'}];}if(e.source===undefined)return [];return validatePair(e.source,e.workingText??undefined,e.key);}
function entryStatus(ws,e){
  const d=currentDecision(e);
  if(d?.action==='code_dependency')return {label:'Code needed',code:'code'};
  if(d?.action==='defer')return {label:'Deferred',code:'deferred'};
  if(d?.action==='request_changes')return {label:'Changes requested',code:'request'};
  if(d?.action==='approve')return {label:'Approved version',code:'approved'};
  if(isChanged(e))return {label:'Unreviewed change',code:'draft'};
  if(entryTechnical(e).length)return {label:'Format needs attention',code:'finding'};
  const chk=findingsStatus(ws,e);
  if(chk==='stale')return {label:'Check out of date',code:'stale'};
  if(chk==='unchecked')return {label:'Not checked',code:'unchecked'};
  const unresolved=currentFindings(ws,e).filter(f=>findingCurrent(ws,e,f)&&latestJudgment(e,f)!=='dismissed');
  if(unresolved.length)return {label:'Needs attention',code:'finding'};
  if(ws.backend&&!ws.backend.analysis.language_complete)return {label:'Language unchecked',code:'unchecked'};
  return {label:'Checked · no open findings',code:'checked'};
}
function findingCurrent(ws,e,f){return (f.revisionId||f.basisRevisionId)===e.currentRevisionId && (f.glossaryVersion??1)===ws.glossaryVersion;}
function latestJudgment(e,f){const j=[...(f.judgments||[])].reverse().find(x=>x.revisionId===e.currentRevisionId && x.sourceHash===e.sourceHash && (typeof Backend==='undefined'||x.glossary_sha256===e.glossaryFingerprint));return j?.value||'unassessed';}
function beginNewRevision(ws,key,text,reason='Edited draft',author='Local reviewer',recordUndo=true){
  const e=ws.entries[key];if(!e || e.workingText===text)return false;
  if(recordUndo){e.undoStack??=[];e.redoStack??=[];e.undoStack.push(e.workingText);e.redoStack=[];}
  const revision={id:`rev-${uniq()}`,text,createdAt:now(),reason,author};
  e.revisions.push(revision);e.currentRevisionId=revision.id;e.workingText=text;
  return true;
}
function updateCurrentRevision(ws,key,text,revisionId){
  const e=ws.entries[key];if(e.currentRevisionId!==revisionId)return beginNewRevision(ws,key,text);
  if(e.workingText===text)return false;
  const r=e.revisions.at(-1);
  if(r.id!==revisionId||e.decisions.some(d=>d.revisionId===revisionId)||(ws.backend&&(e.checkedRevisionId===revisionId||[...e.localFindings,...e.manualFindings,...e.importedFindings].some(f=>(f.revisionId||f.basisRevisionId)===revisionId))))return beginNewRevision(ws,key,text);
  r.text=text;e.workingText=text;return true;
}
function restoreImported(ws,key){return beginNewRevision(ws,key,ws.entries[key].originalTarget,'Restored imported translation');}
function undoRevision(ws,key){
  const e=ws.entries[key];e.undoStack??=[];e.redoStack??=[];
  if(!e.undoStack.length)return false;
  const previous=e.undoStack.pop();e.redoStack.push(e.workingText);
  return beginNewRevision(ws,key,previous,'Undo edit','Local reviewer',false);
}
function redoRevision(ws,key){
  const e=ws.entries[key];e.undoStack??=[];e.redoStack??=[];
  if(!e.redoStack.length)return false;
  const next=e.redoStack.pop();e.undoStack.push(e.workingText);
  return beginNewRevision(ws,key,next,'Redo edit','Local reviewer',false);
}
function review(ws,key,action){
  const e=ws.entries[key];
  if(!e)return false;
  if(action==='approve' && e.workingText===null)throw Error('Write the missing translation before approving this version.');
  if(!['approve','defer','request_changes','code_dependency'].includes(action))throw Error('Unsupported review decision.');
  e.decisions.push({id:uniq(),action,revisionId:e.currentRevisionId,sourceHash:e.sourceHash,at:now(),
    reviewer:ws.session.label.trim()||ws.session.id,sessionId:ws.session.id,nativeSelfDeclared:!!ws.session.nativeSelfDeclared});
  return true;
}
function judgeFinding(ws,key,findingId,value){
  if(!['confirmed','dismissed'].includes(value))throw Error('Unsupported finding decision.');
  const e=ws.entries[key];const f=[...(e.localFindings||[]),...e.importedFindings,...e.manualFindings].find(x=>x.id===findingId);
  if(!f)throw Error('Finding not found.');
  f.judgments??=[];
  f.judgments.push({value,revisionId:e.currentRevisionId,sourceHash:e.sourceHash,at:now(),reviewer:ws.session.label.trim()||ws.session.id});
}
function addFinding(ws,key,type,evidence,severity,suggestion){const f=manualFinding(key,type,evidence,severity,suggestion);f.basisRevisionId=ws.entries[key].currentRevisionId;f.sourceHash=ws.entries[key].sourceHash;ws.entries[key].manualFindings.push(f);return f;}
function matchingForm(haystack,value){
  if(!value||!haystack)return false;
  const h=haystack.normalize('NFC').toLocaleLowerCase(),v=value.trim().normalize('NFC').toLocaleLowerCase();
  if(!v)return false;
  const literal=v.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const word='\\p{L}\\p{M}\\p{N}_';
  // An abbreviation is a token, never an occurrence of its letters in a word.
  // A dotted compound such as a power unit must not become a volume-unit hit.
  const compound=v.length===1?'(?!\\.[\\p{L}\\p{M}])':'';
  const end=compound+'(?=$|[^'+word+'])';
  if(new RegExp('(^|[^'+word+'])'+literal+end,'u').test(h))return true;
  // Units may immediately follow a numeric value without a separating space.
  return /^[\p{L}\p{M}]{1,3}$/u.test(v)&&
    new RegExp('(^|[^'+word+'])\\d+(?:[.,]\\d+)?'+literal+end,'u').test(h);
}
function termForms(ws,term){
  const locale=ws.targetLocale.split(/[-_]/)[0],rule=term._rule||{};
  const forms=value=>Array.isArray(value)?value.filter(v=>typeof v==='string'):[];
  return [...new Set([term.ru,...String(term.ru_variants||'').split('|'),...forms(rule.source_forms),
    term[locale],...(locale==='kk'?forms(term.variants):String(term[locale+'_variants']||'').split('|')),
    ...forms(rule.target_term===term[locale]?rule.allowed_variants:[])].filter(v=>typeof v==='string'&&v.trim()))];
}
function relevantTerms(ws,e){return ws.glossary.filter(t=>[e.source||'',e.workingText||'',e.originalTarget||''].some(str=>termForms(ws,t).some(v=>matchingForm(str,v))));}
function termOccurrences(ws,term){return Object.values(ws.entries).filter(e=>[e.source||'',e.workingText||'',e.originalTarget||''].some(str=>termForms(ws,term).some(v=>matchingForm(str,v))));}
function upsertTerm(ws,term){
  if(!term.ru.trim()||!term.kk.trim()||!term.en.trim())throw Error('Russian, Kazakh and English terms are required.');
  const normalized={...term,id:term.id||`term-${uniq()}`,ru:term.ru.trim(),kk:term.kk.trim(),en:term.en.trim(),notes:(term.notes||'').trim(),status:term.status==='agreed'?'agreed':'proposed',variants:(term.variants||[]).filter(Boolean)};
  const old=ws.glossary.find(t=>t.id===normalized.id);
  ws.glossary=old?ws.glossary.map(t=>t.id===normalized.id?normalized:t):[...ws.glossary,normalized];
  ws.glossaryVersion++;
  for(const e of Object.values(ws.entries)){
    if(e.decisions?.length){e.reviewHistory??=[];e.reviewHistory.push(...e.decisions);e.decisions=[];}
    if([old,normalized].filter(Boolean).some(t=>[e.source||'',e.workingText||'',e.originalTarget||''].some(s=>termForms(ws,t).filter(Boolean).some(v=>matchingForm(s,v)))))e.terminologyStale=true;
  }
  return normalized;
}
function deleteTerm(ws,id){
  const term=ws.glossary.find(t=>t.id===id);if(!term)return;
  for(const e of Object.values(ws.entries)){e.terminologyStale=true;if(e.decisions?.length){e.reviewHistory??=[];e.reviewHistory.push(...e.decisions);e.decisions=[];}}
  ws.glossary=ws.glossary.filter(t=>t.id!==id);ws.glossaryVersion++;
}
function counts(ws){
  const all=Object.values(ws.entries);let findings=0,affected=0,open=0;
  for(const e of all){const f=currentFindings(ws,e);findings+=f.length;if(f.length)affected++;open+=f.filter(x=>latestJudgment(e,x)==='unassessed').length;}
  return {entries:all.length,findings,affected,open,changed:all.filter(isChanged).length,approved:all.filter(isApproved).length,
    changedApproved:all.filter(e=>isChanged(e)&&isApproved(e)).length,drafts:all.filter(e=>isChanged(e)&&!isApproved(e)).length,
    deferred:all.filter(isDeferred).length,code:all.filter(isCodeDependency).length,
    unchecked:all.filter(e=>!e.checkedRevisionId).length,
    stale:all.filter(e=>findingsStatus(ws,e)==='stale'||e.terminologyStale).length,
    nativeReviewed:all.filter(e=>{const d=currentDecision(e);return d?.action==='approve' && d.nativeSelfDeclared;}).length};
}
const severityRank={high:0,medium:1,low:2};
function visibleEntries(ws){
  const q=(ws.ui.query||'').trim().toLocaleLowerCase(),filter=ws.ui.filter;
  let list=ws.order.map(k=>ws.entries[k]);
  if(q)list=list.filter(e=>[e.key,e.source,e.originalTarget,e.workingText,e.description,...Object.values(e.refs||{})].some(s=>String(s??'').toLocaleLowerCase().includes(q)));
  list=list.filter(e=>{
    if(filter==='all')return true;
    if(filter==='attention')return !isApproved(e) && (currentFindings(ws,e).some(f=>latestJudgment(e,f)!=='dismissed' && (f.revisionId||f.basisRevisionId)===e.currentRevisionId) || isChanged(e) || isDeferred(e) || isCodeDependency(e) || entryTechnical(e).length>0);
    if(filter==='flagged')return currentFindings(ws,e).length>0;
    if(filter==='drafts')return isChanged(e)&&!isApproved(e);
    if(filter==='deferred')return isDeferred(e);
    if(filter==='code')return isCodeDependency(e);
    if(filter==='approved')return isApproved(e);
    if(filter==='unchecked')return findingsStatus(ws,e)==='unchecked'||findingsStatus(ws,e)==='stale'||e.terminologyStale;
    if(filter==='clean')return currentFindings(ws,e).length===0 && findingsStatus(ws,e)==='checked' && (!ws.backend||ws.backend.analysis.language_complete);
    return true;
  });
  if(ws.ui.sort==='severity')list.sort((a,b)=>{
    const rank=e=>Math.min(3,...currentFindings(ws,e).filter(f=>latestJudgment(e,f)==='unassessed').map(f=>severityRank[f.severity]??3));
    return rank(a)-rank(b)||ws.order.indexOf(a.key)-ws.order.indexOf(b.key);
  });
  return list;
}

/* === export.js === */



const escapeHtml=s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
function buildRelease(ws,mode='validated') {
  if(!['validated','draft'].includes(mode))throw Error('Unknown release mode.');
  const c=counts(ws),at=new Date().toISOString(),decisions=Object.create(null),selected=Object.create(null),selectedRevisions=Object.create(null),changes=[];
  for(const key of ws.order){
    const e=ws.entries[key], changed=isChanged(e),approved=isApproved(e);
    const include=changed && (mode==='draft'||approved);
    selected[key]=include?e.workingText:e.originalTarget;
    selectedRevisions[key]=include?e.currentRevisionId:e.revisions[0].id;
    decisions[key]=include?currentDecision(e):null;
    if(include)changes.push({key,from:e.originalTarget,to:selected[key],revisionId:selectedRevisions[key],approved:approved&&!!decisions[key],reviewer:decisions[key]?.reviewer||null,reviewed_by_human:approved&&!!decisions[key],nativeSelfDeclared:approved&&!!decisions[key]?.nativeSelfDeclared});
  }
  const src=ws.snapshots.source.messages;
  const errors=validateSelected(src,Object.fromEntries(Object.entries(selected).filter(([,v])=>v!==null)));
  const codeDependencies=ws.order.filter(k=>isCodeDependency(ws.entries[k]));
  const blocked=mode==='validated' && (errors.length>0||codeDependencies.length>0);
  const target=structuredClone(ws.snapshots.target.data);
  for(const ch of changes){
    target[ch.key]=ch.to;
    const e=ws.entries[ch.key];
    const inherited=e.targetMeta ? structuredClone(e.targetMeta) :
      e.sourceMeta?.placeholders ? {placeholders:structuredClone(e.sourceMeta.placeholders)} : {};
    target['@'+ch.key]={...inherited,reviewed_by_human:ch.reviewed_by_human,
      locqaReview:{revision_id:ch.revisionId,reviewed_by_human:ch.reviewed_by_human,
        reviewer:ch.reviewer,reviewed_at:ch.reviewed_by_human?decisions[ch.key]?.at:null,
        native_speaker_self_declared:!!ch.nativeSelfDeclared,source_fingerprint:e.sourceHash}};
  }
  // Original target structure and all unrelated metadata are retained.
  const notes={
    source:ws.snapshots.source.name,target:ws.snapshots.target.name,
    sourceHash:ws.snapshots.source.hash,targetHash:ws.snapshots.target.hash,
    localePair:`${ws.sourceLocale} → ${ws.targetLocale}`,at,mode,
    checks:'Local full-output ICU parser, plural branch and named-placeholder comparison; no Flutter compilation.',
    linguisticAnalysis:'No automatic comprehensive linguistic analysis; any imported or synthetic findings are labeled.',
    externalModelCalls:ws.processing.externalCalls||0,
    externalCost:ws.processing.externalCost||0,externalCostCurrency:'USD',
    nativeSpeakerReviewedChangedEntries:changes.filter(x=>x.nativeSelfDeclared).length,
    nativeSpeakerAttribution:'Self-declared only; not independently verified.'
  };
  const allFindings=[];
  for(const key of ws.order){
    const e=ws.entries[key];
    for(const f of currentFindings(ws,e)){
      // Findings are individually assessed. Approving the text does not imply that
      // unrelated findings have been confirmed or dismissed by a human reviewer.
      const decision=[...(f.judgments||[])].reverse().find(j=>j.revisionId===selectedRevisions[key] && j.sourceHash===e.sourceHash);
      const findingJudgment=decision?.value||'unassessed';
      allFindings.push({key,issue_type:f.issue_type,severity:f.severity,
        source:f.source??e.source??null,target:f.target??e.workingText??null,evidence:f.evidence,
        suggestion:f.suggestion??null,confidence:f.confidence??null,
        reviewed_by_human: ['confirmed','dismissed'].includes(findingJudgment),
        id:f.id,origin:f.origin,checked_revision_id:f.revisionId||f.basisRevisionId||null,
        exported_revision_id:selectedRevisions[key],finding_judgment:findingJudgment,
        current_analysis:(f.revisionId||f.basisRevisionId)===selectedRevisions[key]});
    }
  }
  const report={schema_version:1,product:'dalel',synthetic_demo:ws.demo,created_at:at,
    source_locale:ws.sourceLocale,target_locale:ws.targetLocale,files:{source:notes.source,target:notes.target},
    check_run:ws.checks,methodology:{local_methods:localMethods,external_model_calls:notes.externalModelCalls,external_model_cost_usd:notes.externalCost,flutter_gen_l10n:'not run',native_review_status:'self-declared, not independently verified'},
    counts:{entries:c.entries,findings:allFindings.length,affected_entries:new Set(allFindings.map(f=>f.key)).size,
      exported_changes:changes.length,approved_exported_changes:changes.filter(x=>x.approved).length,
      native_self_declared_exported_changes:notes.nativeSpeakerReviewedChangedEntries,
      unresolved_findings:allFindings.filter(f=>f.finding_judgment==='unassessed').length,structural_blockers:errors.length,code_dependencies:codeDependencies.length},
    structural_validation:{scope:'full selected output',passed:errors.length===0,errors,flutter_compilation:'not run'},
    changes,findings:allFindings};
  const rows=[['ru','kk','en','notes','status','variants','scope','ru_variants'],...ws.glossary.map(t=>[t.ru,t.kk,t.en,t.notes,t.status,(t.variants||[]).join(' | '),t.scope||'',t.ru_variants||''])];
  const filename=`intl_${ws.targetLocale.replace(/[^a-zA-Z0-9_-]/g,'_')}_fixed.arb`;
  const html=`<!doctype html><html lang="en"><head><meta charset="UTF-8"><title>dalel report</title><style>body{font:15px/1.55 system-ui,sans-serif;color:#17242a;max-width:1050px;margin:44px auto;padding:0 24px}h1{font-size:30px;letter-spacing:-.03em}h2{margin-top:40px}table{border-collapse:collapse;width:100%}th,td{text-align:left;border-bottom:1px solid #d9e1e2;padding:12px;vertical-align:top}th{background:#f3f6f5}code{white-space:normal;overflow-wrap:anywhere;background:#f3f6f5;padding:2px 5px}small{color:#687980}.warning{padding:15px;background:#fff4df;border-left:3px solid #9c6a14}p{max-width:75ch}</style></head><body><small>LOCAL REVIEW REPORT · ${escapeHtml(at)}${ws.demo?' · SYNTHETIC DEMONSTRATION':''}</small><h1>dalel</h1><p>${escapeHtml(notes.localePair)} · ${c.entries} entries · ${allFindings.length} findings · ${changes.length} exported changes</p><p class="warning">Structural checks: ${errors.length?'FAILED — '+errors.length+' issues':'passed locally'}. Flutter gen-l10n was not run. Human-review attribution is self-declared; linguistic correctness is not guaranteed.</p><h2>Selected changes</h2><table><thead><tr><th>Key</th><th>Imported</th><th>Exported</th><th>Human review</th></tr></thead><tbody>${changes.map(x=>`<tr><td><code>${escapeHtml(x.key)}</code></td><td>${escapeHtml(x.from??'(missing)')}</td><td>${escapeHtml(x.to)}</td><td>${x.reviewed_by_human?'Approved by '+escapeHtml(x.reviewer):'Unreviewed draft'}</td></tr>`).join('')||'<tr><td colspan="4">No selected changes.</td></tr>'}</tbody></table><h2>Findings</h2><table><thead><tr><th>Key / category</th><th>Evidence</th><th>Status</th></tr></thead><tbody>${allFindings.map(f=>`<tr><td><code>${escapeHtml(f.key)}</code><br>${escapeHtml(issueInfo[f.issue_type]?.label||f.issue_type)} <small>${escapeHtml(f.severity)}</small></td><td>${escapeHtml(f.evidence)}</td><td>${escapeHtml(f.finding_judgment)} · ${escapeHtml(f.origin)}</td></tr>`).join('')||'<tr><td colspan="3">No findings in this snapshot.</td></tr>'}</tbody></table><h2>Structural problems</h2>${errors.length?`<ul>${errors.map(x=>`<li><code>${escapeHtml(x.key)}</code>: ${escapeHtml(x.message)}</li>`).join('')}</ul>`:'<p>No structural errors detected by the local parser in the selected output.</p>'}<h2>Methods and limits</h2><p>${escapeHtml(notes.checks)} No text was uploaded to a service. External model calls: ${notes.externalModelCalls}, recorded cost: $${notes.externalCost} USD. Native-speaker reviewed changed entries (self-declared): ${notes.nativeSpeakerReviewedChangedEntries}. No native credentials were verified.</p></body></html>`;
  const methodology=`# dalel — review summary\n\n- Prepared: ${at}\n- Dataset: ${ws.demo?'SYNTHETIC demonstration':'locally imported private ARB files'}\n- Language pair: ${notes.localePair}\n- Input versions: ${notes.source} (${notes.sourceHash}), ${notes.target} (${notes.targetHash})\n- Release mode: ${mode}\n- Total entries: ${c.entries}\n- Findings represented: ${allFindings.length} across ${report.counts.affected_entries} entries\n- Selected changed entries: ${changes.length}\n- Human-approved selected changed entries: ${report.counts.approved_exported_changes}\n- Native-speaker self-declared reviewed changed entries: ${notes.nativeSpeakerReviewedChangedEntries} (not independently verified)\n- Full selected-output structural errors: ${errors.length}\n- Code-level dependencies: ${codeDependencies.length}\n- Flutter gen-l10n: NOT RUN\n- External model calls: ${notes.externalModelCalls}; recorded cost: USD ${notes.externalCost}\n\n## Detection and correction\n\nLocal checks: ${localMethods.join('; ')}. Suspected errors are review prompts rather than proof. A manual reviewer may confirm or dismiss individual findings, edit drafts, and approve a specific revision. Only the selected text revision is exported. The local parser validates balanced/nested ICU MessageFormat, plural selectors and placeholder identities against source; it does not compile Dart or test rendered layouts. Imported analysis and synthetic examples are labeled.\n\n## Exported ARB review metadata\n\nFor each changed exported message, @key metadata contains \`reviewed_by_human\` and \`locqaReview\` (revision ID, reviewer/session, timestamp, native-speaker self-declaration, source fingerprint). Extra ARB metadata is intended to be ignored by Flutter generators. Compatibility with \`flutter gen-l10n\` has not been tested in this environment; verify in an actual Flutter project.\n\n## Constraints\n\nFull-output structural problems: ${errors.length?errors.map(x=>`${x.key}: ${x.message}`).join('; '):'none found by local checks'}. Code-level dependencies: ${codeDependencies.join(', ')||'none'}. Do not treat local structural validation as proof of translation quality, UI fit, or Flutter compilation.\n`;
  const files={
    [filename]:JSON.stringify(target,null,2)+'\n',
    'report.json':JSON.stringify(report,null,2)+'\n',
    'report.html':html,
    'glossary.csv':csvEncode(rows),
    'methodology.md':methodology
  };
  const id=`release-${hashText(at+JSON.stringify(selectedRevisions)+mode)}`;
  return {id,at,mode,blocked,errors,codeDependencies,counts:report.counts,selectedRevisions,changes,selected,notes,files,filename,workspaceHash:workspaceFingerprint(ws)};
}
function workspaceFingerprint(ws){return hashText(JSON.stringify({entries:ws.order.map(k=>[k,ws.entries[k].currentRevisionId,ws.entries[k].workingText,currentDecision(ws.entries[k])?.action]),glossary:ws.glossary,glossaryVersion:ws.glossaryVersion,mode:ws.ui.releaseMode,session:ws.session,checks:ws.checks,findings:ws.order.map(k=>currentFindings(ws,ws.entries[k])),decisions:ws.order.map(k=>ws.entries[k].decisions)}));}
const encoder=new TextEncoder();
function crc32(bytes){let crc=~0;for(const b of bytes){crc^=b;for(let k=0;k<8;k++)crc=(crc>>>1)^((crc&1)?0xEDB88320:0);}return (~crc)>>>0;}
function put16(arr,x){arr.push(x&255,(x>>>8)&255);}
function put32(arr,x){arr.push(x&255,(x>>>8)&255,(x>>>16)&255,(x>>>24)&255);}
function makeZip(files){
  const output=[],central=[];let offset=0,count=0;
  const date=new Date();const dosTime=(date.getHours()<<11)|(date.getMinutes()<<5)|(Math.floor(date.getSeconds()/2));const dosDate=((date.getFullYear()-1980)<<9)|((date.getMonth()+1)<<5)|date.getDate();
  for(const [name,text] of Object.entries(files)){
    const n=encoder.encode(name),d=encoder.encode(text),crc=crc32(d);const local=[];
    put32(local,0x04034b50);put16(local,20);put16(local,0x0800);put16(local,0);put16(local,dosTime);put16(local,dosDate);put32(local,crc);put32(local,d.length);put32(local,d.length);put16(local,n.length);put16(local,0);local.push(...n);
    output.push(Uint8Array.from(local),d);
    const c=[];put32(c,0x02014b50);put16(c,20);put16(c,20);put16(c,0x0800);put16(c,0);put16(c,dosTime);put16(c,dosDate);put32(c,crc);put32(c,d.length);put32(c,d.length);put16(c,n.length);put16(c,0);put16(c,0);put16(c,0);put16(c,0);put32(c,0);put32(c,offset);c.push(...n);central.push(Uint8Array.from(c));
    offset+=local.length+d.length;count++;
  }
  const centralSize=central.reduce((n,c)=>n+c.length,0),end=[];
  put32(end,0x06054b50);put16(end,0);put16(end,0);put16(end,count);put16(end,count);put32(end,centralSize);put32(end,offset);put16(end,0);
  return new Blob([...output,...central,Uint8Array.from(end)],{type:'application/zip'});
}
function downloadFile(name,text,type='text/plain;charset=utf-8') {downloadBlob(name,new Blob([text],{type}));}
function downloadBlob(name,blob){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);}

/* === storage.js === */
const DB='qazaq-loc-qa-merge-v1',STORE='workspace';
function openDb(){return new Promise((resolve,reject)=>{
  if(!('indexedDB' in window)){reject(Error('IndexedDB is unavailable in this browser.'));return;}
  const req=indexedDB.open(DB,1);
  req.onupgradeneeded=()=>{if(!req.result.objectStoreNames.contains(STORE))req.result.createObjectStore(STORE);};
  req.onsuccess=()=>resolve(req.result);
  req.onerror=()=>reject(req.error||Error('Unable to open local database.'));
});}
async function transact(mode,action){const db=await openDb();return new Promise((resolve,reject)=>{
  const t=db.transaction(STORE,mode),store=t.objectStore(STORE);let result;
  let settled=false;
  t.oncomplete=()=>{db.close();if(!settled){settled=true;resolve(result);}};
  t.onerror=()=>{db.close();if(!settled){settled=true;reject(t.error||Error('Local database transaction failed.'));}};
  t.onabort=()=>{db.close();if(!settled){settled=true;reject(t.error||Error('Local database transaction aborted.'));}};
  try{const req=action(store);req.onsuccess=()=>{result=req.result;};req.onerror=()=>{if(!settled){settled=true;db.close();reject(req.error||Error('Local save failed.'));}};}catch(e){reject(e);}
});}
const loadWorkspace=()=>typeof Backend!=='undefined'?Backend.current():transact('readonly',s=>s.get('current'));
const saveWorkspace=workspace=>typeof Backend!=='undefined'?Backend.save(workspace):transact('readwrite',s=>s.put(structuredClone(workspace),'current'));
const clearWorkspace=()=>typeof Backend!=='undefined'?Backend.clear():transact('readwrite',s=>s.delete('current'));
