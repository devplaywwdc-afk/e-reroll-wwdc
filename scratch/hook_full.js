/*
 * hook_full.js — bypass libcrashlytics-common.so init crash, then dump
 * CMD_CREATE_USER from sub_75F2B40 in libUE4.so.
 *
 * loaded via ZygiskFrida gadget (listen mode, on_load=wait) so we run BEFORE
 * crashlytics loads; we hook android_dlopen_ext to neutralize the crashing
 * signal-handler installer the moment crashlytics maps in.
 */
var UE4 = "libUE4.so";
var RVA = 0x75F2B40;
var CRASHLYTICS = "libcrashlytics-common.so";
var crashPatched = false, ue4Hooked = false;

function log(m){ console.log(m); try{ send({tag:"LOG", msg:m}); }catch(e){} }

/* ---- libc++ std::string reader (SSO-aware) ---- */
function readStdString(p){
    try{
        var flag = p.readU8();
        if(flag & 1){
            var len = p.add(8).readU64().toNumber();
            var ptr = p.add(16).readPointer();
            if(ptr.isNull() || len>0x10000) return "<bad>";
            return ptr.readUtf8String(len);
        } else { return p.add(1).readUtf8String(flag>>1); }
    }catch(e){ return "<err>"; }
}
function s(a1,off){ return readStdString(a1.add(off)); }

var STR = [
 [0x1E8,"hash"],[0x200,"client_version"],[0x218,"platform"],[0x230,"device_identifier"],
 [0x248,"lang"],[0x260,"region"],[0x278,"pcspec"],[0x290,"user_eula_season"],
 [0x2A8,"eula_ip_country_a3"],[0x2C0,"eula_select_country_a3"],[0x2D8,"eula_ip_state"],
 [0x2F0,"eula_select_state"],[0x308,"eula"],[0x320,"eula_privacy_notice"],[0x338,"eula_myclub"],
 [0x350,"eula_japan_version"],[0x368,"eula_myclub_japan"],[0x380,"eula_country_age_kind"],
 [0x398,"eula_privacy_action"],[0x3B8,"user_name"],[0x3E8,"is_deleted"],
 [0x560,"cross_platform_option"],[0x608,"user_age_kind"]
];

function dump(a1){
    var f={}; STR.forEach(function(x){ f[x[1]]=s(a1,x[0]); });
    try{ f["user_id"]=a1.add(0x3B0).readU32(); }catch(e){}
    var ev={ eula:f["eula"], eula_privacy_notice:f["eula_privacy_notice"],
             eula_myclub:f["eula_myclub"], eula_japan_version:f["eula_japan_version"],
             eula_myclub_japan:f["eula_myclub_japan"] };
    send({tag:"CREATE_USER_DUMP", data:{cmd:"CMD_CREATE_USER", ts:new Date().toISOString(), fields:f, eula_version:ev}});
    log("[DUMP] hash="+f["hash"]+" ver="+f["client_version"]+" eula="+JSON.stringify(ev));
}

function hookUE4(){
    if(ue4Hooked) return;
    var m = Process.findModuleByName(UE4);
    if(!m) return;
    ue4Hooked = true;
    var t = m.base.add(RVA);
    Interceptor.attach(t, { onEnter:function(a){ try{ dump(ptr(a[0])); }catch(e){ log("[!] dump "+e); } } });
    log("[+] serializer hook @ "+t+" ("+UE4+" base "+m.base+")");
}

