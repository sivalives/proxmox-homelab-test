## Manual action : Create these certs for Nginx Proxy Manager and upload in Certs (one-time)
# Install mkcert (one-time)
sudo apt update && sudo apt install -y libnss3-tools
curl -JLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
chmod +x mkcert-v*-linux-amd64
sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert

# Create and install local CA
mkcert -install

# Generate cert
mkcert vaultwarden.lan