# ADR-0009: joserfc in the mock OIDC issuer

`authlib.jose` is deprecated in favor of `joserfc`, which Authlib already depends on. The mock
issuer and its tests use `joserfc` directly (declared in pyproject). OAuth form bodies are parsed
with `urllib.parse.parse_qs` so `python-multipart` is not added.
