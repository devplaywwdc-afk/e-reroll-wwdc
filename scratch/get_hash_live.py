import frida

mgr = frida.get_device_manager()
dev = mgr.add_remote_device('127.0.0.1:27042')
session = dev.attach('Gadget')

script_code = """
var base = ptr("0x7bbec34f8000");
var sub_7ADEF3C = new NativeFunction(base.add(0x7ADEF3C), 'pointer', ['pointer']);

var outStr = Memory.alloc(64);
outStr.writeByteArray(new Array(64).fill(0));

sub_7ADEF3C(outStr);

var b0 = outStr.readU8();
var res = "";
if ((b0 & 1) === 0) {
    res = outStr.add(1).readCString();
} else {
    var p = outStr.add(16).readPointer();
    res = p.readCString();
}
console.log("HASH RESULT: " + JSON.stringify(res));
console.log("Raw bytes: " + hexdump(outStr, {length: 32}));
"""

script = session.create_script(script_code)
def on_msg(m, d): print("FRIDA:", m)
script.on('message', on_msg)
script.load()
session.detach()
