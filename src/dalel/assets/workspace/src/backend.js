/* The local Python service is authoritative for persistence, review and exports. */
const Backend=(()=>{
 let token=null,info=null,tail=Promise.resolve(),conflict=null;
 const versions=new Map();
 async function request(path,body){
  let response;
  try{response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'X-Dalel-Token':token||'',...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)}),cache:'no-store'});}
  catch{throw Error('The local dalel backend is unavailable. Keep your backup and restart dalel using dalel serve.');}
  const data=await response.json().catch(()=>({error:'The local service returned an invalid response. Restart it with the current dalel build.'}));
  if(!response.ok){const error=Error(data.error||'The backend could not complete this operation.');error.status=response.status;if(response.status===409)conflict=error;throw error;}
  if(data.workspace)versions.set(data.workspace.id,data.workspace.backend.version);
  return data;
 }
 function serial(fn){const task=tail.catch(()=>{}).then(()=>{if(conflict)throw conflict;return fn();});tail=task;return task;}
 async function init(){info=await request('bootstrap');token=info.token;return info;}
 async function current(){if(!token)await init();return (await request('workspaces/current')).workspace;}
 const op=(workspace,name,payload={})=>serial(()=>request('workspaces/'+encodeURIComponent(workspace.id)+'/'+name,{...payload,expectedVersion:versions.get(workspace.id)}));
 return {init,current,get info(){return info;},get conflict(){return conflict;},
  save:async workspace=>{const draft={name:workspace.name,sourceLocale:workspace.sourceLocale,targetLocale:workspace.targetLocale,snapshots:workspace.snapshots,session:workspace.session,ui:workspace.ui,glossary:workspace.glossary,entries:Object.fromEntries(workspace.order.map(key=>{const e=workspace.entries[key];return [key,{workingText:e.workingText,currentRevisionId:e.currentRevisionId,undoStack:e.undoStack,redoStack:e.redoStack}];}))};return (await op(workspace,'save',{workspace:draft})).workspace;},
  create:async workspace=>(await serial(()=>request('workspaces',{workspace}))).workspace,
  operation:async(workspace,name,payload)=>(await op(workspace,name,payload)).workspace,
  preview:async(workspace,mode)=>(await op(workspace,'preview',{mode})).release,
  reviewExport:async workspace=>request('workspaces/'+encodeURIComponent(workspace.id)+'/review'),
  list:async()=>(await request('workspaces')).workspaces,
  activate:async id=>(await serial(()=>request('workspaces/'+encodeURIComponent(id)+'/activate',{}))).workspace,
  clear:()=>serial(()=>request('workspaces/clear',{})),
  async download(workspace,release,name){
   if(conflict)throw conflict;
   const response=await fetch('/api/workspaces/'+encodeURIComponent(workspace.id)+'/releases/'+encodeURIComponent(release.id)+'/'+encodeURIComponent(name),{headers:{'X-Dalel-Token':token},cache:'no-store'});
   if(!response.ok){const error=await response.json();throw Error(error.error||'Unable to download this snapshot.');}
   return response.blob();
  }
 };
})();
