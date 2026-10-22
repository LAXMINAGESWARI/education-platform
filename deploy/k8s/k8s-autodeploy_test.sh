#!/usr/bin/env bash
# Temporary git repo plus stub docker, helm, and sudo.
# Proves a dirty clone is refused, and a clean clone is detached at origin/main
# with the image tag equal to HEAD.
set -euo pipefail

root="$(CDPATH='' cd -- "$(dirname "$0")/../.." && pwd)"
script="${root}/deploy/k8s/k8s-autodeploy.sh"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

mkdir -p "${tmp}/bin" "${tmp}/state"

cat > "${tmp}/bin/docker" << 'EOF'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "${STUB_LOG}"
exit 0
EOF

cat > "${tmp}/bin/helm" << 'EOF'
#!/usr/bin/env bash
printf 'helm %s\n' "$*" >> "${STUB_LOG}"
if [[ "${HELM_FAIL:-0}" == 1 && "$1" == "upgrade" ]]; then
  echo "helm upgrade failed" >&2
  exit 1
fi
exit 0
EOF

cat > "${tmp}/bin/sudo" << 'EOF'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >> "${STUB_LOG}"
exit 0
EOF

chmod +x "${tmp}/bin/docker" "${tmp}/bin/helm" "${tmp}/bin/sudo"

git_identity() {
  git -C "$1" config user.email "autodeploy-test@example.com"
  git -C "$1" config user.name "autodeploy-test"
}

make_remote() {
  local remote="$1"
  git init -q -b main "${remote}"
  git_identity "${remote}"
  printf 'one\n' > "${remote}/README"
  git -C "${remote}" add README
  git -C "${remote}" commit -q -m "one"
}

advance_remote() {
  local remote="$1"
  printf 'two\n' >> "${remote}/README"
  git -C "${remote}" add README
  git -C "${remote}" commit -q -m "two"
}

run_deploy() {
  local clone="$1"
  shift
  local code=0
  : > "${tmp}/log"
  env \
    PATH="${tmp}/bin:${PATH}" \
    CLONE="${clone}" \
    STATE_FILE="${tmp}/state/sha" \
    LOCK_FILE="${tmp}/state/lock" \
    STUB_LOG="${tmp}/log" \
    "$@" \
    "${script}" > "${tmp}/out" 2>&1 || code=$?
  printf '%s' "${code}"
}

failures=0
fail() {
  echo "FAIL $*"
  failures=$((failures + 1))
}

# Tracked change. checkout --force must not run, and the edit must remain.
make_remote "${tmp}/remote-dirty"
git clone -q "${tmp}/remote-dirty" "${tmp}/clone-dirty"
printf 'local\n' >> "${tmp}/clone-dirty/README"
before="$(git -C "${tmp}/clone-dirty" rev-parse HEAD)"
dirty_code="$(run_deploy "${tmp}/clone-dirty")"
if [[ "${dirty_code}" -ne 1 ]]; then
  fail "dirty tree exit ${dirty_code}, expected 1"
  cat "${tmp}/out"
fi
if ! grep -q 'refusing to deploy' "${tmp}/out"; then
  fail "dirty tree did not say it was refusing"
  cat "${tmp}/out"
fi
if grep -q '^docker ' "${tmp}/log"; then
  fail "dirty tree still built an image"
  cat "${tmp}/log"
fi
if [[ "$(git -C "${tmp}/clone-dirty" rev-parse HEAD)" != "${before}" ]]; then
  fail "dirty tree moved HEAD"
fi
if ! git -C "${tmp}/clone-dirty" symbolic-ref -q HEAD >/dev/null; then
  fail "dirty tree was detached"
fi
if ! grep -q 'local' "${tmp}/clone-dirty/README"; then
  fail "dirty tree edit was discarded"
fi

# Untracked file is relevant. The script owns no path inside the clone.
make_remote "${tmp}/remote-untracked"
git clone -q "${tmp}/remote-untracked" "${tmp}/clone-untracked"
printf 'note\n' > "${tmp}/clone-untracked/notes.txt"
untracked_code="$(run_deploy "${tmp}/clone-untracked")"
if [[ "${untracked_code}" -ne 1 ]]; then
  fail "untracked file exit ${untracked_code}, expected 1"
  cat "${tmp}/out"
fi
if ! grep -q 'notes.txt' "${tmp}/out"; then
  fail "untracked file was not reported"
  cat "${tmp}/out"
fi

