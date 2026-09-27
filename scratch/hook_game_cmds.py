import frida
import time

mgr = frida.get_device_manager()
dev = mgr.add_remote_device('127.0.0.1:27042')
session = dev.attach('Gadget')

script_code = """
var base = ptr("0x7bbec34f8000");

// Hook sub_2F92514: map insert key-value
// int64 sub_2F92514(map_ptr, key_str)
var sub_2F92514 = base.add(0x2F92514);
// Hook sub_2F8F898: map store value
var sub_2F8F898 = base.add(0x2F8F898);

var curKey = "";
Interceptor.attach(sub_2F92514, {
    onEnter: function(args) {
        try {
            var k = args[1].readCString();
            if (k) {
                curKey = k;
            }
        } catch(e) {}
    }
});

Interceptor.attach(sub_2F8F898, {
    onEnter: function(args) {
        if (curKey.length > 0) {
            try {
                // Try reading string from args[1]
                var valPtr = args[1];
                var s = "";
                try { s = valPtr.readCString(); } catch(e){}
                if (!s || s.length === 0) {
                    try { s = valPtr.readPointer().readCString(); } catch(e){}
                }
                if (s && s.length > 0 && s.length < 100) {
                    console.log("MAP[" + curKey + "] = " + JSON.stringify(s));
                }
            } catch(e) {}
            curKey = "";
        }
    }
});

console.log("Hooked map insertions!");
"""

script = session.create_script(script_code)
def on_msg(m, d): print("FRIDA:", m)
script.on('message', on_msg)
script.load()

# Let it run for 10 seconds to catch heartbeats and any active commands
time.sleep(16)
session.detach()
