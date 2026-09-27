import frida
import time

mgr = frida.get_device_manager()
dev = mgr.add_remote_device('127.0.0.1:27042')
session = dev.attach('Gadget')

script_code = """
var base = ptr("0x7bbec0594000");

// Hook sub_74E3374 (base serializer)
var sub_74E3374 = base.add(0x74E3374);
Interceptor.attach(sub_74E3374, {
    onEnter: function(args) {
        var obj = args[0];
        // Read cmd name
        var b0 = obj.add(312).readU8();
        var cmd = "";
        if ((b0 & 1) === 0) {
            cmd = obj.add(313).readCString();
        } else {
            cmd = obj.add(328).readPointer().readCString();
        }
        var uid = obj.add(340).readU32();
        console.log(">>> [CMD ENTER] cmd=" + cmd + " uid=" + uid);
    }
});

// Also hook protobuf wrap / send
// In CommandStream, field 1 is cmd, field 3 is base64(msgpack)
// Let's hook base64 encode or msgpack pack
// In UE4, msgpack packer writes to a buffer at a1 + 240
console.log("Hooks loaded!");
"""

script = session.create_script(script_code)
def on_msg(m, d): print("FRIDA:", m)
script.on('message', on_msg)
script.load()

print("Waiting for game commands...")
time.sleep(15)
session.detach()
