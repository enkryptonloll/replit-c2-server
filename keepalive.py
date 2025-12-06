# keepalive.py
import requests
import time
import threading

def ping_server():
    """Ping the server to keep it awake"""
    while True:
        try:
            # Replace with your Replit URL
            url = f"https://{os.environ['REPL_SLUG']}.{os.environ['REPL_OWNER']}.repl.co/keepalive"
            response = requests.get(url, timeout=10)
            print(f"[{time.ctime()}] Ping: {response.status_code}")
        except Exception as e:
            print(f"[{time.ctime()}] Error: {e}")
        
        # Ping every 4 minutes (Replit sleeps after 5)
        time.sleep(240)

# Run in background
thread = threading.Thread(target=ping_server, daemon=True)
thread.start()

# Also run a simple HTTP server on another port
from http.server import HTTPServer, BaseHTTPRequestHandler

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    
    def log_message(self, format, *args):
        pass  # Disable logging

# Start on port 8081
server = HTTPServer(('0.0.0.0', 8081), KeepAliveHandler)
server.serve_forever()
