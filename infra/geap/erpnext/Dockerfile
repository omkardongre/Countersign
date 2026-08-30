FROM frappe/erpnext:v16.31.1

USER root
RUN apt-get update \
    && apt-get install --no-install-recommends -y rclone \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=frappe:frappe countersign_erpnext /home/frappe/frappe-bench/apps/countersign_erpnext

USER frappe
WORKDIR /home/frappe/frappe-bench
RUN ./env/bin/pip install --no-cache-dir --editable ./apps/countersign_erpnext \
    && bench build --app countersign_erpnext

LABEL org.opencontainers.image.base.name="docker.io/frappe/erpnext:v16.31.1" \
      org.opencontainers.image.title="Countersign ERPNext"
