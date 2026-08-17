#!/usr/bin/env python3
"""Prove the namespace-scoped reconciliation boundary holds.

Run from the repository root, passing the rendered *application overlays* — and only those:

    kubectl kustomize apps/renvor-site/overlays/staging    > rendered/staging.yaml
    kubectl kustomize apps/renvor-site/overlays/production > rendered/production.yaml
    python3 scripts/check-tenancy.py rendered/staging.yaml rendered/production.yaml

The paths are arguments rather than a glob on purpose. `rendered/` also holds the rendered
*control plane* — GitRepository and Kustomization objects — which are hand-applied and are
legitimately the very kinds this script forbids inside a reconciled overlay. Globbing would
either flag them wrongly or force an exclusion rule that a future filename could slip past.

WHAT THIS IS FOR
----------------
Upstream Flux binds `kustomize-controller` to cluster-admin. Renvor reconciles a **public**
repository onto a cluster shared with unrelated production workloads, so repository-driven
applies run as `flux-system/renvor-reconciler` instead — an identity whose whole authority is
two namespaced Roles.

That boundary is made of several separate facts, each of which can be broken independently and
none of which announces itself when it breaks. The worst of them fails *open*: drop
`serviceAccountName` from a Kustomization and reconciliation keeps working, silently, as
cluster-admin. This script asserts every one of those facts.

Every check either finds something and judges it, or fails because it found nothing. A check
that matches no input is a bug, not a pass.
"""
from __future__ import annotations

import collections
import pathlib
import re
import subprocess
import sys

import yaml

RECONCILER = "renvor-reconciler"
FLUX_NS = "flux-system"
RENVOR_NAMESPACES = {"renvor-site", "renvor-site-staging"}

TENANCY = "clusters/hostinger/flux-system/renvor-tenancy.yaml"
BOOTSTRAP_KUSTOMIZATION = "clusters/hostinger/flux-system/kustomization.yaml"
GITREPOSITORY = "clusters/hostinger/gitrepository.yaml"
FLUX_KUSTOMIZATIONS = ["clusters/hostinger/staging.yaml", "clusters/hostinger/production.yaml"]
OVERLAY_DIRS = ["apps/renvor-site/overlays/staging", "apps/renvor-site/overlays/production"]

# Kind -> (apiGroup, RBAC resource name). Deriving the plural by string mangling would guess
# wrong on the first irregular kind it met, so the mapping is explicit and an unknown kind is a
# hard failure: a new resource type must be granted deliberately, never inferred.
KIND_TO_RULE = {
    "Service": ("", "services"),
    "ServiceAccount": ("", "serviceaccounts"),
    "LimitRange": ("", "limitranges"),
    "ResourceQuota": ("", "resourcequotas"),
    "ConfigMap": ("", "configmaps"),
    "Deployment": ("apps", "deployments"),
    "NetworkPolicy": ("networking.k8s.io", "networkpolicies"),
    "Certificate": ("cert-manager.io", "certificates"),
    "Issuer": ("cert-manager.io", "issuers"),
    "IngressRoute": ("traefik.io", "ingressroutes"),
    "Middleware": ("traefik.io", "middlewares"),
}

# Reconciliation must be able to create, read, update, and — because `prune: true` is set —
# delete the resources it manages. Nothing beyond that.
MANAGED_VERBS = {"get", "list", "watch", "create", "update", "patch", "delete"}

# Read-only verbs, for resources observed but never applied.
READ_VERBS = {"get", "list", "watch"}

# Resources the reconciler must be able to READ but must never write.
#
# These are not in any manifest, which is exactly why they need naming here: derived-from-the-
# overlays checking is structurally blind to them, and their absence does not fail until the
# live cluster refuses a health check.
#
# `wait: true` runs kstatus through the *impersonated* client. kstatus's Deployment reader
# resolves the generated ReplicaSets and then their Pods with a namespaced LIST, and propagates
# a `forbidden` as an error rather than degrading — so without these the Kustomization never
# reports Ready and production never reconciles. Write verbs are deliberately NOT granted: the
# reconciler observes Pods, it does not create them.
HEALTH_CHECK_READS: dict[tuple[str, str], set[str]] = {
    ("apps", "replicasets"): READ_VERBS,
    ("", "pods"): READ_VERBS,
}

