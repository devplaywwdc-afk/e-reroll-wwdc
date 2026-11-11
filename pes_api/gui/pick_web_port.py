import socket, sys
for p in range(8080, 8200):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", p)); s.close(); print(p); sys.exit()
    except OSError:
        s.close()
sys.exit(1)
