# Security model

GoldFlow assumes broker credentials can directly affect capital and treats them as high-impact
secrets.

- Secrets are read from environment/secret stores into `SecretStr` fields, excluded from repr, and
  recursively redacted from structured logs. Sentry request headers/body/cookies are stripped.
- JWT validation pins algorithm, issuer, audience, expiry, and required claims. Admin actions are
  role-gated and audited.
- Configuration models are immutable after startup. Runtime mode can narrow but cannot weaken the
  live environment interlock.
- Inputs are Pydantic-validated and bounded. There is no arbitrary Python, shell, SQL, file, or MT5
  payload execution endpoint.
- CORS forbids wildcard origins; security/no-cache headers and rate limiting are installed. Public
  deployments still require TLS, firewalling, and a distributed rate-limiting gateway.
- Broker access is serialized. Durable idempotency, broker correlation, and reconciliation defend
  against network retries and process restarts.
- Containers run non-root/read-only with no-new-privileges. Database/Redis are private dependencies.

Run dependency vulnerability and secret scanning in the organization CI/CD policy. Rotate a secret
after any suspected exposure, inspect audit/log access, and verify broker history before restoring
execution.
