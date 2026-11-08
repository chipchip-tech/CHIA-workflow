# Image cho khối `asic`: OpenROAD-flow-scripts + Ray, để làm worker CHIA.
# Mọi worker phải cùng phiên bản Ray và cùng Python (3.10) với head (chia_env).
#
# Build (từ thư mục gốc repo):
#   docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker
# Sau đó chép ORFS ra host để sửa tay: scripts/setup-orfs.sh
#
# openroad/orfs mới dùng Python 3.14 (kiểm chứng 08/11/2026), nên Ray chạy trong một Python 3.10
# riêng ở /opt/raypy (Miniforge). Chỉ symlink lệnh `ray` ra PATH; `python3` của image giữ nguyên
# cho script của ORFS. Ray worker chạy bằng sys.executable của `ray` nên dùng 3.10.
#
# CHƯA build thử bản này.
ARG ORFS_TAG=latest
FROM openroad/orfs:${ORFS_TAG}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates openssh-client rsync \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL -o /tmp/mf.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh \
 && bash /tmp/mf.sh -b -p /opt/raypy && rm /tmp/mf.sh \
 && /opt/raypy/bin/conda install -y -q python=3.10 \
 && /opt/raypy/bin/conda clean -afy \
 && /opt/raypy/bin/python -m pip install --no-cache-dir "ray[default]==2.54.0" "chialoops==2.0.1" \
 && ln -s /opt/raypy/bin/ray /usr/local/bin/ray

# Kiểm nhanh: Python của Ray là 3.10, công cụ ORFS có sẵn.
RUN /opt/raypy/bin/python -c "import sys; assert sys.version_info[:2]==(3,10), sys.version" \
 && bash -c "source /OpenROAD-flow-scripts/env.sh && openroad -version && yosys -V && ray --version"
