// Frida script: Hook gRPC send/recv in libUE4.so to capture CMD payloads
// Hooks SSL_write and SSL_read to intercept all TLS traffic

var captured = [];

// Hook SSL_write to capture outgoing gRPC frames
Interceptor.attach(Module.findExportByName("libssl.so", "SSL_write"), {
    onEnter: function(args) {
        this.buf = args[1];
        this.len = args[2].toInt32();
    },
    onLeave: function(retval) {
        if (this.len > 5) {
            try {
                var data = Memory.readByteArray(this.buf, Math.min(this.len, 8192));
                var bytes = new Uint8Array(data);
                // Look for gRPC frame (starts with 0x00 + 4 byte length)
                if (bytes[0] === 0x00 && this.len > 10) {
                    var msgLen = (bytes[1] << 24) | (bytes[2] << 16) | (bytes[3] << 8) | bytes[4];
                    if (msgLen > 0 && msgLen < this.len) {
                        // Check if payload contains CMD_ string marker (0x0a tag)
                        if (bytes[5] === 0x0a) {
                            var strLen = bytes[6];
                            if (strLen > 3 && strLen < 100) {
                                var cmdName = "";
                                for (var i = 0; i < strLen && (7+i) < bytes.length; i++) {
                                    cmdName += String.fromCharCode(bytes[7+i]);
                                }
                                if (cmdName.indexOf("CMD_") === 0) {
                                    // Extract base64 payload (field 3 = tag 0x1a)
                                    var hexDump = "";
                                    for (var j = 0; j < Math.min(bytes.length, 4096); j++) {
                                        hexDump += ("0" + bytes[j].toString(16)).slice(-2);
                                    }
                                    send({
                                        type: "grpc_send",
                                        cmd: cmdName,
                                        len: this.len,
                                        msgLen: msgLen,
                                        hex: hexDump
                                    });
                                }
                            }
                        }
                    }
                }
            } catch(e) {}
        }
    }
});

// Hook SSL_read to capture incoming gRPC frames
Interceptor.attach(Module.findExportByName("libssl.so", "SSL_read"), {
    onEnter: function(args) {
        this.buf = args[1];
        this.maxLen = args[2].toInt32();
    },
    onLeave: function(retval) {
        var readLen = retval.toInt32();
        if (readLen > 10) {
            try {
                var data = Memory.readByteArray(this.buf, Math.min(readLen, 8192));
                var bytes = new Uint8Array(data);
                // Look for gRPC response frame
                if (bytes[0] === 0x00) {
                    var msgLen = (bytes[1] << 24) | (bytes[2] << 16) | (bytes[3] << 8) | bytes[4];
                    if (msgLen > 0 && msgLen <= readLen - 5) {
                        if (bytes[5] === 0x0a) {
                            var strLen = bytes[6];
                            if (strLen > 3 && strLen < 100) {
                                var cmdName = "";
                                for (var i = 0; i < strLen && (7+i) < bytes.length; i++) {
                                    cmdName += String.fromCharCode(bytes[7+i]);
                                }
                                if (cmdName.indexOf("CMD_") === 0) {
                                    var hexDump = "";
                                    for (var j = 0; j < Math.min(bytes.length, 4096); j++) {
                                        hexDump += ("0" + bytes[j].toString(16)).slice(-2);
                                    }
                                    send({
                                        type: "grpc_recv",
                                        cmd: cmdName,
                                        len: readLen,
                                        msgLen: msgLen,
                                        hex: hexDump
                                    });
                                }
                            }
                        }
                    }
                }
            } catch(e) {}
        }
    }
});

send({type: "ready", msg: "gRPC traffic hooks installed"});
