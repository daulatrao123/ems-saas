/* Offline browser bundle of actual React components/hooks. Network/API mocked.
   No dependencies downloaded, no app server, credentials, firmware or installer. */
const fs=require('node:fs'),path=require('node:path');
const root='/app/frontend',ts=require(root+'/node_modules/typescript');
const modules=new Map();
const apiSource=String.raw`
const copy=x=>JSON.parse(JSON.stringify(x));
const did='11111111-1111-1111-1111-111111111111', other='22222222-2222-2222-2222-222222222222';
const ids=[did,other], today='2026-09-16';
const mk=()=>({device_id:did,config_version:0,bus:{},mode:{mode:'AUTO',version:0},allocation_enabled:false,
 delivery:{desired_version:0,reported_version:null,reported_at:null,status:'UNKNOWN'},
 meters:Object.fromEntries(['M1','M2','M3','M4','M5'].map(m=>[m,{meter_id:m,enabled:false,serial:null,model:null,modbus_address:null,phases:1,ct_ratio:1,max_kw:null,register_map:null,comm_status:'DISABLED',last_seen:null}]))});
const state=Object.fromEntries(ids.map(id=>[id,{...mk(),device_id:id}]));
const targets={};
const f=window.__fixture={requests:[],state,targets,delay:15,failNext:null,malformed:false};
const error=(status,detail)=>Object.assign(new Error(detail),{response:{status,data:{detail}}});
const respond=(method,url,body)=>{
 const u=new URL(url,location.origin), id=body?.device_id||u.searchParams.get('device_id')||did;
 const s=state[id];
 if(u.pathname==='/api/admin/dashboard')return {society_id:1,society:{name:'OFFLINE · Prestine Pacific',location:'Mumbai',plan:'Basic',society_code:'fixture',status:'active'},reset_day:25,devices:ids.map((d,i)=>({id:d,name:i?'Other Controller':'Main Controller',connected:true,active_slot:null,slots:Object.fromEntries(['A','B','C','D'].map(w=>[w,{display_name:'Wing '+w,target_days:5,used_days:0,physical_toggle:'UNKNOWN',disabled:w==='D'}]))}))};
 if(!s)throw error(404,'Unknown fixture device');
 if(method==='get'&&u.pathname==='/api/energy/commissioning')return f.malformed?{...s,delivery:null}:s;
 if(method==='get'&&u.pathname==='/api/energy/targets'){
  const w=u.searchParams.get('wing'), available=w!=='D', list=targets[id+':'+w]||[];
  return {wing:w,current:list.find(x=>x.effective_from<=today)||null,history:list,basis_hash:'a'.repeat(64),as_of_operating_date:today,delivery:s.delivery,bills:{months:available?[{month:'2026-08',consumption_kwh:4650,days:31},{month:'2026-07',consumption_kwh:4960,days:31}]:[],months_count:available?2:0,total_kwh:available?9610:null,days:available?62:0,daily_average_kwh:available?155:null}};
 }
 if(!['put','post'].includes(method))throw error(500,'Unexpected read '+u.pathname);
 if(f.failNext){const e=f.failNext;f.failNext=null;throw error(e,'Fixture save rejected');}
 const mode=u.pathname.endsWith('/calculation-mode');
 if(body.expected_version !== (mode?s.mode.version:s.config_version))throw error(409,'Energy configuration changed; refresh before saving');
 if(u.pathname==='/api/energy/bus')s.bus={port:body.port,serial:body.serial};
 else if(u.pathname==='/api/energy/meters/M1')s.meters.M1={...s.meters.M1,...body,meter_id:'M1'};
 else if(mode){s.mode={mode:body.mode,version:s.mode.version+1};return {success:true,mode:body.mode,version:s.mode.version};}
 else if(u.pathname==='/api/energy/targets'){
  if(body.expected_basis_hash!=='a'.repeat(64))throw error(409,'Bill history changed');
  const t={id:Date.now(),target_kwh_per_day:155*(1+body.adjustment_percent/100),adjustment_percent:body.adjustment_percent,effective_from:body.effective_from,reason:body.reason};
  (targets[id+':'+body.wing]??=[]).unshift(t);
 }else throw error(500,'Forbidden unexpected write '+u.pathname);
 s.config_version++;s.delivery={...s.delivery,desired_version:s.config_version,status:'PENDING'};
 return {success:true,config_version:s.config_version};
};
const send=(method,url,body,signal)=>new Promise((resolve,reject)=>{
 f.requests.push({method,url,body:copy(body||null)});
 const delay=(url.includes('wing=A')&&f.delayA)||f.delay;
 setTimeout(()=>{if(signal?.aborted){reject(error(499,'cancelled'));return;}try{resolve({data:copy(respond(method,url,body))});}catch(e){reject(e);}},delay);
});
module.exports={__esModule:true,default:{get:(url,cfg)=>send('get',url,null,cfg?.signal),request:cfg=>send(cfg.method,cfg.url,cfg.data,cfg.signal)}};
`;
const virtual={
 '@api':apiSource,
 '@link':`const React=require('react');module.exports={__esModule:true,default:props=>React.createElement('a',props)};`,
 '@entry':`const React=require('react'),{createRoot}=require('react-dom/client');const {EnergySetup}=require('@/components/commissioning/EnergySetup');createRoot(document.getElementById('root')).render(React.createElement(EnergySetup,{societyId:'1',initialDeviceId:'11111111-1111-1111-1111-111111111111'}));`
};
function resolve(id,from){
 if(id==='@/lib/api')return '@api'; if(id==='next/link')return '@link';
 if(id.startsWith('@/')||id.startsWith('.')){
  const base=id.startsWith('@/')?path.join(root,'src',id.slice(2)):path.resolve(path.dirname(from),id);
  const p=['','.tsx','.ts','.js','.json'].map(e=>base+e).find(p=>fs.existsSync(p)&&fs.statSync(p).isFile());
  if(p)return p;
 }
 return require.resolve(id,{paths:[from.startsWith('@')?root:path.dirname(from),root]});
}
function add(file){
 if(modules.has(file))return;
 let source=virtual[file]??fs.readFileSync(file,'utf8');
 if(file.endsWith('.json'))source='module.exports='+source+';';
 if(/\.tsx?$/.test(file))source=ts.transpileModule(source,{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2021,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 const item={source,deps:{}};modules.set(file,item);
 for(const match of source.matchAll(/require\(["']([^"']+)["']\)/g)){const key=resolve(match[1],file);item.deps[match[1]]=key;add(key);}
}
(async()=>{
 add('@entry');
 const factories=[...modules].map(([id,x])=>JSON.stringify(id)+':[function(module,exports,require){\n'+x.source+'\n},'+JSON.stringify(x.deps)+']').join(',\n');
 const bundle='const process={env:{NODE_ENV:"production"}};const mods={'+factories+'},cache={};function load(id){if(cache[id])return cache[id].exports;const m=cache[id]={exports:{}};const [fn,deps]=mods[id];fn(m,m.exports,x=>load(deps[x]));return m.exports;}load("@entry");';
 const postcss=require(require.resolve('postcss',{paths:[root]}));
 const tailwind=require(require.resolve('@tailwindcss/postcss',{paths:[root]}));
 const css=(await postcss([tailwind({base:root})]).process(fs.readFileSync(root+'/src/app/globals.css','utf8'),{from:root+'/src/app/globals.css'})).css;
 const html='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+css+'</style></head><body style="margin:0;padding:20px;background:#0a0e17;color:white"><div id="root"></div><script>'+bundle.replace(/<\/script/gi,'<\\/script')+'</script></body></html>';
 fs.writeFileSync('/app/test_reports/commissioning-browser.html',html);
 console.log('PASS: isolated React browser fixture, '+modules.size+' locally bundled modules; no external API or dependency download');
})().catch(e=>{console.error(e);process.exit(1)});