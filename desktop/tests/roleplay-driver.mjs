// Local browser-control helper. Only DOM actions; never calls the product API.
import { chromium } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';
import { createInterface } from 'node:readline';
const lab=JSON.parse(readFileSync(process.argv[2],'utf8'));
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true});
const pages={}, memory={}, errors=[];
mkdirSync(`${lab.root}/screenshots`,{recursive:true});
for(const name of ['alice','bob']) {
 const context=await browser.newContext({viewport:{width:1440,height:980}});
 const page=await context.newPage(); pages[name]=page;
 page.on('dialog',async d=>{const planned=memory.dialog; delete memory.dialog; console.log(JSON.stringify({dialog:d.message(),user:name})); if(planned?.user===name)await d.accept(planned.value);else await d.dismiss();});
 page.on('pageerror',e=>errors.push({user:name,message:e.message}));
 await page.goto(lab.people[name].url);
}
function locate(page,v){
 let l=v.css?page.locator(v.css):v.label?page.getByLabel(v.label,{exact:true}):v.text?page.getByText(v.text,{exact:true}):page.getByRole(v.role||'button',{name:v.name,exact:true});
 return v.nth!==undefined?l.nth(v.nth):l;
}
console.log(JSON.stringify({ready:true,alice:await pages.alice.locator('body').ariaSnapshot()}));
try {
 for await(const line of createInterface({input:process.stdin})) {
  try {
   const v=JSON.parse(line),p=pages[v.user||'alice'];
   if(v.op==='quit')break;
   if(v.op==='dialog')memory.dialog={user:v.user||'alice',value:v.value||lab.people[v.user||'alice'].repo};
   if(v.op==='picker')await p.evaluate(path=>{window.__TAURI__={dialog:{open:async()=>path}}},v.value||lab.people[v.user||'alice'].repo);
   if(v.op==='stats')console.log(JSON.stringify(await p.evaluate(()=>({overflow:document.documentElement.scrollWidth-innerWidth,tickets:document.querySelectorAll('.ticket-row').length,focus:document.activeElement?.outerHTML.slice(0,300)}))));
   if(v.op==='click')await locate(p,v).click({timeout:4000});
   if(v.op==='fill')await locate(p,v).fill(v.secret==='setup'?lab.setup_token:v.secret==='invite'?memory.invite:v.value);
   if(v.op==='select')await locate(p,v).selectOption(v.value);
   if(v.op==='press')await locate(p,v).press(v.key);
   if(v.op==='goto')await p.goto(v.url||lab.people[v.user||'alice'].url+ (v.path||''));
   if(v.op==='resize')await p.setViewportSize({width:v.width,height:v.height||980});
   if(v.op==='invite')memory.invite=await p.locator('.invitation-link code').innerText();
   if(v.op==='shot')await p.screenshot({path:`${lab.root}/screenshots/${v.file}.png`,fullPage:true,mask:[p.locator('.invitation-link'),p.locator('input[type=password]')]});
   if(v.op==='read')console.log(JSON.stringify({value:await locate(p,v).inputValue()}));
   if(v.op==='wait')await locate(p,v).waitFor({state:'visible',timeout:v.timeout||10000});
   const dom=(await p.locator(v.scope||'body').ariaSnapshot()).replace(/ticketbunker:\/\/join\?[^\s]+/g,'[invitation redacted]');
   console.log(JSON.stringify({ok:true,dom,errors}));
  }catch(e){console.log(JSON.stringify({error:e.message}));}
 }
}finally{await browser.close();}
