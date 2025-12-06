{ pkgs }: {
  deps = [
    pkgs.python38Full
    pkgs.python38Packages.pip
    pkgs.python38Packages.flask
    pkgs.python38Packages.flask-socketio
    pkgs.python38Packages.requests
    pkgs.python38Packages.websockets
  ];
}
