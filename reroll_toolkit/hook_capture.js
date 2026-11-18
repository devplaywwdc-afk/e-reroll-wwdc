'use strict';
/*
 * hook_capture.js — eFootball (jp.konami.pesam) create_user / login capture
 *
 * Captures the EXACT wire payload of account-creation gRPC commands by hooking
 * OpenSSL SSL_write / SSL_read (statically linked in libUE4.so), plus a direct
 * dump of the create_user serializer struct as a cross-check.
 *
 * Works reliably on NATIVE arm64 (physical device or Apple-silicon arm64
 * emulator). On MuMu/x86 houdini translation these inline hooks fire only
 * intermittently — use a native-arm target.
 *
 * Emits frida `send()` messages; capture.py writes them to disk and decodes.
 *   { tag:'WIRE', dir:'w'|'r', cmd, n } + <ArrayBuffer>   raw gRPC frame
 *   { tag:'DUMP', fields:{...} }                          create_user struct
 *   { tag:'LOG', msg }
 */
var LIB = 'libUE4.so';

/* ---- offsets verified in IDA against libUE4.so (imagebase 0). If the game
   updates and the signature scan fails, re-verify these in a fresh IDB. ---- */
var OFF_SSL_WRITE = 0x7FFA9B4;
var OFF_SSL_READ  = 0x7FFA780;
var RVA_CREATE    = 0x75F2B40;   // create_user serializer sub_75F2B40

/* SSL_write / SSL_read are thin wrappers with an identical 80-byte body except
   one byte at offset 40 (the bl to SSL_write_ex=0x8c vs SSL_read_ex=0x33).
   Signature scan is version-robust and avoids base+RVA segment-gap problems. */
var SIG_W = 'ff c3 00 d1 fd 7b 01 a9 f3 13 00 f9 fd 43 00 91 53 d0 3b d5 68 16 40 f9 e8 07 00 f9 e2 01 f8 37 e2 03 02 2a e3 03 00 91 8c ff ff 97 e8 03 40 b9 1f 00 00 71 00 c1 80 1a 68 16 40 f9 e9 07 40 f9 1f 01 09 eb 21 02 00 54 fd 7b 41 a9 f3 13 40 f9 ff c3 00 91';
var SIG_R = SIG_W.replace(' 8c ff ff 97 ', ' 33 ff ff 97 ');

function log(m){ console.log('[cap] ' + m); try{ send({tag:'LOG', msg:m}); }catch(e){} }

/* base of libUE4.so — prefer the module API, fall back to /proc/self/maps */
function moduleBase(){
    try{ var m = Process.findModuleByName(LIB); if(m) return m.base; }catch(e){}
    try{
        var f = new File('/proc/self/maps','r'); var t = f.readText(); f.close();
        var best=null;
        t.split('\n').forEach(function(l){ if(l.indexOf(LIB)===-1) return;
            var lo = ptr('0x'+l.split('-')[0]); if(best===null||lo.compare(best)<0) best=lo; });
        return best;
    }catch(e){ return null; }
}
function segs(){
    var out=[];
    try{ var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
        t.split('\n').forEach(function(l){ if(l.indexOf(LIB)===-1||l.indexOf(' r')===-1) return;
            var p=l.split(/\s+/); var a=p[0].split('-');
            out.push({start:ptr('0x'+a[0]), size:ptr('0x'+a[1]).sub(ptr('0x'+a[0])).toInt32()}); }); }catch(e){}
    return out;
}
function scanSig(sig){
    var hit=null; segs().forEach(function(s){ if(hit||s.size<=0) return;
        try{ var m=Memory.scanSync(s.start,s.size,sig); if(m.length) hit=m[0].address; }catch(e){} });
    return hit;
}

function ascii(bytes){ var u=new Uint8Array(bytes),s=''; for(var i=0;i<u.length;i++){var c=u[i]; s+=(c>=32&&c<127)?String.fromCharCode(c):'.';} return s; }
function hex(bytes){ var u=new Uint8Array(bytes),s=''; for(var i=0;i<u.length;i++) s+=('0'+u[i].toString(16)).slice(-2); return s; }
var INTEREST=/CMD_CREATE_USER|CMD_LOGIN|CMD_GET_EULA_AREA_LIST|CMD_GET_COUNTRY_LIST|CMD_SET_USER_EULA_INFO/;

