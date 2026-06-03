# Builds a NetBox image with the SNMP Sync plugin installed.
# Used by netbox-docker's docker-compose.override.yml (build context = this repo).
#
# The plugin is installed editable so that bind-mounting this source directory over
# /opt/netbox-plugin-snmp-sync (see the compose override) makes local code edits take
# effect on the next container/process restart, without rebuilding the image.
ARG NETBOX_IMAGE=netboxcommunity/netbox:v4.6-5.0.1
FROM ${NETBOX_IMAGE}

COPY . /opt/netbox-plugin-snmp-sync
# The NetBox image ships a uv-managed venv at /opt/netbox/venv (no pip inside it),
# so install the plugin with uv targeting that interpreter.
RUN /usr/local/bin/uv pip install --no-cache \
    --python /opt/netbox/venv/bin/python \
    --editable /opt/netbox-plugin-snmp-sync
