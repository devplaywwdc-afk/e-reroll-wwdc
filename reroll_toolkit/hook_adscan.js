'use strict';
/* Log every gate_*.php command the app sends, via SSL_write at base+OFF (proven offset
   from hook_capture.js). Shows which command the ad-watch flow actually uses. */
var LIB='libUE4.so';
var OFF_SSL_WRITE=0x7FFA9B4;
function ascii(b){var u=new Uint8Array(b),s='';for(var i=0;i<u.length;i++){var c=u[i];s+=(c>=32&&c<127)?String.fromCharCode(c):'.';}return s;}
var done=false,n=0;
var iv=setInterval(function(){ n++;
  if(done){clearInterval(iv);return;}
  var m=null; try{m=Process.findModuleByName(LIB);}catch(e){}
  if(!m){ if(n>3000)clearInterval(iv); return; }
  try{
    Interceptor.attach(m.base.add(OFF_SSL_WRITE),{ onEnter:function(a){ try{
      var ln=a[2].toInt32(); if(ln<=0||ln>1048576) return;
      var s=ascii(a[1].readByteArray(Math.min(ln,700)));
      var mm=s.match(/gate_(CMD_[A-Z_]+)\.php/);
      if(mm) console.log('[W] '+mm[1]+'  ('+ln+'B)');
    }catch(e){} } });
    done=true; console.log('[ADSCAN] SSL_write hooked @'+m.base.add(OFF_SSL_WRITE)+' — กดดูโฆษณาได้เลย');
  }catch(e){ if(n>3000){clearInterval(iv);console.log('[ADSCAN] fail '+e);} }
},20);
console.log('[ADSCAN] loaded...');
