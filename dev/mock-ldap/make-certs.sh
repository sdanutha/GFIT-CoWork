#!/bin/sh
# Make a throwaway CA and a server certificate for the mock LDAP (localhost).
# Output goes to dev/mock-ldap/certs/, which git ignores.
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)/certs
mkdir -p "$DIR"
cd "$DIR"

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout ca.key -out ca.crt -subj "/CN=GFIT-CoWork mock LDAP CA"

openssl req -newkey rsa:2048 -nodes \
  -keyout server.key -out server.csr -subj "/CN=localhost"

printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\n' > san.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 825 -out server.crt -extfile san.ext

rm -f server.csr san.ext ca.srl
chmod 644 ./*.crt ./*.key
echo "certificates written to $DIR"
