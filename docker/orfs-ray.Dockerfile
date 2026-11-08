# Image cho khối `asic`: OpenROAD-flow-scripts + Ray, để làm worker CHIA.
# Mọi worker phải cùng phiên bản Ray và cùng Python (3.10) với head (chia_env).
#
# Build (từ thư mục gốc repo):
#   docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker
#
# CHƯA build thử. Nếu bản openroad/orfs mới đổi Python, ghim tag cũ hơn ở ORFS_TAG.
ARG ORFS_TAG=latest
FROM openroad/orfs:${ORFS_TAG}

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-pip openssh-client rsync \
 && rm -rf /var/lib/apt/lists/*

# Ray không chịu nối node khác phiên bản Python: dừng build sớm nếu lệch.
RUN python3 -c "import sys; v=sys.version_info[:2]; assert v==(3,10), f'Python {v}, head dùng 3.10'"

RUN python3 -m pip install --no-cache-dir "ray[default]==2.54.0" "chialoops==2.0.1"

# Kiểm nhanh công cụ có sẵn trong image.
RUN bash -c "source /OpenROAD-flow-scripts/env.sh && openroad -version && yosys -V && ray --version"
