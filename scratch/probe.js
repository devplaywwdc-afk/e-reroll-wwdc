'use strict';
var LIB='libUE4.so';
function baseFromMaps(){
    var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
    var ls=t.split('\n'), best=null, ranges=[];
    for(var i=0;i<ls.length;i++){ if(ls[i].indexOf(LIB)===-1) continue;
        var p=ls[i].split(' '); var a=p[0].split('-'); var lo=ptr('0x'+a[0]); var hi=ptr('0x'+a[1]);
        var perm=p[1]; ranges.push([lo,hi,perm]);
        if(best===null||lo.compare(best)<0) best=lo; }
    return {base:best, ranges:ranges};
}
function hex(addr,n){ try{ var u=new Uint8Array(addr.readByteArray(n)); var s='';
    for(var i=0;i<u.length;i++) s+=('0'+u[i].toString(16)).slice(-2)+' '; return s; }catch(e){ return '<err '+e+'>'; } }

var m=baseFromMaps();
console.log('[probe] base='+m.base);
m.ranges.forEach(function(r){ console.log('  seg '+r[0]+'-'+r[1]+' '+r[2]); });
console.log('[probe] @+0x75F2B40 (serializer) : '+hex(m.base.add(0x75F2B40),16));
console.log('[probe] IDA serializer expect     : fd 7b ba a9 fc 6f 01 a9 fa 67 02 a9 f8 5f 03 a9');
console.log('[probe] @+0x7FFA9B4 (SSL_write?)  : '+hex(m.base.add(0x7FFA9B4),16));
console.log('[probe] @+0x7FFA780 (SSL_read?)   : '+hex(m.base.add(0x7FFA780),16));
console.log('[probe] prologue expect           : ff c3 00 d1 fd 7b 01 a9 f3 13 00 f9 fd 43 00 91');

// scan the executable range(s) for the shared SSL prologue
var sig='ff c3 00 d1 fd 7b 01 a9 f3 13 00 f9 fd 43 00 91 53 d0 3b d5 68 16 40 f9 e8 07 00 f9';
var hits=[];
m.ranges.forEach(function(r){
    if(r[2].indexOf('x')===-1) return;
    var size=r[1].sub(r[0]).toInt32();
    try{ Memory.scanSync(r[0], size, sig).forEach(function(h){ hits.push(h.address); }); }catch(e){ console.log('scan err '+e); }
});
console.log('[probe] SSL-prologue matches: '+hits.length);
hits.slice(0,20).forEach(function(a){ console.log('   RVA 0x'+a.sub(m.base).toString(16)+'  @'+a); });
