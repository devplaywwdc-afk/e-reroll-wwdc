'use strict';
/* Trace msgpack serialization to identify which command builds each request.
   sub_2F92514(container, keyName) = add-key primitive (low RVA, gap-safe).
   sub_2F913C8(dstStr, srcCharPtr) = string value setter.
   Also hook sub_75F2B40 (create_user serializer) directly. */
var LIB='libUE4.so';
var RVA_ADDKEY=0x2F92514, RVA_SETSTR=0x2F913C8, RVA_CREATE=0x75F2B40;

function baseFromMaps(){
    var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
    var ls=t.split('\n'), best=null;
    for(var i=0;i<ls.length;i++){ if(ls[i].indexOf(LIB)===-1) continue;
        var lo=ptr('0x'+ls[i].split('-')[0]); if(best===null||lo.compare(best)<0) best=lo; }
    return best;
}
function cstr(p){ try{ return p.isNull()?null:p.readCString(); }catch(e){ return null; } }

var base=baseFromMaps();
console.log('[trace] base='+base);

// buffer of recent key/value events; flush a window when a command starts
var recent=[];
function push(kind,val){ if(val==null) return; recent.push(kind+':'+val); if(recent.length>400) recent.shift(); }

// add-key: arg1 = key name
Interceptor.attach(base.add(RVA_ADDKEY),{ onEnter:function(a){ var k=cstr(a[1]);
    if(k){ push('K',k); if(k==='device_identifier'||k==='eula_myclub_japan'){ console.log('[trace] >>> create-user key seen: '+k); } } } });

// set-string: arg1 = source char*
Interceptor.attach(base.add(RVA_SETSTR),{ onEnter:function(a){ var v=cstr(a[1]); if(v && v.length<64) push('V',v); } });

// direct create_user serializer
Interceptor.attach(base.add(RVA_CREATE),{ onEnter:function(a){
    console.log('[trace] *** sub_75F2B40 (CMD_CREATE_USER serializer) FIRED ***');
    send({tag:'CREATE_FIRED', a1:a[0].toString()});
} });

// flush recent trace every 2s so we can see command flow
setInterval(function(){ if(recent.length){ send({tag:'TRACE', items:recent.slice()}); recent=[]; } }, 2000);

console.log('[trace] hooks installed (addkey/setstr/create). trigger guest creation.');
send({tag:'LOG', msg:'trace ready base='+base});
