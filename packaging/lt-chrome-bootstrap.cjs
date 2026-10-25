// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
// Generation input only. All replacement strings come from packaging/plugin.toml.
(function(a){
var fs=require('fs'),P=require('path'),os=require('os'),cp=require('child_process');
var R=[],via='unknown',L=null;
a.forEach(function(s){var i=s.indexOf('='),k=s.slice(0,i),v=s.slice(i+1);
if(k=='root'){if(v.indexOf('$')==-1){if(P.isAbsolute(v)){R.push(v)}}}
else if(k=='via'){if(v.replace(/[a-z0-9._-]/g,'')==''){if(v.length){if(v.slice(0,40)==v){via=v}}}}});
R.forEach(function(r){if(L==null){var f=P.join(r,'__LAUNCHER_REL__');if(fs.existsSync(f)){L=f}}});
if(L){require(L).main(a);return}
var e=process.env;['LT_SUITE_CONFIG','XDG_CONFIG_HOME','XDG_CACHE_HOME','APPDATA','LOCALAPPDATA'].forEach(function(q){var v=e[q];if(v){if(v.indexOf('$')==-1){return}}delete e[q]});
var p=process.platform,W=p=='win32',x=e.XDG_CACHE_HOME,h=e.HOME?e.HOME:os.homedir(),c;
if(W){h=e.USERPROFILE?e.USERPROFILE:h;c=P.join(e.LOCALAPPDATA?e.LOCALAPPDATA:P.join(h,'AppData','Local'),'lt-suite','cache')}
else if(p=='darwin'){c=P.join(h,'Library','Caches','lt-suite')}
else{c=P.join(x?(P.isAbsolute(x)?x:P.join(h,'.cache')):P.join(h,'.cache'),'lt-suite')}
var d=P.join(c,'lt-chrome','run'),w=process.cwd(),r=R.length?'no lt-chrome launcher under the plugin folder':'this agent did not pass the plugin folder',why=r;
try{fs.mkdirSync(d,{recursive:true,mode:448});fs.writeFileSync(P.join(d,'package.json'),'{}');
// npm adds ancestor .bin folders on POSIX. These nearer shims pin both interpreters.
if(W==false){var b=P.join(d,'node_modules','.bin');fs.mkdirSync(b,{recursive:true,mode:448});var q=String.fromCharCode(39),Q=String.fromCharCode(34);[['node',process.execPath],['sh','/bin/sh']].forEach(function(t){var s=q+t[1].split(q).join(q+'\\'+q+q)+q;fs.writeFileSync(P.join(b,t[0]),String.fromCharCode(35,33)+'/bin/sh\nexec '+s+' '+Q+'$@'+Q+'\n',{mode:448})})}
w=d}catch(z){why=r+'; '+d+' is not writable, so npx runs in the agent folder'}
process.stderr.write('lt-chrome: '+why+'; it runs as in lt-suite 0.1.0 (auto-connect, browser settings ignored). Run setup.py lt-chrome snippet to use your settings.\n');
var n=P.dirname(process.execPath),j=[P.join(n,'node_modules','npm','bin','npx-cli.js'),P.join(n,'..','lib','node_modules','npm','bin','npx-cli.js')].filter(function(f){return fs.existsSync(f)})[0];
var A=__ARGV__;
var v={};Object.keys(e).forEach(function(q){var u=W?q.toUpperCase():q;if(u.indexOf('CHROME_DEVTOOLS_MCP_')==0){return}if(u=='PLUGIN_DATA'){return}v[q]=e[q]});
v.CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY='1';v.CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS='1';
var o={stdio:'inherit',cwd:w,env:v,shell:false},k;
if(j){k=cp.spawn(process.execPath,[j].concat(A),o)}else if(W){o.shell=true;k=cp.spawn('npx '+A.join(' '),o)}else{k=cp.spawn('npx',A,o)}
k.on('spawn',function(){try{fs.writeFileSync(P.join(c,'lt-chrome','last-fallback-'+via+'.json'),JSON.stringify({time:new Date().toISOString(),lt_suite_version:'__VERSION__',via:via,action:'fallback',reason:r}))}catch(z){}});
['SIGINT','SIGTERM','SIGHUP'].forEach(function(s){process.on(s,function(){k.kill(s)})});
k.on('error',function(){process.exit(1)});
k.on('exit',function(c2){process.exit(c2==null?1:c2)})
})(process.argv.slice(1))
