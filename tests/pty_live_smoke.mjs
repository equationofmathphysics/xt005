import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
let chromium;
try { ({chromium}=require('playwright')); } catch { process.exit(77); }
let browser;
try { browser=await chromium.launch({headless:true,args:['--no-sandbox']}); }
catch(error) { console.log(String(error)); process.exit(77); }
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[]; page.on('pageerror',error=>errors.push(String(error)));
const url=process.argv[2];
async function ready(){ await page.waitForFunction(()=>typeof termConnected!=='undefined'&&termConnected); }
async function screenContains(text){await page.waitForFunction(text=>{const b=term.buffer.active;return Array.from({length:b.length},(_,i)=>b.getLine(i)?.translateToString()||'').join('\n').includes(text)},text);}
try {
 await page.goto(url); await ready();
 await page.evaluate(()=>term.paste("printf 'PTY_LIVE_中文_OK\\n'\n"));
 await screenContains('PTY_LIVE_中文_OK');
 await page.getByRole('button',{name:'草稿',exact:true}).click();
 await page.getByRole('textbox',{name:'终端草稿'}).fill('保留草稿\nsecond line');
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.reload(); await ready(); await screenContains('PTY_LIVE_中文_OK');
 await page.getByRole('button',{name:'草稿',exact:true}).click();
 if(await page.getByRole('textbox',{name:'终端草稿'}).inputValue()!=='保留草稿\nsecond line') throw Error('draft lost');
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.evaluate(()=>previewConversation('12345678-1234-1234-1234-123456789abc'));
 await page.getByText('离线历史内容',{exact:true}).waitFor();
 await page.getByRole('button',{name:'关闭',exact:true}).click();
 await page.evaluate(()=>toggleWorkspacePin('codexws'));
 const pins=await (await page.request.get(url+'/api/workspaces')).json();
 if(!pins.workspaces.find(x=>x.id==='codexws').pinned) throw Error('pin not persisted');
 await page.getByRole('button',{name:'+ Codex',exact:true}).click();
 await page.getByText('未安装 Codex CLI；通用终端仍可使用',{exact:true}).waitFor();
 await page.evaluate(()=>term.paste("printf 'AFTER_MISSING_AGENT_OK\\n'\n"));
 await screenContains('AFTER_MISSING_AGENT_OK');
 if(errors.length) throw Error(errors.join('\n'));
 if(process.env.PTY_SMOKE_SCREENSHOT) await page.screenshot({path:process.env.PTY_SMOKE_SCREENSHOT});
 console.log('Live PTY UI: Unicode, reconnect, draft, offline history, pin, missing optional CLI passed');
} finally {await browser.close();}
