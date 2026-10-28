/* Non-Windows reference data and presentation. Injected locally by server.py.
   These are documented defaults, not a scan of OS settings or installed apps.
   Keep Ctrl and Command distinct: terminal Ctrl bindings stay Ctrl on a Mac. */
var HKHQPlatform = (function () {
  'use strict';
  var mac = document.querySelector('meta[name="hkhq-platform"]').content === 'macos';
  var order = ['ctrl', 'alt', 'shift', 'win'];
  var aliases = { control:'ctrl', option:'alt', opt:'alt', command:'win', cmd:'win',
                  super:'win', meta:'win', esc:'escape', return:'enter',
                  pgup:'pageup', pgdn:'pagedown' };
  function normalise(step) {
    var p = step.toLowerCase().split('+').map(function(s){ return aliases[s] || s; });
    var base = p.pop();
    return order.filter(function(m){ return p.indexOf(m) >= 0; }).concat(base).join('+');
  }
  function rows(layer, entries, note, os) {
    return entries.map(function(e){ return { c:e[0], l:layer, t:e[1], n:note || '', os:!!os }; });
  }
  var osRows = mac ? [
    ['win+space','Spotlight'], ['win+tab','Switch applications'],
    ['win+`','Switch windows of the front app'], ['alt+win+escape','Force Quit'],
    ['ctrl+win+q','Lock screen'], ['ctrl+up','Mission Control'],
    ['ctrl+down','App Expose'], ['ctrl+left','Previous desktop'], ['ctrl+right','Next desktop'],
    ['shift+win+3','Screenshot screen'], ['shift+win+4','Screenshot selection'],
    ['shift+win+5','Screenshot controls']
  ] : [
    ['alt+f1','Activities overview'],
    ['alt+f2','Run command'], ['win+tab','Switch applications'],
    ['alt+escape','Switch windows directly'], ['win+l','Lock screen'],
    ['win+d','Hide all normal windows'], ['win+a','Show applications'],
    ['win+left','Tile window left'], ['win+right','Tile window right']
  ];
  var vscodeMac = [
    ['shift+win+p','Command Palette'], ['win+p','Quick Open — go to file'],
    ['shift+win+e','Explorer'], ['shift+win+f','Search across files'],
    ['ctrl+shift+g','Source control'], ['shift+win+d','Run and debug'],
    ['shift+win+x','Extensions'], ['shift+win+o','Go to symbol in file'],
    ['win+b','Toggle side bar'], ['win+j','Toggle bottom panel'],
    ['ctrl+`','Toggle integrated terminal'], ['win+,','Settings'],
    ['win+/','Toggle line comment'], ['win+d','Add next match to selection'],
    ['shift+win+l','Select all occurrences'], ['shift+win+k','Delete line'],
    ['ctrl+g','Go to line'], ['win+t','Go to symbol in workspace'],
    ['win+w','Close editor'], ['win+\\','Split editor'],
    ['alt+shift+f','Format document'], ['win+k win+s','Keyboard Shortcuts editor'],
    ['ctrl+space','Trigger suggestions'], ['win+k win+c','Add line comment'],
    ['win+k win+u','Remove line comment'], ['win+s','Save'], ['alt+win+s','Save all']
  ];
  var vscodeLinux = [
    ['ctrl+shift+p','Command Palette'], ['ctrl+p','Quick Open — go to file'],
    ['ctrl+shift+e','Explorer'], ['ctrl+shift+f','Search across files'],
    ['ctrl+shift+g','Source control'], ['ctrl+shift+d','Run and debug'],
    ['ctrl+shift+x','Extensions'], ['ctrl+shift+o','Go to symbol in file'],
    ['ctrl+b','Toggle side bar'], ['ctrl+j','Toggle bottom panel'],
    ['ctrl+`','Toggle integrated terminal'], ['ctrl+,','Settings'],
    ['ctrl+/','Toggle line comment'], ['ctrl+d','Add next match to selection'],
    ['ctrl+shift+l','Select all occurrences'], ['ctrl+shift+k','Delete line'],
    ['ctrl+g','Go to line'], ['ctrl+t','Go to symbol in workspace'],
    ['ctrl+w','Close editor'], ['ctrl+\\','Split editor'],
    ['ctrl+shift+i','Format document'], ['ctrl+k ctrl+s','Keyboard Shortcuts editor'],
    ['ctrl+space','Trigger suggestions'], ['ctrl+k ctrl+c','Add line comment'],
    ['ctrl+k ctrl+u','Remove line comment'], ['ctrl+s','Save']
  ];
  var freeNote = 'Unclaimed in the loaded reference and saved overrides. Other apps, desktop customisations, terminal includes and native window shortcuts may still use it. Click a combo to bind it.';
  var fnNote = 'Fn/Globe handling depends on the keyboard and OS. This page cannot capture or rebind it. On macOS, check Keyboard settings for the function-key behaviour.';
  return {
    mac:mac, hidden:mac ? ['powertoys','nvidia'] : ['powertoys','nvidia','excel','notion'],
    osName:mac ? 'macOS defaults' : 'Linux (GNOME defaults)',
    freeNote:freeNote, fnNote:fnNote,
    fnExplainer:'<p class="ro-combo">Fn / Globe</p><p class="ro-sub">' + fnNote + '</p><div class="ro-actions"><button class="btn" data-act="idle">Close</button></div>',
    normalise:normalise,
    fromConfig:function(combo, target){
      return combo.split(' ').map(function(step){
        if (target === 'claude') step = step.replace(/(^|\+)meta(?=\+)/gi, '$1alt');
        return normalise(step);
      }).join(' ');
    },
    modLabel:function(m){ return m === 'win' ? (mac ? 'Cmd' : 'Super') : m === 'alt' && mac ? 'Option' : {ctrl:'Ctrl',alt:'Alt',shift:'Shift'}[m] || m; },
    forConfig:function(combo, target){
      return combo.split(' ').map(function(step){
        return step.split('+').map(function(p){ return p === 'win' ? (target === 'vscode' ? (mac ? 'cmd' : 'meta') : 'super') : p; }).join('+');
      }).join(' ');
    },
    defaults:function(original){
      var data = original.filter(function(e){
        return e.l === 'claude' && e.n.indexOf('VS Code') < 0 && e.n.indexOf('Windows') < 0;
      }).map(function(e){ return Object.assign({}, e, {os:false}); });
      if (mac) data = data.concat(rows('claude', [['win+k','Clear screen (terminal support required)']], 'chat:clearScreen'));
      data = data.concat(rows('windows', osRows, mac ? 'macOS default; user settings may differ' : 'GNOME reference only; KDE, other desktops and custom settings differ', true));
      data = data.concat(rows('vscode', mac ? vscodeMac : vscodeLinux, 'Platform default; extensions, context and user settings may override it'));
      data = data.concat(original.filter(function(e){ return e.l === 'vscode' && /^f[0-9]+$/.test(e.c); }));
      ['text','obsidian','chrome','notion'].forEach(function(layer){
        // A conservative shared set whose macOS binding uses Command.
        var common = ['ctrl+c','ctrl+x','ctrl+v','ctrl+z','ctrl+shift+z','ctrl+a','ctrl+s',
          'ctrl+f','ctrl+n','ctrl+o','ctrl+p','ctrl+t','ctrl+w','ctrl+r','ctrl+l','ctrl+d',
          'ctrl+b','ctrl+i','ctrl+e','ctrl+g','ctrl+k','ctrl+,','ctrl+enter',
          'ctrl+shift+f','ctrl+shift+t','ctrl+shift+n','ctrl+[','ctrl+]'];
        if (!mac && layer === 'notion') return;
        original.filter(function(e){ return e.l === layer && (!mac || common.indexOf(e.c) >= 0); }).forEach(function(e){
          data.push(Object.assign({}, e, {c:mac ? e.c.replace(/ctrl/g, 'win') : e.c,
            n:'Common application default; user settings may differ', os:false}));
        });
      });
      if (mac) data = data.concat(rows('excel', [['win+1','Format cells'], ['win+t','Create a table'], ['shift+win+l','Toggle filters']], 'Excel for Mac reference'));
      return data.concat(rows('shell', [
        ['ctrl+c','Interrupt command'], ['ctrl+d','End input / exit shell'], ['ctrl+l','Clear screen'],
        ['ctrl+r','Reverse-search history'], ['ctrl+a','Start of line'], ['ctrl+e','End of line'],
        ['ctrl+u','Delete to start of line'], ['ctrl+k','Delete to end of line'], ['ctrl+w','Delete previous word'],
        ['tab','Complete token'], ['up','Previous command'], ['down','Next command']
      ], 'Typical readline / Emacs editing mode; shells and vi mode differ'));
    },
    prepare:function(layouts){
      Object.keys(layouts).forEach(function(id){
        layouts[id].rows.forEach(function(row){ row.forEach(function(k){
          if (k[0] === '@win') k[1] = mac ? 'cmd' : 'super';
          if (mac && k[0] === '@alt') k[1] = 'option';
        }); });
      });
    },
    physicalNames:function(names){
      names.MetaLeft = mac ? 'Left Cmd' : 'Left Super'; names.MetaRight = mac ? 'Right Cmd' : 'Right Super';
      if (mac) { names.AltLeft = 'Left Option'; names.AltRight = 'Right Option'; }
    },
    terminalNotes:function(notes){
      var p = document.getElementById('terminalNotes');
      if (p) p.textContent = notes.join(' ');
    },
    describe:function(){
      document.querySelector('.mast p').textContent = 'Claude Code, VS Code, ' + (mac ? 'macOS' : 'Linux') + ' reference shortcuts and your saved bindings on one keyboard.';
      document.getElementById('platformNotes').innerHTML = '<h3>' + (mac ? 'macOS' : 'Linux') + ' beta</h3><ul>' +
        '<li>Built and checked on Windows with automated tests and syntax/type checks; not yet tested on real Mac/Linux hardware.</li>' +
        '<li>Capture works only inside this focused page. The OS, compositor, window manager and native webview may consume shortcuts first. No global hook or special input permission is used.</li>' +
        '<li>' + (mac ? 'Cmd is Command; Option is Alt. Terminal Ctrl shortcuts stay Ctrl. Option-as-Meta depends on terminal settings.' : 'Super is the Windows-logo key. OS rows are GNOME defaults only, not a live desktop-settings scan. KDE and other desktops differ on both X11 and Wayland.') + '</li>' +
        '<li>Default tables are a selected reference, not an exhaustive inventory of installed apps or reserved keys. VS Code profiles, Insiders, portable installs and sandboxed app config paths are not imported.</li>' +
        '<li id="terminalNotes">Terminal custom bindings: kitty' + (mac ? ' and iTerm2' : '') + ' saved config only. Terminal.app, GNOME Terminal and Konsole custom bindings are not imported.</li></ul>';
      document.querySelector('.kb-note').textContent = 'Hold Ctrl, ' + (mac ? 'Option, Shift or Cmd' : 'Alt, Shift or Super') + ' to preview combinations; click any key for details. ' + fnNote + ' The board is a generic ANSI layout.';
      document.querySelector('.foot p').textContent = 'Reads saved Claude Code, VS Code, kitty' + (mac ? ', iTerm2' : '') + ' and Obsidian configs. Assign/Remove writes only Claude Code or VS Code; Obsidian export and backup/restore are explicit actions. Launch also snapshots supported configs locally.';
      document.querySelectorAll('.foot p')[1].innerHTML = 'Built-in rows are a selected platform reference. Claude CLI rows retain the original snapshot: <span id="verifiedNote"></span>. <span id="versionNote"></span>';
      document.getElementById('readout').innerHTML = '<p class="ro-hint">Click here and press a shortcut. Capture can prevent only keys delivered to this page. ' + (mac ? 'Try Cmd+Shift+P or Ctrl+R.' : 'Try Ctrl+Shift+P or Ctrl+R.') + '</p>';
    }
  };
})();
