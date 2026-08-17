# Promote a new landing image

**Nothing here builds anything.** Promotion moves an existing, already-scanned digest from one
overlay to the next. If you find yourself rebuilding, stop — a rebuild produces a different
digest, and production would then run something no staging environment ever saw.

## 1. Find the digest

The `publish-image` workflow in `renvor-rs/renvor-site` publishes on a trusted push to `main`
and records the digest in its run summary and in the `release-evidence` artifact.

```sh
gh run list  --repo renvor-rs/renvor-site --workflow publish-image --limit 5
gh run view  --repo renvor-rs/renvor-site <run-id> --log | grep -E '^digest:|^pinned_reference:'
```

Confirm it independently rather than trusting the log:

```sh
DIGEST=sha256:...
docker logout ghcr.io                                    # prove it is publicly pullable
docker pull ghcr.io/renvor-rs/renvor-site@"$DIGEST"
gh attestation verify "oci://ghcr.io/renvor-rs/renvor-site@$DIGEST" --repo renvor-rs/renvor-site
```

The `gh attestation verify` step checks the Sigstore signature **and** that the provenance
names this repository. A digest that pulls but does not verify has not been promoted by our
pipeline, whatever else is true of it.

## 2. Staging

```sh
$EDITOR apps/renvor-site/overlays/staging/kustomization.yaml   # images[0].digest
kubectl kustomize apps/renvor-site/overlays/staging | grep 'image:'
```

The rendered image reference must contain `@sha256:` and **must not** contain a tag. Then open
a pull request, get it reviewed, and merge — Flux reconciles from `main`, so nothing takes
effect until it is merged.

```sh
kubectl -n flux-system get kustomization renvor-site-staging -w
kubectl -n renvor-site-staging get pods -o wide
```

## 3. Validate staging

Staging has no Ingress by design. Reach it through a temporary port-forward, which requires
cluster credentials and leaves nothing behind:

```sh
kubectl -n renvor-site-staging port-forward svc/renvor-site 18080:80 &
curl -sSI http://127.0.0.1:18080/           | grep -iE 'content-security-policy|cache-control|x-content-type'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/nope     # expect 404
kill %1
```

Also confirm, before promoting:

- `kubectl -n renvor-site-staging get pods` — Running, `0` restarts;
- `kubectl -n renvor-site-staging describe pod` — no warning events;
- the running image ID equals the digest above, not merely the reference:
  `kubectl -n renvor-site-staging get pod -o jsonpath='{.items[*].status.containerStatuses[*].imageID}'`

## 4. Production

Copy **the same digest** into `apps/renvor-site/overlays/production/kustomization.yaml`. Open a
second pull request; the diff should be one line.

```sh
kubectl -n flux-system get kustomization renvor-site-production -w
```

Production `dependsOn` staging, so it will not reconcile while staging is unhealthy. That
ordering is enforced by the controller, not by remembering to do it in order.

## 5. Verify from outside

```sh
curl -sSI https://renvor.dev/ | grep -iE 'content-security-policy|cache-control|strict-transport'
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' 'https://www.renvor.dev/plan?x=1'
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' http://renvor.dev/
```

Expect: HTTP 200 with the policy on the apex, **301** to `https://renvor.dev/plan?x=1` from
`www` with path and query intact, and a redirect to HTTPS on plain HTTP.

**Record the previous digest before you finish.** It is the rollback target, and looking it up
during an incident is time spent not fixing the incident.
