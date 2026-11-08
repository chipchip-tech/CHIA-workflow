# Image cho khối `asic`: OpenROAD-flow-scripts + Ray, để làm worker CHIA.
# Mọi worker phải cùng phiên bản Ray và cùng Python với head (chia_env), ĐÚNG tới bản vá: 3.10.19.
#
# Build (từ thư mục gốc repo):
#   docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker
# Sau đó chép ORFS ra host để sửa tay: scripts/setup-orfs.sh
#
# openroad/orfs mới dùng Python 3.14 (kiểm chứng 08/11/2026), nên Ray chạy trong một Python 3.10
# riêng ở /opt/raypy (env conda tạo bằng Miniforge ở /opt/miniforge). Chỉ symlink lệnh `ray` ra PATH; `python3` của image giữ nguyên
# cho script của ORFS. Ray worker chạy bằng sys.executable của `ray` nên dùng 3.10.
#
# Đã build thật (08/11/2026).
ARG ORFS_TAG=latest
FROM openroad/orfs:${ORFS_TAG}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates openssh-client rsync \
 && rm -rf /var/lib/apt/lists/*

# Không hạ Python của base Miniforge (xung đột pip/conda): tạo env riêng /opt/raypy.
RUN curl -fsSL -o /tmp/mf.sh https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh \
 && bash /tmp/mf.sh -b -p /opt/miniforge && rm /tmp/mf.sh \
 && /opt/miniforge/bin/conda create -y -q -p /opt/raypy python=3.10.19 pip \
 && /opt/miniforge/bin/conda clean -afy \
 && /opt/raypy/bin/python -m pip install --no-cache-dir "ray[default]==2.54.0" "chialoops==2.0.1" \
 && ln -s /opt/raypy/bin/ray /usr/local/bin/ray

# `chia down` dừng Ray trong container bằng `python3` trên PATH (ở đây là /opt/orfs-python, 3.14) và cần
# psutil; cả hai Python đều không có (kiểm chứng 09/11/2026). Cài vào cả hai; dùng pip của raypy với
# --python vì venv của ORFS có thể không có pip.
RUN /opt/raypy/bin/python -m pip install --no-cache-dir psutil \
 && /opt/raypy/bin/python -m pip --python "$(command -v python3)" install --no-cache-dir psutil \
 && python3 -c "import psutil" && /opt/raypy/bin/python -c "import psutil"

# Kiểm nhanh: Python của Ray là 3.10, công cụ ORFS có sẵn.
RUN /opt/raypy/bin/python -c "import sys; assert sys.version_info[:3]==(3,10,19), sys.version" \
 && bash -c "source /OpenROAD-flow-scripts/env.sh && openroad -version && yosys -V && ray --version"
