'use strict';
/* dump CMD_CREATE_USER struct from sub_75F2B40 (verified @ base+0x75F2B40).
   base resolved from /proc/self/maps (findModuleByName unreliable under bridge). */
var LIB='libUE4.so';
var RVA=0x75F2B40;

function baseFromMaps(){
    var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
    var ls=t.split('\n'), best=null;
    for(var i=0;i<ls.length;i++){ if(ls[i].indexOf(LIB)===-1) continue;
        var lo=ptr('0x'+ls[i].split('-')[0]); if(best===null||lo.compare(best)<0) best=lo; }
    return best;
}
function readStr(p){ try{ var flag=p.readU8();
    if(flag&1){ var len=p.add(8).readU64().toNumber(); var ptr2=p.add(16).readPointer();
        if(ptr2.isNull()||len>0x10000) return '<bad>'; return ptr2.readUtf8String(len); }
    return p.add(1).readUtf8String(flag>>1); }catch(e){ return '<err>'; } }
function s(a1,off){ return readStr(a1.add(off)); }

var STR=[[0x1E8,'hash'],[0x200,'client_version'],[0x218,'platform'],[0x230,'device_identifier'],
 [0x248,'lang'],[0x260,'region'],[0x278,'pcspec'],[0x290,'user_eula_season'],
 [0x2A8,'eula_ip_country_a3'],[0x2C0,'eula_select_country_a3'],[0x2D8,'eula_ip_state'],
 [0x2F0,'eula_select_state'],[0x308,'eula'],[0x320,'eula_privacy_notice'],[0x338,'eula_myclub'],
 [0x350,'eula_japan_version'],[0x368,'eula_myclub_japan'],[0x380,'eula_country_age_kind'],
 [0x398,'eula_privacy_action'],[0x3B8,'user_name'],[0x3E8,'is_deleted'],
 [0x560,'cross_platform_option'],[0x608,'user_age_kind']];

function dump(a1){
    var f={}; STR.forEach(function(x){ f[x[1]]=s(a1,x[0]); });
    try{ f['user_id']=a1.add(0x3B0).readU32(); }catch(e){}
    var ev={eula:f['eula'],eula_privacy_notice:f['eula_privacy_notice'],eula_myclub:f['eula_myclub'],
            eula_japan_version:f['eula_japan_version'],eula_myclub_japan:f['eula_myclub_japan']};
    send({tag:'CREATE_USER_DUMP', data:{cmd:'CMD_CREATE_USER', ts:new Date().toISOString(), fields:f, eula_version:ev}});
    console.log('[DUMP] hash='+f['hash']+' ver='+f['client_version']+' eula='+JSON.stringify(ev));
}

var base=baseFromMaps();
if(!base){ console.log('[!] libUE4 base not found'); }
else{
    var t=base.add(RVA);
    console.log('[+] libUE4 base='+base+'  hooking serializer @'+t);
    Interceptor.attach(t,{ onEnter:function(a){ try{ dump(ptr(a[0])); }catch(e){ console.log('[!] '+e); } } });
    console.log('[+] ready — create a fresh guest ID now');
    send({tag:'LOG', msg:'serializer hook installed @'+t});
}
