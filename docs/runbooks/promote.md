# Promote a Renvor web image

**Nothing here builds anything.** Promotion moves an existing, already-scanned digest from one
overlay to the next. If you find yourself rebuilding, stop — a rebuild produces a different
digest, and production would then run something no staging environment ever saw.

Choose one target and keep its values together throughout the procedure:

| Target | Repository / image | Staging namespace | Production namespace | Flux Kustomizations | Public host |
|---|---|---|---|---|---|
| Landing site | `renvor-rs/renvor-site` | `renvor-site-staging` | `renvor-site` | `renvor-site-staging`, `renvor-site-production` | `renvor.dev` |
| Documentation | `renvor-rs/renvor-docs` | `renvor-docs-staging` | `renvor-docs` | `renvor-docs-staging`, `renvor-docs-production` | `docs.renvor.dev` |

The examples below use shell variables to make an accidental cross-target promotion visible:

```sh
APP=renvor-docs                    # or renvor-site
REPO=renvor-rs/renvor-docs        # or renvor-rs/renvor-site
STAGING_NS=renvor-docs-staging     # or renvor-site-staging
PRODUCTION_NS=renvor-docs          # or renvor-site
HOST=docs.renvor.dev               # or renvor.dev
```

## 1. Find the digest

The selected repository's `publish-image` workflow publishes on a trusted push to `main` and
records the digest in its run summary and in the `release-evidence` artifact.

```sh
gh run list --repo "$REPO" --workflow publish-image --limit 5
gh run view --repo "$REPO" <run-id> --log | grep -E '^digest:|^pinned_reference:'
```

Confirm it independently rather than trusting the log:

```sh
DIGEST=sha256:...
ANON_DOCKER_CONFIG="$(mktemp -d)"
trap 'rm -rf "$ANON_DOCKER_CONFIG"' EXIT
DOCKER_CONFIG="$ANON_DOCKER_CONFIG" docker pull "ghcr.io/$REPO@$DIGEST"
gh attestation verify "oci://ghcr.io/$REPO@$DIGEST" --repo "$REPO"
```

The `gh attestation verify` step checks the Sigstore signature **and** that the provenance names
the selected repository. A digest that pulls but does not verify has not been promoted by this
pipeline, whatever else is true of it.

## 2. Staging

```sh
$EDITOR "apps/$APP/overlays/staging/kustomization.yaml"   # images[0].digest
kubectl kustomize "apps/$APP/overlays/staging" | grep 'image:'
```

The rendered image reference must contain `@sha256:` and **must not** contain a tag. Then open a
pull request, get it reviewed, and merge — Flux reconciles from `main`, so nothing takes effect
until it is merged.

```sh
kubectl -n flux-system get kustomization "$APP-staging" -w
kubectl -n "$STAGING_NS" get pods -o wide
```

## 3. Validate staging

Staging has no Ingress by design. Reach it through a temporary port-forward, which requires
cluster credentials and leaves nothing behind:

```sh
kubectl -n "$STAGING_NS" port-forward "svc/$APP" 18080:80 &
PORT_FORWARD_PID=$!
curl -sSI http://127.0.0.1:18080/ | grep -iE 'content-security-policy|cache-control|x-content-type'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/nope     # expect 404
kill "$PORT_FORWARD_PID"
```

Also confirm, before promoting:

- `kubectl -n "$STAGING_NS" get pods` — Running, `0` restarts;
- `kubectl -n "$STAGING_NS" describe pod` — no warning events;
- the running image ID equals the digest above, not merely the reference:
  `kubectl -n "$STAGING_NS" get pod -o jsonpath='{.items[*].status.containerStatuses[*].imageID}'`.

## 4. Production

Copy **the same digest** into `apps/$APP/overlays/production/kustomization.yaml`. Open a second
pull request; the digest diff should be one line.

```sh
kubectl -n flux-system get kustomization "$APP-production" -w
```

Production `dependsOn` staging, so it will not reconcile while staging is unhealthy. That
ordering is enforced by the controller, not by remembering to do it in order.

For a new hostname, complete the staging-to-production ACME procedure in `certificates.md`
before treating the deployment as public.

## 5. Verify from outside

```sh
curl -sSI "https://$HOST/" | grep -iE 'content-security-policy|cache-control|strict-transport'
curl -s -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' "https://$HOST/"
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' "http://$HOST/"
```

For the landing site, also verify that `https://www.renvor.dev/plan?x=1` returns **301** to
`https://renvor.dev/plan?x=1`, preserving the path and query.

**Record the previous digest before you finish.** It is the rollback target, and looking it up
during an incident is time spent not fixing the incident.
