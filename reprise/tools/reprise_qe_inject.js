;/*repriseQE-v1*/(function(){
try{
var rq=require("node:net"),rp=require("node:path"),rf=require("node:fs"),rho=require("node:os");
var MAXP=1024*1024;
var MIN=3;
var RS=rp.join(rf.realpathSync(rho.homedir()),".claude-desktop","quick-entry.sock");
var conn=null,buf="",backoff=1000;
function send(o){if(!conn)return;try{conn.write(JSON.stringify(o)+String.fromCharCode(10))}catch(e){}}
function dispatch(id,pr){
var text=pr&&typeof pr.prompt==="string"?pr.prompt:"";
if(text.trim().length<MIN){send({jsonrpc:"2.0",id:id,error:{code:-32602,
message:"prompt trop court (garde QTi : > 2 caractères après trim)"}});return}
TYr({prompt:text,images:Array.isArray(pr.images)?pr.images.map(String):[],
filePaths:Array.isArray(pr.filePaths)?pr.filePaths.map(String):[],chatId:pr.chatId||void 0})
.then(function(){send({jsonrpc:"2.0",id:id,result:{dispatch_attempted:true,
request_key:pr&&pr.request_key}})})
.catch(function(e){send({jsonrpc:"2.0",id:id,error:{code:-32603,message:String(e)}})});
}
function line(raw){
var p;try{p=JSON.parse(raw)}catch(e){return}
if(p&&p.method==="relay/dispatch")dispatch(p.id,p.params||{});
}
function connect(){
var c=rq.connect(RS);
c.setEncoding("utf8");
var pings=0;
c.on("connect",function(){
backoff=1000;pings=0;conn=c;
send({hello:true,role:"relay",protocol:"reprise-relay-1",version:1});
try{N.info("[repriseQE] relay connected "+RS)}catch(e){}
});
var timer=setInterval(function(){
if(conn!==c){clearInterval(timer);return}
pings+=1;
if(pings>2){try{c.destroy()}catch(e2){}return}
send({jsonrpc:"2.0",id:Date.now(),method:"ping"});
},30000);
c.on("data",function(d){
buf+=d;
if(buf.length>MAXP){try{c.destroy()}catch(e2){}return}
var i;
while((i=buf.indexOf(String.fromCharCode(10)))>=0){
var ln=buf.slice(0,i);buf=buf.slice(i+1);
if(ln.trim())line(ln);
}
});
c.on("error",function(){});
c.on("close",function(){
clearInterval(timer);
if(conn===c)conn=null;
setTimeout(connect,backoff);
backoff=Math.min(backoff*2,15000);
});
}
try{
var el=require("electron");
if(el&&el.app&&el.app.whenReady){
el.app.whenReady().then(function(){try{connect()}catch(e){N.warn("[repriseQE] "+e)}})
.catch(function(e){N.warn("[repriseQE] whenReady failed "+e)});
}else{connect()}
}catch(e2){connect()}
}catch(e){try{N.warn("[repriseQE] init failed "+e)}catch(x){}}
})();
