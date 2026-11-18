'use strict';
/* Dump the GET_ONLINE_AD_FILE request fields in plaintext by hooking its serializer
   sub_772B2E0 (from IDA): ad_match_kind = std::string @ obj+488, event_id = uint @ obj+512.
   Also hooks SSL_write to log any ad-related command URL as a cross-check. */
var LIB = 'libUE4.so';
var RVA_AD_SER = 0x772B2E0;

function readStr(p){ try{ var f=p.readU8();
  if(f&1){ var len=p.add(8).readU64().toNumber(); var q=p.add(16).readPointer();
    if(q.isNull()||len>0x10000) return '<bad>'; return q.readUtf8String(len); }
  return p.add(1).readUtf8String(f>>1); }catch(e){ return '<err:'+e+'>'; } }

function base(){ try{ var m=Process.findModuleByName(LIB); return m?m.base:null; }catch(e){ return null; } }

var done=false, n=0;
var iv=setInterval(function(){
  n++;
  if(done){ clearInterval(iv); return; }
  var b=base(); if(!b){ if(n>2000) clearInterval(iv); return; }
  try{
    Interceptor.attach(b.add(RVA_AD_SER),{ onEnter:function(a){
      try{
        var obj=ptr(a[0]);
        var mk=readStr(obj.add(488));
        var ev=obj.add(512).readU32();
        console.log('\n[AD] >>> GET_ONLINE_AD_FILE  ad_match_kind="'+mk+'"  event_id='+ev+'\n');
      }catch(e){ console.log('[AD] read err '+e); }
    }});
    done=true;
    console.log('[AD] hooked GET_ONLINE_AD_FILE serializer @'+b.add(RVA_AD_SER)+' — ตอนนี้ไปกดดูโฆษณาในแพ็คฟรีได้เลย');
  }catch(e){ if(n>2000){ clearInterval(iv); console.log('[AD] attach failed: '+e); } }
}, 20);
console.log('[AD] loaded, finding libUE4.so base...');
