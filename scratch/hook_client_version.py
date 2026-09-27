import frida
import json
import time

mgr = frida.get_device_manager()
dev = mgr.add_remote_device('127.0.0.1:27042')
session = dev.attach('Gadget')

script_code = """
var mod = Process.findModuleByName("libUE4.so");
console.log("libUE4.so base: " + mod.base);

// Search memory in libUE4 for "client_version" references
// In sub_75F2B40 (CMD_CREATE_USER serializer), client_version is added
// Let's hook sub_2F92514 which is std::map::operator[] or json key lookup
var sub_2F92514 = mod.base.add(0x2F92514);
Interceptor.attach(sub_2F92514, {
    onEnter: function(args) {
        try {
            var key = Memory.readCString(args[1]);
            if (key === "client_version" || key === "auth_code" || key === "hash" || key === "device_identifier") {
                this.key = key;
                this.target = args[0];
            }
        } catch (e) {}
    }
});

// Also hook sub_2F8F898 which assigns the value to the key!
var sub_2F8F898 = mod.base.add(0x2F8F898);
Interceptor.attach(sub_2F8F898, {
    onEnter: function(args) {
        try {
            // args[1] is the value pointer (std::string or similar)
            // In libUE4, std::string has short string optimization or pointer at offset 0/16
            var valPtr = args[1];
            // Read as string
            var s = "";
            try { s = Memory.readUtf8String(valPtr); } catch(e){}
            if (!s || s.length === 0) {
                try { s = Memory.readUtf8String(Memory.readPointer(valPtr)); } catch(e){}
            }
            if (s && s.length > 0) {
                // Check if looks interesting
                console.log("[VAL ASSIGN] " + s);
            }
        } catch(e) {}
    }
});

// Let's also search memory for recent occurrences of "client_version"
var results = Memory.scanSync(mod.base, mod.size, "63 6c 69 65 6e 74 5f 76 65 72 73 69 6f 6e");
console.log("Scan found " + results.length + " matches for client_version");
"""

script = session.create_script(script_code)
def on_message(message, data):
    print("FRIDA:", message)

script.on('message', on_message)
script.load()

# Let's wait a few seconds to see if any value assigns trigger
time.sleep(3)
session.detach()