# Kinds that must never appear in a reconciled overlay. Cluster-scoped objects escape the
# namespace boundary; RBAC objects would let reconciliation widen its own authority; Flux
# control objects would let it rewrite its own constraints.
FORBIDDEN_IN_OVERLAYS = {
    "Namespace", "Node", "PersistentVolume", "StorageClass",
    "ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding",
    "CustomResourceDefinition", "ClusterIssuer",
    "ValidatingWebhookConfiguration", "MutatingWebhookConfiguration",
    "GitRepository", "Kustomization", "HelmRelease", "OCIRepository",
    "Secret",
}

# Resources the reconciler must never be granted, whatever a future manifest asks for. Reading
# Secrets is the one that matters most: it is how a compromised commit would exfiltrate the TLS
# private key and any other credential in the namespace.
NEVER_GRANT = {
    "secrets", "namespaces", "nodes", "customresourcedefinitions",
    "clusterroles", "clusterrolebindings", "roles", "rolebindings",
    "persistentvolumes", "storageclasses", "clusterissuers",
}

REQUIRED_CONTROLLER_ARGS = {
    "--default-service-account=renvor-reconciler",
    "--no-cross-namespace-refs=true",
    "--no-remote-bases=true",
}

# The only hostnames Renvor may route.
#
# THIS IS THE ONE PLACE A FULLY RBAC-COMPLIANT COMMIT CAN REACH ANOTHER TENANT. Traefik matches
# routes cluster-globally by rule and priority; it does not care which namespace an IngressRoute
# lives in. So an IngressRoute inside `renvor-site` — entirely within this reconciler's granted
# authority — can claim `Host(`gitlab.example.com`)`, or a bare `PathPrefix(`/`)` catch-all, at a
# priority high enough to outrank the real owner and take its traffic.
#
# Namespaced RBAC cannot express that constraint, because the escape is not an API-server
# authorisation question at all. It has to be asserted here.
ALLOWED_ROUTE_HOSTS = {"renvor.dev", "www.renvor.dev"}

# `Host(...)` extraction. Traefik accepts backticks, single and double quotes.
HOST_CALL = re.compile(r"Host\(\s*([`'\"])([^`'\"]+)\1\s*\)")
# Matchers that select on something other than an exact host are refused outright: a rule with
# no Host() term matches on path alone and is therefore global.
FORBIDDEN_MATCHERS = ("HostRegexp", "HostSNIRegexp")

# NetworkPolicies the overlays are allowed to declare. Policies are ADDITIVE — a second policy
# cannot tighten the first, only widen the union — so `egress: []` in the default-deny is a
# guarantee only while no other policy grants egress. An unrecognised policy name means a new
# one arrived, and it must be reviewed rather than assumed harmless.
ALLOWED_NETWORKPOLICIES = {"renvor-site-default-deny", "renvor-site-allow-traefik"}

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global checks
    checks += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)
    return ok


def load(path: str) -> list[dict]:
    p = pathlib.Path(path)
    if not p.is_file():
        failures.append(f"missing file: {path}")
        print(f"  [FAIL] {path} does not exist")
        return []
    return [d for d in yaml.safe_load_all(p.read_text()) if isinstance(d, dict)]


# ---------------------------------------------------------------- 1. the overlays
print("== overlays contain only namespaced application resources ==")
rendered = sys.argv[1:]
if not rendered:
    sys.exit("usage: check-tenancy.py <rendered-overlay.yaml> [more...]  (see module docstring)")
check("rendered overlays were supplied", len(rendered) >= 2, f"{len(rendered)} files")
missing_render = [p for p in rendered if not pathlib.Path(p).is_file()]
check("every supplied overlay render exists", not missing_render, str(missing_render))

required_rules: set[tuple[str, str]] = set()
overlay_objects = 0
def parse(path: str) -> list[dict]:
    """Parse a render, turning a malformed file into a reported failure rather than a crash."""
    try:
        return [d for d in yaml.safe_load_all(pathlib.Path(path).read_text())
                if isinstance(d, dict)]
    except yaml.YAMLError as exc:
        check(f"{path} parses as YAML", False, f"{type(exc).__name__}")
        return []


