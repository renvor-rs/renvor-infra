# TLS certificates

Renvor's certificates are issued by the existing **cert-manager v1.20.2** through
**namespace-scoped Issuers**. No ClusterIssuer is created or modified: a ClusterIssuer is
cluster-wide, and editing one to suit Renvor would put every other certificate on this shared
cluster at risk of a Renvor mistake.

| Target | Namespace | Certificate / Secret | Required SANs |
|---|---|---|---|
| Landing site | `renvor-site` | `renvor-dev-tls` | `renvor.dev`, `www.renvor.dev` |
| Documentation | `renvor-docs` | `docs-renvor-dev-tls` | `docs.renvor.dev` |

Choose a target before running commands:

```sh
NAMESPACE=renvor-docs              # or renvor-site
CERTIFICATE=docs-renvor-dev-tls     # or renvor-dev-tls
HOST=docs.renvor.dev                # or renvor.dev
```

## Never do this

**Do not read, print, log, copy, or commit the contents of the TLS Secret or of an ACME account
key.** There is no operational task that requires it. Everything below inspects status and what
the server presents on the wire.

## Check status

```sh
kubectl -n "$NAMESPACE" get certificate "$CERTIFICATE"
kubectl -n "$NAMESPACE" describe certificate "$CERTIFICATE" | sed -n '/Status/,$p'
kubectl -n "$NAMESPACE" get certificaterequest,order,challenge
```

From outside, which is the only view that matters to a reader:

```sh
echo | openssl s_client -connect "$HOST:443" -servername "$HOST" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
```

The SAN list must contain every hostname in the table. `www.renvor.dev` redirects, but a browser
completes the TLS handshake before it will read a redirect — a redirect served over a certificate
error is a redirect nobody follows.

## First issuance: staging directory first

A new Certificate ships pointing at `letsencrypt-staging`. That is deliberate. Let's Encrypt's
production limits are measured in **failures per week**, and a misconfigured HTTP-01 path burns
that allowance for the whole domain. The staging directory issues an untrusted certificate while
exercising the identical challenge path, so a mistake costs nothing.

Once the staging certificate reaches `Ready=True`, switch the issuer in a separate reviewed
commit:

```yaml
issuerRef:
  name: letsencrypt-prod   # was letsencrypt-staging
```

After that commit is merged and reconciled, cert-manager should create a new CertificateRequest
for `letsencrypt-prod` while continuing to serve the staging-issued Secret. Watch the request and
Certificate until the production issuance replaces it:

```sh
kubectl -n "$NAMESPACE" get certificaterequest,order,challenge -w
kubectl -n "$NAMESPACE" get certificate "$CERTIFICATE" -w
```

Do not delete a healthy, currently served Secret merely to trigger reissuance; that creates an
avoidable TLS gap. If changing `issuerRef` does not create a production CertificateRequest, stop
and inspect the Certificate events rather than destroying the last working certificate.

The landing-site and documentation certificates have each completed this promotion independently.
A newly introduced hostname must do the same; one hostname's successful issuance proves nothing
about another hostname's DNS and challenge route.

## When a challenge fails

HTTP-01 needs plain **HTTP on port 80** to reach Traefik and be answered from
`/.well-known/acme-challenge/`. The single most common cause of failure is an over-broad
HTTP-to-HTTPS redirect swallowing that path.

```sh
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  "http://$HOST/.well-known/acme-challenge/probe"
kubectl -n "$NAMESPACE" get challenge -o wide
kubectl -n "$NAMESPACE" describe challenge | sed -n '/Status\|Reason\|Message/p'
```

Each HTTP IngressRoute carries `priority: 1` precisely so cert-manager's temporary solver route
always outranks it. **This matters most at renewal**, roughly 60 days after issuance, when nobody
is watching — a redirect that breaks renewal looks fine for two months and then takes the site
offline.

## Renewal

cert-manager renews automatically at 30 days remaining on a 90-day certificate. That is the
window in which a failure has to be noticed.

```sh
kubectl -n "$NAMESPACE" get certificate "$CERTIFICATE" \
  -o jsonpath='{.status.notAfter}{"\n"}{.status.renewalTime}{"\n"}'
```

**There is no alerting.** notification-controller is not installed, so nothing pages anyone if a
renewal fails. Until that changes this is a recurring manual check, and it is recorded as a known
limitation rather than presented as automated.
