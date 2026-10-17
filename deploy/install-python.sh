#!/bin/sh
# Install locked dependencies, then the CPU PyTorch wheels.
# The lockfile's Linux torch build depends on multi-gigabyte NVIDIA packages.
# Those wheels exist for aarch64, but an Always Free ARM VM has no GPU and a
# small boot disk, so the image keeps the CPU builds instead.
set -eu

cd /app/backend
mode="${1:?usage: install-python.sh deps|project}"

excludes="
torch
torchvision
triton
cuda-bindings
cuda-toolkit
cuda-pathfinder
nvidia-cublas
nvidia-cuda-cupti
nvidia-cuda-nvrtc
nvidia-cuda-runtime
nvidia-cudnn-cu13
nvidia-cufft
nvidia-cufile
nvidia-curand
nvidia-cusolver
nvidia-cusparse
nvidia-cusparselt-cu13
nvidia-nccl-cu13
nvidia-nvjitlink
nvidia-nvshmem-cu13
nvidia-nvtx
"

set -- uv sync --python /usr/local/bin/python3 --frozen --no-dev
for pkg in $excludes; do
  set -- "$@" --no-install-package "$pkg"
done

case "$mode" in
  deps)
    set -- "$@" --no-install-project
    "$@"
    uv pip install --python /app/backend/.venv/bin/python --torch-backend cpu \
      "torch==2.13.0" "torchvision==0.28.0"
    ;;
  project)
    # Keep the CPU torch installed above. A plain sync would treat it as
    # extraneous and put the CUDA build back.
    set -- "$@" --inexact
    "$@"
    ;;
  *)
    echo "unknown mode: $mode" >&2
    exit 1
    ;;
esac