for path in rendered:
    docs = parse(path)
    check(f"{path} is non-empty", len(docs) > 0, f"{len(docs)} objects")
    overlay_objects += len(docs)
    bad = sorted({d["kind"] for d in docs if d.get("kind") in FORBIDDEN_IN_OVERLAYS})
    check(f"{path} declares no forbidden kind", not bad, str(bad) if bad else "clean")
    unknown = sorted({d["kind"] for d in docs if d.get("kind") not in KIND_TO_RULE})
    check(f"{path} uses only mapped kinds", not unknown,
          f"unmapped: {unknown}" if unknown else "all mapped")
    for d in docs:
        if d.get("kind") in KIND_TO_RULE:
            required_rules.add(KIND_TO_RULE[d["kind"]])

check("the overlays actually declare resources", overlay_objects > 0, f"{overlay_objects} objects")
print(f"     derived requirement: {len(required_rules)} (apiGroup, resource) pairs")

# --------------------------------------------- 1b. the shared ingress is the real escape hatch
print("\n== no route claims a hostname that is not Renvor's ==")
routes_seen = 0
policies_seen = 0
for path in rendered:
    for d in parse(path):
        if d.get("kind") == "IngressRoute":
            for route in d["spec"].get("routes", []):
                routes_seen += 1
                match = route.get("match", "")
                where = f"{d['metadata']['name']} p={route.get('priority')}"
                hosts = {m.group(2) for m in HOST_CALL.finditer(match)}
                check(f"{where}: the rule selects on an explicit Host()", bool(hosts),
                      match[:70] if not hosts else f"{sorted(hosts)}")
                foreign = hosts - ALLOWED_ROUTE_HOSTS
                check(f"{where}: every Host() is a Renvor hostname", not foreign,
                      f"foreign {sorted(foreign)}" if foreign else "clean")
                bad_matcher = [m for m in FORBIDDEN_MATCHERS if m in match]
                check(f"{where}: uses no wildcard host matcher", not bad_matcher, str(bad_matcher))

        if d.get("kind") == "NetworkPolicy":
            policies_seen += 1
            name = d["metadata"]["name"]
            spec = d.get("spec", {})
            check(f"NetworkPolicy/{name} is one of the reviewed policies",
                  name in ALLOWED_NETWORKPOLICIES, name)
            # An empty rule object means "allow everything", not "allow nothing": the same
            # shape that denies traffic when the list is absent permits all traffic when the
            # list holds one empty element.
            egress = spec.get("egress") or []
            check(f"NetworkPolicy/{name} opens no egress", not egress, str(egress) or "none")

check("at least one IngressRoute rule was inspected", routes_seen > 0, f"{routes_seen} rules")
check("at least one NetworkPolicy was inspected", policies_seen > 0, f"{policies_seen} policies")

# ---------------------------------------------------------------- 2. the Roles
print("\n== the reconciler's authority matches that requirement exactly ==")
tenancy = load(TENANCY)

roles = [d for d in tenancy if d.get("kind") == "Role" and d["metadata"]["name"] == RECONCILER]
check("a Role exists in each Renvor namespace", len(roles) == 2, f"{len(roles)} Roles")
check("the Roles are in the Renvor namespaces only",
      {r["metadata"]["namespace"] for r in roles} == RENVOR_NAMESPACES,
      str(sorted(r["metadata"]["namespace"] for r in roles)))

# The full expectation: what the overlays apply (managed, full verbs) plus what the health
# checks read (read-only). Anything outside this is surplus; anything inside it and absent is a
# gap that will fail on the live cluster.
expected: dict[tuple[str, str], set[str]] = {r: MANAGED_VERBS for r in required_rules}
for pair, verbs in HEALTH_CHECK_READS.items():
    if pair in expected:
        # An applied resource that is also observed keeps its full verb set.
        continue
    expected[pair] = verbs

