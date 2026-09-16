// Real components, SSR only, synthetic reads. No live API/network/hardware.
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root='/app/frontend',ts=require(root+'/node_modules/typescript'),React=require(root+'/node_modules/react');
const {renderToStaticMarkup}=require(root+'/node_modules/react-dom/server');
const did='11111111-1111-1111-1111-111111111111';
const delivery={desired_version:0,reported_version:null,reported_at:null,status:'UNKNOWN'};
const meters=Object.fromEntries(['M1','M2','M3','M4','M5'].map(m=>[m,{meter_id:m,enabled:false,serial:null,model:null,modbus_address:null,phases:1,ct_ratio:1,max_kw:null,register_map:null,comm_status:'DISABLED',last_seen:null}]));
const setup={device_id:did,config_version:0,bus:{},meters,mode:{mode:'AUTO',version:0},delivery,allocation_enabled:false};
const target={wing:'A',current:null,history:[],basis_hash:'a'.repeat(64),as_of_operating_date:'2026-09-16',delivery,bills:{months:[{month:'2026-08',consumption_kwh:4650,days:31},{month:'2026-07',consumption_kwh:4960,days:31}],months_count:2,total_kwh:9610,days:62,daily_average_kwh:155}};
const dash={society_id:1,society:{name:'Prestine Pacific · OFFLINE FIXTURE',location:'Mumbai',plan:'Basic',society_code:'prestine',status:'active'},reset_day:25,devices:[{id:did,name:'Main Controller',connected:true,slots:{},active_slot:null}]};
const cache=new Map();
function load(file){
 if(cache.has(file))return cache.get(file).exports;
 const module={exports:{}};cache.set(file,module);
 const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2021,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText;
 const req=id=>{
  if(id==='react')return React;
  if(id==='react/jsx-runtime')return require(root+'/node_modules/react/jsx-runtime');
  if(id==='next/link')return {__esModule:true,default:props=>React.createElement('a',props)};
  if(id==='@/lib/api')return {__esModule:true,default:new Proxy({},{get(){return ()=>{throw Error('Network forbidden')}}})};
  if(id.startsWith('.')||id.startsWith('@/')){
   const base=id.startsWith('@/')?path.join(root,'src',id.slice(2)):path.resolve(path.dirname(file),id);
   const found=['','.tsx','.ts','.js'].map(e=>base+e).find(p=>fs.existsSync(p)&&fs.statSync(p).isFile());
   if(!found)throw Error('Unknown local import '+id);
   const out=load(found);
   if(found.endsWith('/commissioning/hooks.ts'))return {...out,useRead:url=>({data:url.includes('/dashboard')?dash:url.includes('/targets')?target:setup,loading:false,error:'',refresh(){}}),useSave:()=>({save(){throw Error('Read-only fixture')},busy:false,notice:'',error:''})};
   return out;
  }
  throw Error('Unexpected external import '+id);
 };
 vm.runInNewContext(compiled,{module,exports:module.exports,require:req,console,URLSearchParams,AbortController,Date,Set,Map,Promise,setTimeout,clearTimeout},{filename:file});
 return module.exports;
}
(async()=>{
 const postcss=require(root+'/node_modules/postcss');
 const tailwind=require(require.resolve('@tailwindcss/postcss',{paths:[root]}));
 const source=fs.readFileSync(root+'/src/app/globals.css','utf8');
 const css=(await postcss([tailwind({base:root})]).process(source,{from:root+'/src/app/globals.css'})).css;
 const {EnergySetup}=load(root+'/src/components/commissioning/EnergySetup.tsx');
 const html='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>'+css+'</style></head><body style="background:#0a0e17;color:#d1d5db;margin:0;padding:24px">'+renderToStaticMarkup(React.createElement(EnergySetup,{societyId:'1',initialDeviceId:did}))+'</body></html>';
 fs.writeFileSync('/app/test_reports/commissioning-smoke.html',html);
 console.log('PASS: actual commissioning components SSR rendered with MOCKED reads, local CSS, no requests');
})().catch(e=>{console.error(e);process.exit(1)});