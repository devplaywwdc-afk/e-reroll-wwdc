'use strict';
/* Resolve SSL_write/SSL_read via /proc/self/maps FILE-OFFSET math (handles the
 * segment gaps that break naive base+RVA), then capture CMD_* request/response
 * wire bytes. IDA vaddr==file offset for these LOAD segments. */
var LIB='libUE4.so';
var OFF_W=0x7FFA9B4, OFF_R=0x7FFA780;
var PRO='ffc300d1fd7b01a9f31300f9fd43009153d03bd5681640f9e80700f9';
var MAX=1048576;

function segs(){
    var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
    var out=[]; t.split('\n').forEach(function(l){ if(l.indexOf(LIB)===-1) return;
        var p=l.split(/\s+/); var a=p[0].split('-');
        out.push({start:ptr('0x'+a[0]), end:ptr('0x'+a[1]), perm:p[1]}); });
    return out;
}
// 80-byte signatures; SSL_write/SSL_read differ only at offset 40 (8c vs 33)
var SIG_W='ff c3 00 d1 fd 7b 01 a9 f3 13 00 f9 fd 43 00 91 53 d0 3b d5 68 16 40 f9 e8 07 00 f9 e2 01 f8 37 e2 03 02 2a e3 03 00 91 8c ff ff 97 e8 03 40 b9 1f 00 00 71 00 c1 80 1a 68 16 40 f9 e9 07 40 f9 1f 01 09 eb 21 02 00 54 fd 7b 41 a9 f3 13 40 f9 ff c3 00 91';
var SIG_R='ff c3 00 d1 fd 7b 01 a9 f3 13 00 f9 fd 43 00 91 53 d0 3b d5 68 16 40 f9 e8 07 00 f9 e2 01 f8 37 e2 03 02 2a e3 03 00 91 33 ff ff 97 e8 03 40 b9 1f 00 00 71 00 c1 80 1a 68 16 40 f9 e9 07 40 f9 1f 01 09 eb 21 02 00 54 fd 7b 41 a9 f3 13 40 f9 ff c3 00 91';

function scanSig(name, sig){
    var hits=[]; segs().forEach(function(s){
        if(s.perm.indexOf('r')===-1) return;
        var size=s.end.sub(s.start).toInt32(); if(size<=0) return;
        try{ Memory.scanSync(s.start,size,sig).forEach(function(h){
            var k=h.address.toString(); if(hits.indexOf(k)<0) hits.push(k); }); }catch(e){}
    });
    return hits.length? ptr(hits[0]) : null;
}
function findFunc(vaddr,name){ return scanSig(name, name==='SSL_write'?SIG_W:SIG_R); }

function txt(bytes){ try{ var u=new Uint8Array(bytes); var s=''; for(var i=0;i<u.length;i++){ var c=u[i]; s+=(c>=32&&c<127)?String.fromCharCode(c):'.'; } return s; }catch(e){ return ''; } }

function hookW(W){
    Interceptor.attach(W,{ onEnter:function(a){ try{
        var n=a[2].toInt32(); if(n<=0||n>MAX) return;
        var buf=a[1].readByteArray(n); var s=txt(buf);
        if(s.indexOf('CMD_')>=0){
            var cmd=(s.match(/CMD_[A-Z_]+/)||['?'])[0];
            console.log('[SSL_W] '+cmd+' ('+n+'B)');
            send({tag:'WIRE', dir:'w', cmd:cmd, n:n}, buf);
        }
    }catch(e){} } });
}
function hookR(R){
    Interceptor.attach(R,{ onEnter:function(a){ this.b=a[1]; }, onLeave:function(r){ try{
        var n=r.toInt32(); if(n<=0||n>MAX) return;
        var buf=this.b.readByteArray(n); var s=txt(buf);
        if(s.indexOf('CMD_')>=0 || s.indexOf('auth_code')>=0){
            var cmd=(s.match(/CMD_[A-Z_]+/)||['resp'])[0];
            send({tag:'WIRE', dir:'r', cmd:cmd, n:n}, buf);
        }
    }catch(e){} } });
}

// continuous rescan + retry-attach: handles partial mapping at boot-freeze AND
// houdini translation delay. keep going until both hooked.
var W=null, R=null, doneW=false, doneR=false, tries=0;
var iv=setInterval(function(){
    tries++;
    if(!W) W=findFunc(OFF_W,'SSL_write');
    if(!R) R=findFunc(OFF_R,'SSL_read');
    if(W && !doneW){ try{ hookW(W); doneW=true; send({tag:'LOG',msg:'SSL_write hooked @'+W+' try '+tries}); }catch(e){} }
    if(R && !doneR){ try{ hookR(R); doneR=true; send({tag:'LOG',msg:'SSL_read hooked @'+R+' try '+tries}); }catch(e){} }
    if(doneW && doneR){ clearInterval(iv); send({tag:'LOG',msg:'all hooks installed (try '+tries+')'}); }
    if(tries>3000) clearInterval(iv);
}, 15);
