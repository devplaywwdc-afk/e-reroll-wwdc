'use strict';
/* Loaded by frida-gadget in SCRIPT mode at process boot (before create_user).
 * Hooks the create_user serializer sub_75F2B40 AND SSL_write; writes captures
 * to device files (no external client in script mode). */
var LIB='libUE4.so';
var RVA_CREATE=0x75F2B40;
var OFF_W=0x7FFA9B4;
var OUT_DUMP='/data/user/0/jp.konami.pesam/files/create_dump.json';
var OUT_WIRE='/data/user/0/jp.konami.pesam/files/create_wire.txt';
var LOGP='/data/user/0/jp.konami.pesam/files/agent_boot.log';
var SIG_W='ff c3 00 d1 fd 7b 01 a9 f3 13 00 f9 fd 43 00 91 53 d0 3b d5 68 16 40 f9 e8 07 00 f9 e2 01 f8 37 e2 03 02 2a e3 03 00 91 8c ff ff 97 e8 03 40 b9 1f 00 00 71 00 c1 80 1a 68 16 40 f9 e9 07 40 f9 1f 01 09 eb 21 02 00 54 fd 7b 41 a9 f3 13 40 f9 ff c3 00 91';

function wfile(path,txt){ try{ var f=new File(path,'a'); f.write(txt); f.flush(); f.close(); }catch(e){} }
function log(m){ try{ wfile(LOGP, m+'\n'); }catch(e){} }

function baseFromMaps(){
    try{ var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
        var ls=t.split('\n'), best=null;
        for(var i=0;i<ls.length;i++){ if(ls[i].indexOf(LIB)===-1) continue;
            var lo=ptr('0x'+ls[i].split('-')[0]); if(best===null||lo.compare(best)<0) best=lo; }
        return best; }catch(e){ return null; }
}
function readStr(p){ try{ var flag=p.readU8();
    if(flag&1){ var len=p.add(8).readU64().toNumber(); var q=p.add(16).readPointer();
        if(q.isNull()||len>0x10000) return '<bad>'; return q.readUtf8String(len); }
    return p.add(1).readUtf8String(flag>>1); }catch(e){ return '<err>'; } }
function s(a1,off){ return readStr(a1.add(off)); }
var STR=[[0x1E8,'hash'],[0x200,'client_version'],[0x218,'platform'],[0x230,'device_identifier'],
 [0x248,'lang'],[0x260,'region'],[0x278,'pcspec'],[0x290,'user_eula_season'],
 [0x2A8,'eula_ip_country_a3'],[0x2C0,'eula_select_country_a3'],[0x2D8,'eula_ip_state'],
 [0x2F0,'eula_select_state'],[0x308,'eula'],[0x320,'eula_privacy_notice'],[0x338,'eula_myclub'],
 [0x350,'eula_japan_version'],[0x368,'eula_myclub_japan'],[0x380,'eula_country_age_kind'],
 [0x398,'eula_privacy_action'],[0x3B8,'user_name'],[0x3E8,'is_deleted'],
 [0x560,'cross_platform_option'],[0x608,'user_age_kind']];
function dumpStruct(a1){
    var f={}; STR.forEach(function(x){ f[x[1]]=s(a1,x[0]); });
    try{ f.user_id=a1.add(0x3B0).readU32(); }catch(e){}
    var rec={cmd:'CMD_CREATE_USER', ts:new Date().toISOString(), fields:f};
    wfile(OUT_DUMP, JSON.stringify(rec)+'\n');
    log('[DUMP] hash='+f.hash+' ver='+f.client_version+' eula8='+f.eula+'/'+f.eula_privacy_notice+'/'+f.eula_myclub);
}

function txt(bytes){ var u=new Uint8Array(bytes); var s=''; for(var i=0;i<u.length;i++){ var c=u[i]; s+=(c>=32&&c<127)?String.fromCharCode(c):'.'; } return s; }
function hex(bytes){ var u=new Uint8Array(bytes); var s=''; for(var i=0;i<u.length;i++) s+=('0'+u[i].toString(16)).slice(-2); return s; }

var base=baseFromMaps();
log('agent_boot start base='+base);

// --- serializer hook (retry until libUE4 present + attach ok) ---
var sHooked=false;
// --- SSL_write scan+hook (retry) ---
var wHooked=false, W=null;
function scanW(){ if(!base) return null; var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
    var hits=null; t.split('\n').forEach(function(l){ if(l.indexOf(LIB)===-1||l.indexOf(' r')===-1) return;
        var p=l.split(/\s+/); var a=p[0].split('-'); var st=ptr('0x'+a[0]); var sz=ptr('0x'+a[1]).sub(st).toInt32(); if(sz<=0) return;
        try{ var m=Memory.scanSync(st,sz,SIG_W); if(m.length&&!hits) hits=m[0].address; }catch(e){} });
    return hits; }

var tries=0;
var iv=setInterval(function(){
    tries++;
    if(!base) base=baseFromMaps();
    if(base && !sHooked){ try{
        Interceptor.attach(base.add(RVA_CREATE), { onEnter:function(a){ try{ dumpStruct(ptr(a[0])); }catch(e){ log('dump err '+e); } } });
        sHooked=true; log('serializer hooked @'+base.add(RVA_CREATE)+' try '+tries);
    }catch(e){} }
    if(!wHooked){ if(!W) W=scanW(); if(W){ try{
        Interceptor.attach(W, { onEnter:function(a){ try{ var n=a[2].toInt32(); if(n<=0||n>1048576) return;
            var b=a[1].readByteArray(n); var st=txt(b); if(st.indexOf('CMD_CREATE_USER')>=0||st.indexOf('CMD_LOGIN')>=0||st.indexOf('CMD_GET_EULA')>=0){
                var cmd=(st.match(/CMD_[A-Z_]+/)||['?'])[0]; wfile(OUT_WIRE, cmd+' '+n+' '+hex(b)+'\n'); log('[WIRE] '+cmd+' '+n+'B'); } }catch(e){} } });
        wHooked=true; log('SSL_write hooked @'+W+' try '+tries);
    }catch(e){} } }
    if(sHooked && wHooked){ clearInterval(iv); log('all hooks installed try '+tries); }
    if(tries>5000) clearInterval(iv);
}, 10);