function patchCrashlytics(){
    if(crashPatched) return;
    var m = Process.findModuleByName(CRASHLYTICS);
    if(!m) return;
    crashPatched = true;
    log("[+] "+CRASHLYTICS+" base "+m.base+" — neutralizing signal-handler installer");
    var patched = false;
    // primary: patch by RVA from tombstone (install_signal_handler_linker @ 0x41800)
    try{
        var fnaddr = m.base.add(0x41800);
        Interceptor.replace(fnaddr, new NativeCallback(function(){ return 0; }, 'int', []));
        log("[+] stubbed install_signal_handler_linker @ "+fnaddr+" (RVA 0x41800)");
        patched = true;
    }catch(e){ log("[!] rva replace: "+e); }
    // fallback: exported symbol (frida 17: module-instance method)
    if(!patched) try{
        var exp = m.findExportByName ? m.findExportByName("install_signal_handler_linker") : null;
        if(exp){
            Interceptor.replace(exp, new NativeCallback(function(){ return 0; }, 'int', []));
            log("[+] stubbed install_signal_handler_linker @ "+exp);
            patched = true;
        }
    }catch(e){ log("[!] export replace: "+e); }
    // fallback: scan symbols
    if(!patched){
        try{
            m.enumerateSymbols().forEach(function(sym){
                if(sym.name.indexOf("install_signal_handler")>=0 && sym.address && !sym.address.isNull()){
                    Interceptor.replace(sym.address, new NativeCallback(function(){ return 0; }, 'int', []));
                    log("[+] stubbed "+sym.name+" @ "+sym.address);
                    patched = true;
                }
            });
        }catch(e){ log("[!] symbol scan: "+e); }
    }
    if(!patched) log("[!] install_signal_handler_linker not found — may still crash");
}

/* block crashlytics native lib from ever loading (Java + native), so its
 * signal-handler installer never runs. crashlytics is optional telemetry. */
function blockCrashlyticsJava(){
    try{
        if(!Java || !Java.available){ return; }
        Java.perform(function(){
            var System = Java.use("java.lang.System");
            System.loadLibrary.implementation = function(name){
                if(name && name.indexOf("crashlytics")>=0){ log("[Java] skip loadLibrary("+name+")"); return; }
                return this.loadLibrary(name);
            };
            var Runtime = Java.use("java.lang.Runtime");
            Runtime.loadLibrary0.overload("java.lang.Class","java.lang.String").implementation = function(c,name){
                if(name && name.indexOf("crashlytics")>=0){ log("[Java] skip loadLibrary0("+name+")"); return; }
                return this.loadLibrary0(c,name);
            };
            log("[+] Java loadLibrary crashlytics-block installed");
        });
    }catch(e){ log("[!] Java block: "+e); }
}
blockCrashlyticsJava();

/* native belt-and-suspenders: make dlopen of crashlytics a no-op success */
(function(){
    var p = globalExport("android_dlopen_ext");
    if(!p) return;
    try{
        Interceptor.attach(p, {
            onEnter:function(a){
                try{
                    var path = a[0].readCString();
                    if(path && path.indexOf("crashlytics")>=0){
                        this.skip = true;
                        // redirect to a harmless already-mapped lib so it "loads" ok
                        this.saved = a[0];
                        a[0] = Memory.allocUtf8String("/system/lib64/libc.so");
                        log("[native] redirect crashlytics dlopen -> libc.so");
                    }
                }catch(e){}
            }
        });
    }catch(e){ log("[!] dlopen redirect: "+e); }
})();

/* hook the loader so we patch crashlytics the instant it maps in */
function globalExport2(){}
function globalExport(name){
    try{ if(Module.findGlobalExportByName) return Module.findGlobalExportByName(name); }catch(e){}
    try{ if(Module.getGlobalExportByName) return Module.getGlobalExportByName(name); }catch(e){}
    return null;
}
["android_dlopen_ext","dlopen"].forEach(function(fn){
    var p = globalExport(fn);
    if(!p) return;
    Interceptor.attach(p, {
        onEnter:function(a){ try{ this.path = a[0].readCString(); }catch(e){ this.path=null; } },
        onLeave:function(r){
            if(this.path && this.path.indexOf("crashlytics")>=0){ patchCrashlytics(); }
            if(this.path && this.path.indexOf("libUE4")>=0){ hookUE4(); }
        }
    });
    log("[+] loader hook on "+fn);
});

// in case libs are already present
patchCrashlytics();
hookUE4();
var iv = setInterval(function(){ patchCrashlytics(); hookUE4(); }, 20);
setTimeout(function(){ clearInterval(iv); }, 60000);

log("[*] hook_full ready");
