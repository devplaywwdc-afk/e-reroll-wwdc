'use strict';
/* Dump AES key material from libUE4 software-AES functions (Te0 table-based).
 * Resolve addresses via /proc/self/maps FILE-OFFSET math (handles houdini
 * segment gaps: vaddr==file offset for these LOAD segments).
 * The block-encrypt's key arg (AES_KEY*) has the raw key in its first 16/32 bytes
 * (round-0 key). The GATE key is CONSTANT -> shows as a repeated value across calls.
 */
var LIB = 'libUE4.so';
var RVAS = [0x7F320F8, 0x7F31D78, 0x7F35714, 0x7F33384, 0x7F35378, 0x7F32FE0];
var OUT = '/data/user/0/jp.konami.pesam/files/aes_dump.txt';

function wfile(t){ try{ var f=new File(OUT,'a'); f.write(t); f.flush(); f.close(); }catch(e){} }
function log(m){ console.log('[aes] '+m); wfile('#'+m+'\n'); }

function segs(){
    var out=[];
    try{ var f=new File('/proc/self/maps','r'); var t=f.readText(); f.close();
        t.split('\n').forEach(function(l){ if(l.indexOf(LIB)===-1) return;
            var p=l.split(/\s+/); var a=p[0].split('-');
            out.push({start:ptr('0x'+a[0]), size:ptr('0x'+a[1]).sub(ptr('0x'+a[0])).toInt32(),
                      off:parseInt(p[2],16), perm:p[1]}); }); }catch(e){}
    return out;
}
function resolve(rva){ // vaddr==file offset; find seg by file-offset window
    var s=segs();
    for(var i=0;i<s.length;i++){ if(rva>=s[i].off && rva<s[i].off+s[i].size) return s[i].start.add(rva-s[i].off); }
    return null;
}
function hx(p,n){ try{ var u=new Uint8Array(p.readByteArray(n)); var s=''; for(var i=0;i<u.length;i++) s+=('0'+u[i].toString(16)).slice(-2); return s; }catch(e){ return '?'; } }

var done=RVAS.map(function(){return false;});
var installed=0, tries=0;
var iv=setInterval(function(){
    tries++;
    RVAS.forEach(function(rva, idx){
        if(done[idx]) return;
        var a=resolve(rva);
        if(!a) return;
        try{
            (function(rvaHex){
                Interceptor.attach(a, { onEnter:function(args){ try{
                    var line='F'+rvaHex+' ';
                    for(var k=0;k<4;k++){ try{ var pv=args[k]; if(!pv.isNull()){ line+='a'+k+':'+hx(pv,32)+' '; } }catch(e){} }
                    wfile(line+'\n');
                }catch(e){} } });
            })(rva.toString(16));
            done[idx]=true; installed++;
            log('hooked RVA 0x'+rva.toString(16)+' @'+a);
        }catch(e){}
    });
    if(installed>=RVAS.length){ clearInterval(iv); log('all AES hooks installed (try '+tries+')'); }
    if(tries>3000) clearInterval(iv);
}, 20);
log('hook_aes loaded; resolving...');
