# TLS certificates

Issued by the existing **cert-manager v1.20.2** through a **namespace-scoped Issuer** in
`renvor-site`. No ClusterIssuer is created or modified: a ClusterIssuer is cluster-wide, and
editing one to suit Renvor would put every other certificate on this shared cluster at risk of
a Renvor mistake.

## Never do this

**Do not read, print, log, copy, or commit the contents of `secret/renvor-dev-tls` or of the
ACME account key.** There is no operational task that requires it. Everything below inspects
status and what the server presents on the wire.

## Check status

```sh
kubectl -n renvor-site get certificate renvor-dev-tls
kubectl -n renvor-site describe certificate renvor-dev-tls | sed -n '/Status/,$p'
kubectl -n renvor-site get certificaterequest,order,challenge
```

From outside, which is the only view that matters to a reader:

```sh
echo | openssl s_client -connect renvor.dev:443 -servername renvor.dev 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates -ext subjectAltName
```

The SAN list must contain **both** `renvor.dev` and `www.renvor.dev`. `www` is redirected, but
the browser completes the TLS handshake before it will read a redirect — a redirect served over
a certificate error is a redirect nobody follows.

## First issuance: staging directory first

`certificate.yaml` ships pointing at `letsencrypt-staging`. That is deliberate. Let's Encrypt's
production limits are measured in **failures per week**, and a misconfigured HTTP-01 path burns
that allowance for the whole domain. The staging directory issues an untrusted certificate
while exercising the identical challenge path, so a mistake costs nothing.

Once a staging certificate reaches `Ready=True`, switch the issuer in a separate reviewed
commit:

```yaml
issuerRef:
  name: letsencrypt-prod   # was letsencrypt-staging
```

then delete the staging-issued Secret so cert-manager reissues:

```sh
kubectl -n renvor-site delete secret renvor-dev-tls
kubectl -n renvor-site get certificate renvor-dev-tls -w
```

## When a challenge fails

HTTP-01 needs plain **HTTP on port 80** to reach Traefik and be answered from
`/.well-known/acme-challenge/`. The single most common cause of failure is an over-broad
HTTP-to-HTTPS redirect swallowing that path.

```sh
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' \
  http://renvor.dev/.well-known/acme-challenge/probe
kubectl -n renvor-site get challenge -o wide
kubectl -n renvor-site describe challenge | sed -n '/Status\|Reason\|Message/p'
```

The HTTP IngressRoute in this repository carries `priority: 1` precisely so cert-manager's
temporary solver route always outranks it. **This matters most at renewal**, roughly 60 days
after issuance, when nobody is watching — a redirect that breaks renewal looks fine for two
months and then takes the site offline.

## Renewal

cert-manager renews automatically at 30 days remaining on a 90-day certificate. That is the
window in which a failure has to be noticed.

```sh
kubectl -n renvor-site get certificate renvor-dev-tls \
  -o jsonpath='{.status.notAfter}{"\n"}{.status.renewalTime}{"\n"}'
```

**There is no alerting.** notification-controller is not installed, so nothing pages anyone if a
renewal fails. Until that changes this is a recurring manual check, and it is recorded as a
known limitation rather than presented as automated.
