#!/usr/bin/env bash
set -euo pipefail

cd /home/drews/discord-watcher

sudo apt-get update
sudo apt-get install -y python3-pip python3-venv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

sudo cp discord-watcher.service /etc/systemd/system/
sudo cp voice-watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable discord-watcher
sudo systemctl enable voice-watcher
sudo systemctl restart discord-watcher
sudo systemctl restart voice-watcher
sudo systemctl status discord-watcher --no-pager
sudo systemctl status voice-watcher --no-pager