for role in roles:
    ns = role["metadata"]["namespace"]
    granted: set[tuple[str, str]] = set()
    for rule in role.get("rules", []):
        for group in rule.get("apiGroups", []):
            for res in rule.get("resources", []):
                pair = (group, res)
                granted.add(pair)
                verbs = set(rule.get("verbs", []))
                want = expected.get(pair)
                label = f"{group or 'core'}/{res}"
                if want is None:
                    # Surplus — reported by the set comparison below; no verb judgement to make.
                    pass
                else:
                    kind = "read-only" if want == READ_VERBS else "managed"
                    check(f"{ns}: {label} grants exactly its {kind} verb set",
                          verbs == want,
                          f"extra {sorted(verbs - want)} missing {sorted(want - verbs)}"
                          if verbs != want else "exact")
                check(f"{ns}: {res} is not a forbidden resource", res not in NEVER_GRANT, res)
                check(f"{ns}: {label} uses no wildcard", "*" not in (group, res), label)

    missing = set(expected) - granted
    surplus = granted - set(expected)
    check(f"{ns}: every resource the overlays create is granted",
          not (missing & required_rules),
          f"missing {sorted(missing & required_rules)}" if (missing & required_rules) else "complete")
    check(f"{ns}: the health-check reads are granted",
          not (missing & set(HEALTH_CHECK_READS)),
          f"missing {sorted(missing & set(HEALTH_CHECK_READS))}"
          if (missing & set(HEALTH_CHECK_READS)) else "complete")
    check(f"{ns}: nothing is granted beyond the overlays and the health checks", not surplus,
          f"surplus {sorted(surplus)}" if surplus else "minimal")

# ---------------------------------------------------------------- 3. the bindings
print("\n== the bindings reach only this identity, only in these namespaces ==")
check("no ClusterRole is defined for the reconciler",
      not [d for d in tenancy if d.get("kind") == "ClusterRole"], "none")
check("no ClusterRoleBinding is defined for the reconciler",
      not [d for d in tenancy if d.get("kind") == "ClusterRoleBinding"], "none")

bindings = [d for d in tenancy if d.get("kind") == "RoleBinding"]
check("a RoleBinding exists in each Renvor namespace", len(bindings) == 2, f"{len(bindings)}")
for b in bindings:
    ns = b["metadata"]["namespace"]
    check(f"{ns}: RoleBinding is namespaced to a Renvor namespace", ns in RENVOR_NAMESPACES, ns)
    check(f"{ns}: roleRef is a Role, not a ClusterRole", b["roleRef"]["kind"] == "Role",
          b["roleRef"]["kind"])
    subs = b.get("subjects", [])
    check(f"{ns}: exactly one subject", len(subs) == 1, str(len(subs)))
    check(f"{ns}: the subject is flux-system/renvor-reconciler",
          all(s["kind"] == "ServiceAccount" and s["name"] == RECONCILER
              and s["namespace"] == FLUX_NS for s in subs),
          str([f"{s.get('namespace')}/{s.get('name')}" for s in subs]))

sas = [d for d in tenancy if d.get("kind") == "ServiceAccount" and d["metadata"]["name"] == RECONCILER]
check("the ServiceAccount is declared in flux-system", len(sas) == 1 and
      sas[0]["metadata"]["namespace"] == FLUX_NS,
      sas[0]["metadata"]["namespace"] if sas else "absent")

# ---------------------------------------------------------------- 4. PSA
print("\n== both Renvor namespaces enforce Pod Security 'restricted' ==")
namespaces = [d for d in tenancy if d.get("kind") == "Namespace"]
check("both namespaces are declared in the bootstrap",
      {n["metadata"]["name"] for n in namespaces} == RENVOR_NAMESPACES,
      str(sorted(n["metadata"]["name"] for n in namespaces)))
for n in namespaces:
    labels = n["metadata"].get("labels", {})
    for mode in ("enforce", "audit", "warn"):
        check(f"{n['metadata']['name']}: pod-security {mode}=restricted",
              labels.get(f"pod-security.kubernetes.io/{mode}") == "restricted",
              str(labels.get(f"pod-security.kubernetes.io/{mode}")))

