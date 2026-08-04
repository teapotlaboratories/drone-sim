# Minimal AirSim RPC client — the thing that talks to a running simulator and pulls images.
#
# This exists because the capture experiments were previously run from throwaway containers
# with an ad-hoc `pip install`, which is precisely why their results were hard to trust or
# repeat. The Cosys-AirSim Python client is NOT installed here; it is mounted from vendor/ at
# run time so the client always matches the vendored tree that built the plugin.
#
# msgpack-rpc-python is pinned because it is unmaintained and drags in tornado: releases after
# 0.4.1 do not exist, and tornado >= 5 breaks its IOLoop usage.
FROM python:3.11-slim

RUN pip install --no-cache-dir \
      msgpack-rpc-python==0.4.1 \
      "tornado<5" \
      numpy==1.26.4 \
      opencv-python-headless==4.10.0.84

# The vendored client is mounted at /client (see scripts/capture_experiment.py).
ENV PYTHONPATH=/client
WORKDIR /work
