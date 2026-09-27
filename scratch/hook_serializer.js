/*
 * hook_serializer.js — dump CMD_CREATE_USER field values straight from the
 * native serializer struct (sub_75F2B40) in libUE4.so.
 *
 * sub_75F2B40 builds the msgpack object tree from a struct at args[0].
 * imagebase in the IDB is 0x0, so RVA == file addr; runtime addr = base + RVA.
 *
 * usage:
 *   frida -U -f jp.konami.pesam -l hook_serializer.js --runtime=v8
 *   then create a fresh guest ID in the game.
 */

var MODULE = "libUE4.so";
var RVA = 0x75F2B40;           // sub_75F2B40
var g_base = null;

// libc++ std::string reader (SSO-aware). p = pointer to the 24-byte string obj.
function readStdString(p) {
    try {
        var flag = p.readU8();
        if (flag & 1) {                       // long / heap mode
            var len = p.add(8).readU64().toNumber();
            var ptr = p.add(16).readPointer();
            if (ptr.isNull() || len > 0x10000) return "<bad>";
            return ptr.readUtf8String(len);
        } else {                              // short / inline mode
            var len = flag >> 1;
            return p.add(1).readUtf8String(len);
        }
    } catch (e) {
        return "<err:" + e + ">";
    }
}

function str(a1, off)  { return readStdString(a1.add(off)); }
function u32(a1, off)  { return a1.add(off).readU32(); }
function u64(a1, off)  { return a1.add(off).readU64().toString(); }

// top-level std::string fields (offset -> key), from the decompile offset map
var STR_FIELDS = [
    [0x1E8, "hash"],                  // 488
    [0x200, "client_version"],        // 512
    [0x218, "platform"],              // 536
    [0x230, "device_identifier"],     // 560  (device.device_identifier)
    [0x248, "lang"],                  // 584
    [0x260, "region"],                // 608
    [0x278, "pcspec"],                // 632
    [0x290, "user_eula_season"],      // 656
    [0x2A8, "eula_ip_country_a3"],    // 680
    [0x2C0, "eula_select_country_a3"],// 704
    [0x2D8, "eula_ip_state"],         // 728
    [0x2F0, "eula_select_state"],     // 752
    [0x308, "eula"],                  // 776
    [0x320, "eula_privacy_notice"],   // 800
    [0x338, "eula_myclub"],           // 824
    [0x350, "eula_japan_version"],    // 848
    [0x368, "eula_myclub_japan"],     // 872
    [0x380, "eula_country_age_kind"], // 896
    [0x398, "eula_privacy_action"],   // 920
    // pes_user_info.user_id is uint at 944 (0x3B0) -> handled below
    [0x3B8, "user_name"],             // 952
    [0x3E8, "is_deleted"],            // 1000
    [0x560, "cross_platform_option"], // 1376
    [0x608, "user_age_kind"],         // 1544
];

// scalar fields worth grabbing
var INT_FIELDS = [
    [0x3B0, "user_id", 32],           // 944
];

function dumpStruct(a1) {
    var out = { cmd: "CMD_CREATE_USER", ts: new Date().toISOString(), fields: {} };
    STR_FIELDS.forEach(function (f) { out.fields[f[1]] = str(a1, f[0]); });
    INT_FIELDS.forEach(function (f) { out.fields[f[1]] = u32(a1, f[0]); });

    // rebuild the eula_version block the server checks (the blocker)
    out.eula_version = {
        eula:               out.fields["eula"],
        eula_privacy_notice:out.fields["eula_privacy_notice"],
        eula_myclub:        out.fields["eula_myclub"],
        eula_japan_version: out.fields["eula_japan_version"],
        eula_myclub_japan:  out.fields["eula_myclub_japan"]
    };

    send({ tag: "CREATE_USER_DUMP", data: out });
    console.log("\n===== CMD_CREATE_USER (live from game) =====");
    console.log("  hash            : " + out.fields["hash"]);
    console.log("  client_version  : " + out.fields["client_version"]);
    console.log("  platform        : " + out.fields["platform"]);
    console.log("  lang / region   : " + out.fields["lang"] + " / " + out.fields["region"]);
    console.log("  user_eula_season: " + out.fields["user_eula_season"]);
    console.log("  eula_country_age: " + out.fields["eula_country_age_kind"]);
    console.log("  eula_privacy    : " + out.fields["eula_privacy_action"]);
    console.log("  eula_version    : " + JSON.stringify(out.eula_version));
    console.log("  device_id       : " + out.fields["device_identifier"]);
    console.log("============================================\n");
}

function install(base) {
    var target = base.add(RVA);
    console.log("[+] " + MODULE + " base = " + base + "  hooking sub_75F2B40 @ " + target);
    Interceptor.attach(target, {
        onEnter: function (args) {
            try { dumpStruct(ptr(args[0])); }
            catch (e) { console.log("[!] dump error: " + e); }
        }
    });
    console.log("[+] hook installed. create a fresh guest ID now.");
}

// wait for libUE4.so to load (spawn mode: module not present at t=0)
(function waitForModule() {
    var m = Process.findModuleByName(MODULE);
    if (m) { g_base = m.base; install(g_base); return; }
    console.log("[..] waiting for " + MODULE + " ...");
    var iv = setInterval(function () {
        var mm = Process.findModuleByName(MODULE);
        if (mm) { clearInterval(iv); g_base = mm.base; install(g_base); }
    }, 200);
})();
