;/*repriseQE-v1*/(function(){
try{
var rq=require("node:net"),rp=require("node:path"),rf=require("node:fs"),rho=require("node:os");
var MAXP=8*1024*1024;
var RS=rp.join(rf.realpathSync(rho.homedir()),".claude-desktop","quick-entry-reprise.sock");
var started=false;
function send(s,o){try{s.write(JSON.stringify(o)+String.fromCharCode(10))}catch(e){}}
function startQE(){
if(started)return;started=true;
try{rf.unlinkSync(RS)}catch(e){}
var RSV=rq.createServer(function(s){
var b="";
s.setEncoding("utf8");
s.on("data",function(d){
b+=d;
if(b.length>MAXP){N.warn("[repriseQE] payload too large");s.destroy();return}
var i;
while((i=b.indexOf(String.fromCharCode(10)))>=0){
var line=b.slice(0,i);b=b.slice(i+1);
if(!line.trim())continue;
try{
var p=JSON.parse(line);
if(p&&p.hello){send(s,{hello:true});continue}
if(typeof p.prompt!=="string"||!p.prompt.trim())throw new Error("prompt required");
TYr({prompt:p.prompt,images:Array.isArray(p.images)?p.images.map(String):[],
filePaths:Array.isArray(p.filePaths)?p.filePaths.map(String):[],chatId:p.chatId||void 0})
.then(function(){send(s,{ok:true})})
.catch(function(e){send(s,{ok:false,error:String(e)})});
}catch(e){send(s,{ok:false,error:String(e)})}
}
});
s.on("error",function(e){N.warn("[repriseQE] conn error "+e)});
});
RSV.on("error",function(e){N.warn("[repriseQE] socket error "+e)});
RSV.listen(RS,function(){N.info("[repriseQE] listening "+RS)});
}
try{
var el=require("electron");
if(el&&el.app&&el.app.whenReady){
el.app.whenReady().then(function(){try{startQE()}catch(e){N.warn("[repriseQE] "+e)}})
.catch(function(e){N.warn("[repriseQE] whenReady failed "+e)});
}else{startQE()}
}catch(e2){startQE()}
}catch(e){try{N.warn("[repriseQE] init failed "+e)}catch(x){}}
})();
