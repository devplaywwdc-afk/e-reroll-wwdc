import frida

mgr = frida.get_device_manager()
dev = mgr.add_remote_device('127.0.0.1:27042')
session = dev.attach('Gadget')

script = session.create_script("""
var mods = Process.enumerateModules();
console.log("Total modules: " + mods.length);
mods.slice(0, 30).forEach(function(m) {
    console.log("MODULE: " + m.name + " (" + m.path + ")");
});
""")
def on_message(msg, data):
    print("FRIDA:", msg)
script.on('message', on_message)
script.load()
session.detach()