# ---------------------------------------------------------------- 5. the Kustomizations
print("\n== every Kustomization names the restricted identity ==")
for path in FLUX_KUSTOMIZATIONS:
    for d in load(path):
        if d.get("kind") != "Kustomization":
            continue
        name = d["metadata"]["name"]
        spec = d["spec"]
        check(f"{name}: sets serviceAccountName", "serviceAccountName" in spec,
              str(spec.get("serviceAccountName")))
        check(f"{name}: serviceAccountName is the reconciler",
              spec.get("serviceAccountName") == RECONCILER, str(spec.get("serviceAccountName")))
        check(f"{name}: targetNamespace is a Renvor namespace",
              spec.get("targetNamespace") in RENVOR_NAMESPACES, str(spec.get("targetNamespace")))
        check(f"{name}: reconciles a path under apps/", str(spec.get("path", "")).startswith("./apps/"),
              str(spec.get("path")))
        check(f"{name}: prune is enabled", spec.get("prune") is True, str(spec.get("prune")))

# ---------------------------------------------------------------- 6. controller flags
print("\n== the controller cannot fall back to cluster-admin ==")
#
# THIS IS CHECKED AGAINST THE BUILT OUTPUT, NOT THE SOURCE TEXT.
#
# An earlier version asked `arg in bootstrap_kustomization_text`. That passes on a tree where
# the `patches:` block has been deleted and the flags survive only inside a comment — the
# rendered controller then has none of them, and the single most important one fails *open*
# straight back to cluster-admin. A substring search cannot distinguish a live patch from a
# sentence about a patch.
#
# So the bootstrap is built and the container's actual `args` array is read. If kustomize is
# unavailable the check FAILS rather than degrading to the text search: an unverifiable
# boundary is not a verified one.
bootstrap_args: list[str] = []
build_ok = False
try:
    built = subprocess.run(
        ["kubectl", "kustomize", str(pathlib.Path(BOOTSTRAP_KUSTOMIZATION).parent)],
        capture_output=True, text=True, timeout=120,
    )
    build_ok = built.returncode == 0
    if build_ok:
        for d in yaml.safe_load_all(built.stdout):
            if isinstance(d, dict) and d.get("kind") == "Deployment" \
               and d["metadata"]["name"] == "kustomize-controller":
                bootstrap_args = d["spec"]["template"]["spec"]["containers"][0].get("args", [])
except (OSError, subprocess.SubprocessError, yaml.YAMLError) as exc:
    print(f"  (bootstrap build failed: {exc})")

check("the bootstrap kustomization builds", build_ok,
      "" if build_ok else (built.stderr[-200:] if 'built' in dir() else "kustomize unavailable"))
check("the built bootstrap contains kustomize-controller", bool(bootstrap_args),
      f"{len(bootstrap_args)} args")
for arg in sorted(REQUIRED_CONTROLLER_ARGS):
    check(f"the BUILT kustomize-controller carries {arg}", arg in bootstrap_args)

# ---------------------------------------------------------------- 7. the source artifact
print("\n== the reconciled artifact cannot contain the control plane ==")
gitrepos = [d for d in load(GITREPOSITORY) if d.get("kind") == "GitRepository"]
check("exactly one GitRepository is declared", len(gitrepos) == 1, str(len(gitrepos)))
for g in gitrepos:
    ignore = g["spec"].get("ignore", "")
    included = [ln.strip()[1:] for ln in ignore.splitlines()
                if ln.strip().startswith("!")]
    check("the ignore rule is an allow-list", "/*" in [ln.strip() for ln in ignore.splitlines()],
          "excludes everything first")
    check("only /apps is re-included", included == ["/apps"], str(included))
    check("/clusters is not re-included", "/clusters" not in included,
          "the control plane is absent from the fetched bytes")
    check("no credential is referenced", "secretRef" not in g["spec"],
          "anonymous public read")

# ---------------------------------------------------------------- verdict
print(f"\n=== {checks} checks, {len(failures)} failed ===")
if not checks:
    sys.exit("no checks ran; this script would have passed vacuously")
if failures:
    for f in failures:
        print(f"::error::{f}")
    sys.exit(f"{len(failures)} tenancy check(s) failed")
print("the namespace-scoped reconciliation boundary holds")
