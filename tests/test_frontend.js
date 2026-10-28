/* Run: node tests/test_frontend.js. Execute the actual page with a small DOM
   double; no browser, network or external packages. This does not test webviews. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const platformData = fs.readFileSync(path.join(root, 'platform_data.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));

async function boot(platform, source = html) {
  const elements = new Map(), events = new Map(), requests = [];
  function element(id) {
    return { id, children:[], innerHTML:'', textContent:'', value:'', style:{setProperty(){}, removeProperty(){}},
      classList:{add(){}, remove(){}, toggle(){}}, appendChild(child){this.children.push(child);},
      setAttribute(k,v){this[k]=v;}, getAttribute(k){return this[k] || '';},
      addEventListener(k,fn){events.set(id + ':' + k, fn);}, querySelectorAll(){return [];},
      querySelector(){return null;}, contains(){return false;}, blur(){} };
  }
  const get = id => { if (!elements.has(id)) elements.set(id, element(id)); return elements.get(id); };
  const metas = {'hkhq-platform':platform, 'hkhq-token':'fixture-token', 'hkhq-version':'test', 'hkhq-verified':'fixture'};
  const document = {getElementById:get, createElement:tag=>element(tag),
    querySelector(selector){
      const m = selector.match(/^meta\[name="(.*?)"\]$/);
      return m ? {content:metas[m[1]], getAttribute(){return metas[m[1]];}} : get(selector);
    }, querySelectorAll(){return [get('foot-first'), get('foot-second')];},
    addEventListener(k,fn,capture){events.set('document:' + k + ':' + !!capture, fn);},
    body:get('body'), documentElement:get('html'), activeElement:null};
  const data = {
    '/api/keybindings':{bindings:[{context:'Chat',bindings:{'super+k':'chat:clearScreen'}}]},
    '/api/vscode':{bindings:[{key:'cmd+alt+9',command:'fixture.command'}],removals:[{key:platform === 'macos' ? 'cmd+p' : 'ctrl+p',command:'workbench.action.quickOpen'}]},
    '/api/terminal':{bindings:[{combo:'ctrl+shift+9',action:'new_tab',source:'kitty'}],notes:['fixture terminal note']},
    '/api/obsidian':{shortcuts:[{combo:'shift+win+9',command:'fixture',action:'fixture obsidian'}]},
    '/api/powertoys':{shortcuts:[]}, '/api/assign':{ok:true,path:'fixture/keybindings.json'},
    '/api/unassign':{ok:true,removed:true,path:'fixture/keybindings.json'}
  };
  const context = vm.createContext({document, window:{addEventListener(){},confirm(){return true;}},
    CSS:{escape:v=>v}, localStorage:{getItem(){return null;},setItem(){}}, navigator:{},
    setTimeout(){return 1;}, clearTimeout(){}, console,
    fetch:async (url,options)=>{requests.push({url,options});return {ok:true,status:200,json:async()=>data[url] || {}};}});
  let script = source.match(/<script>([\s\S]*?)<\/script>/)[1];
  script = script.replace('/*__HKHQ_PLATFORM_DATA__*/', platform === 'windows' ? '' : platformData);
  const end = script.lastIndexOf('})();');
  script = script.slice(0,end) + '\nglobalThis.testApp = {normalise,prettyCombo,assign,showMatches,findFree,buildCheatSheet,lookup,DATA,LAYERS,LAYER_ORDER,CUSTOM,NATIVE:typeof NATIVE === "undefined" ? null : NATIVE,PHYS_NAME};\n' + script.slice(end);
  vm.runInContext(script, context);
  for (let i=0;i<12;i++) await Promise.resolve();
  return {app:context.testApp,elements,events,requests,context};
}

