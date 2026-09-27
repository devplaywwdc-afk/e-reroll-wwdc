import frida

mgr = frida.get_device_manager()
dev = mgr.add_remote_device('127.0.0.1:27042')
session = dev.attach('Gadget')

script = session.create_script("""
var base = ptr("0x7bbec34f8000");
console.log("Reading ELF header at " + base);
console.log("Magic: " + hexdump(base, {length: 16}));

// In IDA Pro, aClientVersion string is at 0xc151b0
var aClientVersion = base.add(0xc151b0);
console.log("At aClientVersion: " + Memory.readCString(aClientVersion));
""")
def on_msg(m, d): print("FRIDA:", m)
script.on('message', on_msg)
script.load()
session.detach()
