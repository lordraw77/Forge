# What Forge's own token or AppRole needs on the Transit key (and nothing else).
path "transit/encrypt/forge" { capabilities = ["update"] }
path "transit/decrypt/forge" { capabilities = ["update"] }
path "transit/keys/forge"    { capabilities = ["read"] }
