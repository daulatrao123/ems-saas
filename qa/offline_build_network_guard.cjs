// Used only by isolated build verification. Never loaded by the application.
const forbidden=()=>{throw new Error('Network is forbidden in offline build verification');};
for(const name of ['node:http','node:https']){
 const mod=require(name); mod.get=forbidden;mod.request=forbidden;
}
globalThis.fetch=async()=>forbidden();
const net=require('node:net');
for(const key of ['connect','createConnection']){
 const original=net[key];net[key]=function(...args){
  const first=args[0];
  if(typeof first==='number'||(first&&typeof first==='object'&&('port'in first||'host'in first)))return forbidden();
  return original.apply(this,args);
 };
}