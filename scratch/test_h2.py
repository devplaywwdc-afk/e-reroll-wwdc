import ssl
import socket
import h2.connection
import h2.events

HOST = "pes22-game.cs.konami.net"
PORT = 443

ctx = ssl.create_default_context()
ctx.set_alpn_protocols(["h2"])

sock = socket.create_connection((HOST, PORT), timeout=10)
tls_sock = ctx.wrap_socket(sock, server_hostname=HOST)
print("Connected! Negotiated protocol:", tls_sock.selected_alpn_protocol())

conn = h2.connection.H2Connection()
conn.initiate_connection()
tls_sock.sendall(conn.data_to_send())

# Read server settings
data = tls_sock.recv(65535)
events = conn.receive_data(data)
for ev in events:
    print("Event:", type(ev).__name__, ev)
tls_sock.close()
print("Test successful!")