(async function(){
  for (const platform of ['windows','macos','linux']) {
    const {app,elements,events,requests} = await boot(platform);
    assert.ok(elements.get('tbl').innerHTML.includes('Command Palette'), platform + ' table renders');
    assert.ok(elements.get('kb').children.length > 0, platform + ' keyboard renders');
    assert.equal(app.lookup(platform === 'macos' ? 'win+p' : 'ctrl+p').some(e=>e.l === 'vscode'), false, 'user removal suppresses default');
    if (platform === 'windows') {
      assert.equal(app.NATIVE, null);
      assert.ok(app.LAYER_ORDER.includes('powertoys'));
      assert.equal(app.prettyCombo('ctrl+shift+win+k'), 'Ctrl+Shift+Win+K');
    } else {
      assert.equal(app.LAYER_ORDER.includes('powertoys'), false);
      assert.equal(requests.some(r=>r.url === '/api/powertoys'), false);
      assert.equal(app.LAYER_ORDER.includes('nvidia'), false);
      assert.equal(app.CUSTOM.some(e=>e.t === 'fixture obsidian' && e.mine), false);
      assert.ok(elements.get('terminalNotes').textContent.includes('fixture terminal note'));
      assert.equal(app.normalise('cmd+shift+k'), 'shift+win+k');
      assert.equal(app.normalise('super+alt+k'), 'alt+win+k');
      assert.equal(app.NATIVE.fromConfig('meta+p', 'claude'), 'alt+p');
      assert.equal(app.NATIVE.fromConfig('meta+p', 'vscode'), 'win+p');
      assert.ok(app.DATA.some(e=>e.l === 'shell' && e.c === 'ctrl+c'));
      assert.equal(app.DATA.some(e=>e.n && /PowerShell|Windows Task Manager|Windows\/WSL/.test(e.n)), false);
      assert.equal(app.NATIVE.forConfig('shift+win+k ctrl+x', 'vscode'), platform === 'macos' ? 'shift+cmd+k ctrl+x' : 'shift+meta+k ctrl+x');
      app.showMatches(platform === 'macos' ? 'win+space' : 'win+l', [{l:'windows',t:'Example',os:true}]);
      assert.ok(elements.get('readout').innerHTML.includes('platform reference'));
      await app.assign('Chat', 'win+k', 'chat:clearScreen', false, 'claude');
      assert.equal(JSON.parse(requests.find(r=>r.url === '/api/assign').options.body).keystroke, 'super+k');
      for (let i=0;i<8;i++) await Promise.resolve();
      const attrs = {'data-act':'remove', 'data-combo':'super+k', 'data-ctx':'Chat', 'data-target':'claude'};
      events.get('readout:click')({target:{closest(){return {getAttribute:k=>attrs[k]};}}});
      for (let i=0;i<8;i++) await Promise.resolve();
      assert.equal(JSON.parse(requests.find(r=>r.url === '/api/unassign').options.body).keystroke, 'super+k');
      assert.equal(app.lookup('win+k').some(e=>e.mine), false);
    }
    if (platform === 'macos') {
      assert.equal(app.prettyCombo('ctrl+alt+shift+win+k'), 'Ctrl+Option+Shift+Cmd+K');
      assert.equal(app.PHYS_NAME.MetaLeft, 'Left Cmd');
      assert.ok(app.DATA.some(e=>e.l === 'vscode' && e.c === 'ctrl+`'));
      assert.equal(app.DATA.some(e=>e.l === 'vscode' && e.c === 'win+`'), false);
    }
    if (platform === 'linux') {
      assert.equal(app.prettyCombo('win+l'), 'Super+L');
      assert.equal(app.LAYER_ORDER.includes('excel'), false);
      assert.ok(app.DATA.some(e=>e.l === 'vscode' && e.c === 'ctrl+shift+i' && e.t === 'Format document'));
    }
    // Tab remains usable with capture on.
    let prevented = false;
    events.get('document:keydown:true')({code:'Tab',preventDefault(){prevented=true;}});
    assert.equal(prevented, false);
    console.log(platform + ': boot, table, modifiers, imports, capture and config serialization passed');
  }
  // Optional local regression check against an explicitly provided original HTML.
  if (process.argv[2]) {
    const before = await boot('windows', fs.readFileSync(process.argv[2], 'utf8'));
    const after = await boot('windows');
    assert.deepEqual(clone(after.app.DATA), clone(before.app.DATA));
    assert.deepEqual(clone(after.app.LAYERS), clone(before.app.LAYERS));
    assert.deepEqual(clone(after.app.LAYER_ORDER), clone(before.app.LAYER_ORDER));
    assert.equal(after.elements.get('tbl').innerHTML, before.elements.get('tbl').innerHTML);
    assert.equal(after.app.buildCheatSheet(), before.app.buildCheatSheet());
    console.log('Windows data, layers, rendered table and cheat sheet match original');
  }
})().catch(e=>{console.error(e);process.exitCode=1;});
