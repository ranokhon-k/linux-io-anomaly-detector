FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# basic tools + python
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    fio \
    sysstat \
    procps \
    util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# install python deps
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# copy project files
COPY src/ ./src/
COPY tests/ ./tests/
COPY run_experiment.sh .

RUN mkdir -p logs

# the entrypoint runs the full experiment
RUN chmod +x run_experiment.sh
CMD ["bash", "run_experiment.sh"]
