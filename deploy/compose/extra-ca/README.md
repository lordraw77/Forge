# Extra trusted CA certificates

Put the PEM file of your corporate root CA here (for example when an egress proxy inspects
TLS) and set `FORGE_EXTRA_CA_FILE=/etc/forge/extra-ca/<file>.pem` in `.env`. Forge appends it
to the default trust store for every outbound call (model providers, S3, model downloads).

Certificates placed here are not secrets, but `*.pem` files are git-ignored so that local
deployments do not commit them.
