// Current cached font bytes only; no Google requests, no production font change.
const fs=require('node:fs'),path=require('node:path');
const dir='/app/frontend/.next/static/media';
const file=fs.readdirSync(dir).find(f=>f.endsWith('.woff2'));
if(!file)throw Error('A local cached font is required for offline build verification');
module.exports=new Proxy({}, {get:(_,url)=>{
 const family=String(url).includes('Geist+Mono')?'Geist Mono':'Geist';
 return `/* latin */\n@font-face { font-family: '${family}'; font-style: normal; font-weight: 100 900; font-display: swap; src: url(${path.join(dir,file)}) format('woff2'); unicode-range: U+0000-00FF; }`;
}});