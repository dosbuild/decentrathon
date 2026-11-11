/* Code-only integration: use the real fetch bridge and renderers, no browser. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');
const assets=process.argv[2],base=process.argv[3];
const root={addEventListener(){},querySelector(){return null;},querySelectorAll(){return [];}};
const document={querySelector(){return root;},addEventListener(){}};
const context=vm.createContext({document,window:{addEventListener(){}},fetch:(url,options)=>fetch(base+url,options),crypto:webcrypto,structuredClone,TextEncoder,Blob,FormData,setTimeout,clearTimeout,queueMicrotask,console});
const code=['core','backend','app'].map(name=>fs.readFileSync(assets+'/src/'+name+'.js','utf8')).join('\n').replace(/\nboot\(\);\s*$/,'');
vm.runInContext(code+`\nglobalThis.testUI={Backend,edit:beginNewRevision,approve:isApproved,technical:entryTechnical,hydrate:hydrateSaved,set(w){ws=w;termId=w.glossary[0]?.id;},get(){return ws;},review:reviewView,glossary:glossaryView,release(r){releasePreview=r;releasePreviewKey=workspaceFingerprint(ws);return exportView();}};`,context);
(async()=>{
 const ui=context.testUI,b=ui.Backend;assert.equal((await b.init()).simulation,true);assert.equal(await b.current(),null);
 const source=JSON.stringify({'@@locale':'en',message:'Ready'}),target=JSON.stringify({'@@locale':'de',message:'Bereit'});
 let w=await b.create({name:'Bridge contract',sourceLocale:'en',targetLocale:'de',snapshots:{source:{name:'en.arb',raw:source},target:{name:'de.arb',raw:target},references:[]},glossary:[]});assert.equal(w.demo,false);assert.equal(w.processing.externalCalls,0);
 ui.set(w);assert.match(ui.review(),/English source/);assert.match(ui.glossary(),/Build your glossary/);
 const key=w.order[0];ui.edit(w,key,'Noch bereit');w=await b.save(w);ui.set(w);
 assert.equal(w.entries[key].workingText,'Noch bereit');assert.equal(ui.technical(w.entries[key]).length,0);assert.equal(ui.approve(w.entries[key]),false);
 w=await b.operation(w,'analyze');assert.equal(w.checks.status,'complete');ui.set(w);
 w=await b.operation(w,'review',{key,revisionId:w.entries[key].currentRevisionId,text:w.entries[key].workingText,action:'approve',reviewer:'Code-only simulation',attestHuman:true});
 assert.equal(ui.approve(w.entries[key]),false);assert.equal(w.entries[key].reviewHistory.at(-1).action,'simulated_approval');
 let r=await b.preview(w,'validated');ui.set(w);assert.match(ui.release(r),/1 unreviewed draft is excluded/);assert.match(ui.release(r),/Run Flutter check/);
 w.ui.releaseMode='draft';w=await b.save(w);r=await b.preview(w,'draft');ui.set(w);assert.match(ui.release(r),/unreviewed change is included/);
 w=await b.operation(w,'prepare',{mode:'draft'});const frozen=w.releases.at(-1);assert.equal(frozen.changes.length,1);assert.equal(frozen.changes[0].reviewed_by_human,false);
 assert.ok((await b.download(w,frozen,'package.zip')).size>1000);
 // A delayed response cannot replace newer text entered while autosave ran.
 const sent=structuredClone(w),result=structuredClone(w);ui.set(w);ui.edit(w,key,'Erneut bereit');ui.hydrate(result,sent);assert.equal(ui.get().entries[key].workingText,'Erneut bereit');
 w=await b.save(ui.get());await assert.rejects(()=>b.download(w,frozen,'package.zip'),/changed/);
 const saved=await b.current();assert.equal(saved.entries[key].workingText,'Erneut bereit');
 console.log('Connected JavaScript bridge, renderers, analysis, review simulation and downloads passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
