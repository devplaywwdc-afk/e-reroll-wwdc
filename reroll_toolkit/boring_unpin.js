'use strict';
function exp(n){ try{return Module.getGlobalExportByName(n);}catch(e){} try{return Process.getModuleByName('libssl.so').findExportByName(n);}catch(e){} return null; }
var okCb = new NativeCallback(function(ssl, out_alert){ return 0; }, 'int', ['pointer','pointer']); // ssl_verify_ok
function install(){
  var done=0;
  ['SSL_CTX_set_custom_verify','SSL_set_custom_verify'].forEach(function(n){
    var a=exp(n); if(!a){ console.log('[boring] miss '+n); return; }
    Interceptor.attach(a,{ onEnter:function(args){ args[1]=ptr(0); args[2]=okCb; } });
    console.log('[boring] hooked '+n+' @'+a); done++;
  });
  var g=exp('SSL_get_verify_result');
  if(g){ Interceptor.replace(g, new NativeCallback(function(ssl){return 0;},'long',['pointer'])); console.log('[boring] SSL_get_verify_result->0'); }
  // legacy verify callback setters
  ['SSL_CTX_set_verify','SSL_set_verify'].forEach(function(n){
    var a=exp(n); if(a){ Interceptor.attach(a,{onEnter:function(args){ args[1]=ptr(0); }}); console.log('[boring] relaxed '+n); }
  });
  return done;
}
if(!install()){ var iv=setInterval(function(){ if(install()){clearInterval(iv);} },50); }
console.log('[boring] loaded');