# Stale clone: HEAD is the parent of origin/main, so the build must use
# origin/main's SHA, not the commit that was checked out before fetch.
make_remote "${tmp}/remote-clean"
git clone -q "${tmp}/remote-clean" "${tmp}/clone-clean"
stale_head="$(git -C "${tmp}/clone-clean" rev-parse HEAD)"
advance_remote "${tmp}/remote-clean"
if [[ "$(git -C "${tmp}/remote-clean" rev-parse HEAD)" == "${stale_head}" ]]; then
  fail "stale fixture did not move origin/main"
fi
clean_code="$(run_deploy "${tmp}/clone-clean")"
if [[ "${clean_code}" -ne 0 ]]; then
  fail "clean tree exit ${clean_code}, expected 0"
  cat "${tmp}/out"
fi
origin_sha="$(git -C "${tmp}/clone-clean" rev-parse origin/main)"
head_sha="$(git -C "${tmp}/clone-clean" rev-parse HEAD)"
if [[ "${head_sha}" != "${origin_sha}" ]]; then
  fail "HEAD ${head_sha} is not origin/main ${origin_sha}"
fi
if git -C "${tmp}/clone-clean" symbolic-ref -q HEAD >/dev/null; then
  fail "clean tree stayed on a branch"
fi
if ! grep -q "^docker build -f deploy/Dockerfile.api -t curioed-api:${head_sha} .$" "${tmp}/log"; then
  fail "api image tag is not HEAD ${head_sha}"
  cat "${tmp}/log"
fi
if ! grep -q "^docker build -f deploy/Dockerfile.web -t curioed-web:${head_sha} " "${tmp}/log"; then
  fail "web image tag is not HEAD ${head_sha}"
  cat "${tmp}/log"
fi
if ! grep -q "api.image.tag=${head_sha}" "${tmp}/log"; then
  fail "helm api tag is not HEAD ${head_sha}"
  cat "${tmp}/log"
fi
if [[ "$(cat "${tmp}/state/sha")" != "${head_sha}" ]]; then
  fail "state file was not written with HEAD after the upgrade"
fi

# HEAD after checkout is not origin/main. The script must abort before build
# and must not write the state file.
real_git="$(command -v git)"
cat > "${tmp}/bin/git" << EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == "rev-parse" && "\${2:-}" == "HEAD" ]]; then
  printf '%s\\n' "0000000000000000000000000000000000000000"
  exit 0
fi
exec ${real_git} "\$@"
EOF
chmod +x "${tmp}/bin/git"
make_remote "${tmp}/remote-mismatch"
git clone -q "${tmp}/remote-mismatch" "${tmp}/clone-mismatch"
advance_remote "${tmp}/remote-mismatch"
rm -f "${tmp}/state/mismatch"
mismatch_code="$(run_deploy "${tmp}/clone-mismatch" STATE_FILE="${tmp}/state/mismatch")"
rm -f "${tmp}/bin/git"
if [[ "${mismatch_code}" -eq 0 ]]; then
  fail "HEAD mismatch exited 0"
  cat "${tmp}/out"
fi
if ! grep -q 'left HEAD at' "${tmp}/out"; then
  fail "HEAD mismatch did not say HEAD disagreed"
  cat "${tmp}/out"
fi
if grep -q '^docker ' "${tmp}/log"; then
  fail "HEAD mismatch still built an image"
  cat "${tmp}/log"
fi
if [[ -f "${tmp}/state/mismatch" ]]; then
  fail "HEAD mismatch wrote the state file"
fi

# Helm upgrade failure must not record the SHA.
make_remote "${tmp}/remote-helm"
git clone -q "${tmp}/remote-helm" "${tmp}/clone-helm"
advance_remote "${tmp}/remote-helm"
rm -f "${tmp}/state/helm-fail"
helm_code="$(run_deploy "${tmp}/clone-helm" HELM_FAIL=1 STATE_FILE="${tmp}/state/helm-fail")"
if [[ "${helm_code}" -eq 0 ]]; then
  fail "failed helm upgrade exited 0"
  cat "${tmp}/out"
fi
if [[ -f "${tmp}/state/helm-fail" ]]; then
  fail "failed helm upgrade wrote the state file"
fi
if ! grep -q '^docker build ' "${tmp}/log"; then
  fail "failed helm upgrade did not reach the build"
  cat "${tmp}/log"
fi

if [[ "${failures}" -ne 0 ]]; then
  echo "${failures} autodeploy case(s) failed"
  exit 1
fi
echo "autodeploy checkout checks passed"
