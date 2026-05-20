FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        curl \
        git \
        openssh-server \
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install -e . \
    && pip install "pytest>=8.3.3,<9" "pytest-cov>=6.0.0,<7" "pytest-xdist>=3.6.1,<4" "hypothesis>=6.119.4,<7"

RUN mkdir -p /run/sshd /root/.ssh \
    && echo "root:root" | chpasswd \
    && chmod 700 /root/.ssh \
    && sed -i 's/#\\?PermitRootLogin .*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/#\\?PasswordAuthentication .*/PasswordAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/#\\?UsePAM .*/UsePAM no/' /etc/ssh/sshd_config \
    && printf '\ncd /app\n' >> /root/.bashrc

RUN git config --global --add safe.directory /app \
    && git config --global user.email "rl-scaling@example.local" \
    && git config --global user.name "RL Scaling" \
    && if [ ! -d .git ]; then git init; fi \
    && git add . \
    && git commit -m "Initial task snapshot" || true

EXPOSE 22

CMD ["/usr/sbin/sshd", "-D", "-e"]