function hookW(W){ Interceptor.attach(W,{ onEnter:function(a){ try{
    var n=a[2].toInt32(); if(n<=0||n>1048576) return;
    var b=a[1].readByteArray(n); var s=ascii(b);
    if(INTEREST.test(s)){ var cmd=(s.match(/CMD_[A-Z_]+/)||['?'])[0];
        log('SSL_write '+cmd+' ('+n+'B)'); send({tag:'WIRE',dir:'w',cmd:cmd,n:n}, b); }
}catch(e){} } }); }
function hookR(R){ Interceptor.attach(R,{ onEnter:function(a){ this.b=a[1]; }, onLeave:function(r){ try{
    var n=r.toInt32(); if(n<=0||n>1048576) return;
    var b=this.b.readByteArray(n); var s=ascii(b);
    if(INTEREST.test(s)||s.indexOf('auth_code')>=0){ var cmd=(s.match(/CMD_[A-Z_]+/)||['resp'])[0];
        send({tag:'WIRE',dir:'r',cmd:cmd,n:n}, b); }
}catch(e){} } }); }

/* create_user serializer struct dump (cross-check; libc++ std::string SSO) */
function readStr(p){ try{ var f=p.readU8();
    if(f&1){ var len=p.add(8).readU64().toNumber(); var q=p.add(16).readPointer();
        if(q.isNull()||len>0x10000) return '<bad>'; return q.readUtf8String(len); }
    return p.add(1).readUtf8String(f>>1); }catch(e){ return '<err>'; } }
var STR=[[0x1E8,'hash'],[0x200,'client_version'],[0x218,'platform'],[0x230,'device_identifier'],
 [0x248,'lang'],[0x260,'region'],[0x278,'pcspec'],[0x290,'user_eula_season'],
 [0x2A8,'eula_ip_country_a3'],[0x2C0,'eula_select_country_a3'],[0x2D8,'eula_ip_state'],
 [0x2F0,'eula_select_state'],[0x308,'eula'],[0x320,'eula_privacy_notice'],[0x338,'eula_myclub'],
 [0x350,'eula_japan_version'],[0x368,'eula_myclub_japan'],[0x380,'eula_country_age_kind'],
 [0x398,'eula_privacy_action'],[0x3B8,'user_name'],[0x3E8,'is_deleted'],
 [0x560,'cross_platform_option'],[0x608,'user_age_kind']];
function dumpCreate(a1){ var f={}; STR.forEach(function(x){ f[x[1]]=readStr(a1.add(x[0])); });
    try{ f.user_id=a1.add(0x3B0).readU32(); }catch(e){}
    log('DUMP hash='+f.hash+' ver='+f.client_version); send({tag:'DUMP', fields:f}); }

/* install everything, retrying scan+attach until each succeeds */
var base=null, W=null, R=null, dW=false, dR=false, dC=false, n=0;
var iv=setInterval(function(){
    n++;
    if(!base) base=moduleBase();
    // PRIMARY: signature scan → finds the REAL SSL_write/SSL_read even when
    // base+RVA lands in a houdini segment-gap (MuMu). Slower but correct; a
    // UI-gated create_user gives plenty of runway. base+RVA only as fallback.
    if(!W) W=scanSig(SIG_W);
    if(!R) R=scanSig(SIG_R);
    if(!W && base) W=base.add(OFF_SSL_WRITE);
    if(!R && base) R=base.add(OFF_SSL_READ);
    if(W&&!dW){ try{ hookW(W); dW=true; log('SSL_write hooked @'+W); }catch(e){} }
    if(R&&!dR){ try{ hookR(R); dR=true; log('SSL_read hooked @'+R); }catch(e){} }
    if(base&&!dC){ try{ Interceptor.attach(base.add(RVA_CREATE),{ onEnter:function(a){ try{ dumpCreate(ptr(a[0])); }catch(e){} } }); dC=true; log('serializer hooked @'+base.add(RVA_CREATE)); }catch(e){} }
    if(dW&&dR&&dC){ clearInterval(iv); log('all hooks installed (try '+n+')'); }
    if(n>3000) clearInterval(iv);
}, 15);
log('hook_capture loaded; scanning...');
