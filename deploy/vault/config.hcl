# OpenBao for the Compose lab profile `vault`: one node, file storage, no TLS (put TLS in front, or
# use your own OpenBao: Forge only needs its HTTP API). It starts SEALED: scripts/vault-setup.sh
# initialises and unseals it the first time; after a restart it must be unsealed again
# (`make vault/unseal`). Keep the unseal keys and the root token apart from the data.
ui            = false
disable_mlock = true
storage "file" {
  path = "/openbao/file"
}
listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}
api_addr = "http://openbao:8200"
