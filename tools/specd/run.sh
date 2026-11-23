#!/usr/bin/env bash
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
git_common=$(git rev-parse --path-format=absolute --git-common-dir)
git_dir=$(git rev-parse --path-format=absolute --git-dir)
git_user_name=$(git config user.name || true)
git_user_email=$(git config user.email || true)
image=${SPECD_IMAGE:-local-lab-specd:0.2.0}
user_args=()

if [[ "$(uname -s)" == Linux* ]]; then
    user_args=(--user "$(id -u):$(id -g)")
fi

if [[ -z "$git_user_name" || -z "$git_user_email" ]]; then
    echo "FAIL: git user.name and user.email must already be configured on the host" >&2
    exit 2
fi

case "$git_dir" in
    "$git_common") container_git_dir=/git ;;
    "$git_common"/*) container_git_dir="/git/${git_dir#"$git_common"/}" ;;
    *)
        echo "FAIL: git dir is outside git common dir: $git_dir" >&2
        exit 2
        ;;
esac

docker build --quiet \
    --file "$repo_root/tools/specd/Dockerfile" \
    --tag "$image" \
    "$repo_root" >/dev/null

docker run --rm \
    "${user_args[@]}" \
    --env HOME=/tmp \
    --env GIT_DIR="$container_git_dir" \
    --env GIT_WORK_TREE=/workspace \
    --env GIT_CONFIG_COUNT=3 \
    --env GIT_CONFIG_KEY_0=safe.directory \
    --env GIT_CONFIG_VALUE_0=/workspace \
    --env GIT_CONFIG_KEY_1=user.name \
    --env GIT_CONFIG_VALUE_1="$git_user_name" \
    --env GIT_CONFIG_KEY_2=user.email \
    --env GIT_CONFIG_VALUE_2="$git_user_email" \
    --volume "$repo_root:/workspace" \
    --volume "$git_common:/git:ro" \
    --workdir /workspace \
    "$image" "$@"
