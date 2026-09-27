'use strict';
/*
 * agent.js — eFootball (jp.konami.pesam / PES AM) traffic agent
 *
 * Engine : Unreal Engine 4  (libUE4.so, 160 MB)
 * Stack  : gRPC over HTTP/2 over TLS (OpenSSL statically linked inside libUE4.so)
 *          + HTTP/1.1 PHP endpoints (ntl.service.konami.net, info.service.konami.net)
 *
 * Hook point: OpenSSL SSL_write / SSL_read  (public API, static-linked)
 *   offsets verified in IDA against libUE4.so:
 *     SSL_write @ 0x7FFA9B4   (wraps SSL_write_ex)
 *     SSL_read  @ 0x7FFA780   (wraps SSL_read_ex)
 *   prologue (both): ffc300d1 fd7b01a9 f31300f9 fd430091 53d03bd5 681640f9 e80700f9
 *
 * NOTE: the gadget runs in the arm64 realm on MuMu, but Process.findModuleByName()
 * does not see libUE4.so (bridge namespace quirk). Resolve the base from
 * /proc/self/maps instead.
 *
 * send({t:'w'|'r', ssl, n, ts}, <ArrayBuffer>)   t:'w'=SSL_write  t:'r'=SSL_read
 */

var MAX = 1048576;
var LIB = 'libUE4.so';

var OFF_SSL_WRITE = 0x7FFA9B4;
var OFF_SSL_READ  = 0x7FFA780;
var PROLOGUE_HEX  = 'ffc300d1fd7b01a9f31300f9fd43009153d03bd5681640f9e80700f9';

var _armed = false;
var _tries = 0;

function log(s) { console.log('[efb] ' + s); }

function findBaseFromMaps() {
    try {
        var f = new File('/proc/self/maps', 'r');
        var txt = f.readText();
        f.close();
        var lines = txt.split('\n');
        var best = null;
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line.indexOf(LIB) === -1) continue;
            var parts = line.split(' ');
            var addrs = parts[0].split('-');
            var lo = ptr('0x' + addrs[0]);
            if (best === null || lo.compare(best) < 0) best = lo;
        }
        return best;
    } catch (e) {
        return null;
    }
}

function prologueOk(addr) {
    try {
        var u = new Uint8Array(addr.readByteArray(24));
        var out = '';
        for (var i = 0; i < u.length; i++) out += ('0' + u[i].toString(16)).slice(-2);
        return out === PROLOGUE_HEX;
    } catch (e) { return false; }
}

function hookSsl(base) {
    var W = base.add(OFF_SSL_WRITE);
    var R = base.add(OFF_SSL_READ);

    log('SSL_write@' + W + ' prologue_ok=' + prologueOk(W) +
        '  SSL_read@' + R + ' prologue_ok=' + prologueOk(R));

    Interceptor.attach(W, {
        onEnter: function (a) {
            try {
                var n = a[2].toInt32();
                if (n > 0 && n <= MAX) send({ t: 'w', ssl: a[0].toString(), n: n, ts: Date.now() }, a[1].readByteArray(n));
            } catch (e) {}
        }
    });
    Interceptor.attach(R, {
        onEnter: function (a) { this.ssl = a[0].toString(); this.b = a[1]; },
        onLeave: function (r) {
            try {
                var n = r.toInt32();
                if (n > 0 && n <= MAX) send({ t: 'r', ssl: this.ssl, n: n, ts: Date.now() }, this.b.readByteArray(n));
            } catch (e) {}
        }
    });
    log('hooked OK');
}

function hookCriHttp(base) {
    var OFF_CRI_OPEN     = 0x8b0e4b4;
    var OFF_CRI_CONNECT  = 0x8b0e854;
    var OFF_CRI_SET_HDR  = 0x8b0e5c0;
    var OFF_CRI_SET_BODY = 0x8b0e70c;
    var OFF_CRI_STATUS   = 0x8b0ea4c;
    var OFF_CRI_DOWNLOAD = 0x8b0ec14;
    var OFF_CRI_CLOSE    = 0x8b0e964;

    var reqs = {};
    function getReq(tag) {
        if (!reqs[tag]) {
            reqs[tag] = { url: '', headers: [], method: 'GET', ts: Date.now() };
        }
        return reqs[tag];
    }

    try {
        Interceptor.attach(base.add(OFF_CRI_OPEN), {
            onEnter: function (a) {
                var tag = a[1].toString();
                var url = a[2].isNull() ? '' : a[2].readCString();
                log('[criHttp] OPEN ' + tag + ' ' + url);
                var r = getReq(tag);
                r.url = url;
                r.ts = Date.now();
                send({ t: 'http_open', tag: tag, url: url, ts: r.ts });
            }
        });

        Interceptor.attach(base.add(OFF_CRI_CONNECT), {
            onEnter: function (a) {
                var tag = a[1].toString();
                var url = a[2].isNull() ? '' : a[2].readCString();
                if (url) {
                    var r = getReq(tag);
                    r.url = url;
                }
            }
        });

        Interceptor.attach(base.add(OFF_CRI_SET_HDR), {
            onEnter: function (a) {
                var tag = a[1].toString();
                var k = a[2].isNull() ? '' : a[2].readCString();
                var v = a[3].isNull() ? '' : a[3].readCString();
                send({ t: 'http_hdr', tag: tag, key: k, val: v, ts: Date.now() });
            }
        });

        Interceptor.attach(base.add(OFF_CRI_SET_BODY), {
            onEnter: function (a) {
                var tag = a[1].toString();
                var sz = a[3].toInt32();
                if (sz > 0 && sz <= MAX) {
                    send({ t: 'http_body_w', tag: tag, n: sz, ts: Date.now() }, a[2].readByteArray(sz));
                }
            }
        });

        Interceptor.attach(base.add(OFF_CRI_STATUS), {
            onLeave: function (r) {
                var code = r.toInt32();
                send({ t: 'http_status', status: code, ts: Date.now() });
            }
        });

        Interceptor.attach(base.add(OFF_CRI_DOWNLOAD), {
            onEnter: function (a) {
                this.tag = a[1].toString();
                this.buf = a[2];
            },
            onLeave: function (r) {
                var n = r.toInt32();
                if (n > 0 && n <= MAX) {
                    send({ t: 'http_body_r', tag: this.tag, n: n, ts: Date.now() }, this.buf.readByteArray(n));
                }
            }
        });

        Interceptor.attach(base.add(OFF_CRI_CLOSE), {
            onEnter: function (a) {
                var tag = a[1].toString();
                send({ t: 'http_close', tag: tag, ts: Date.now() });
                delete reqs[tag];
            }
        });
        log('CriHttp hooked OK');
    } catch (e) {
        log('CriHttp hook error: ' + e);
    }
}

function arm() {
    if (_armed) return;
    _tries++;
    var base = findBaseFromMaps();
    if (!base) {
        if (_tries <= 60) setTimeout(arm, 500);
        else log('libUE4.so not found after ' + _tries + ' tries');
        return;
    }
    log('libUE4.so base=' + base + ' (try ' + _tries + ')');
    try {
        hookSsl(base);
        hookCriHttp(base);
        _armed = true;
        send({ t: 'ready', ts: Date.now(), base: base.toString() });
    } catch (e) {
        log('hook fail: ' + e);
        setTimeout(arm, 1000);
    }
}

arm();
