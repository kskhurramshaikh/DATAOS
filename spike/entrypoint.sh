#!/bin/bash
# Builds AIRFLOW_CONN_SEAWEEDFS_S3 from the SeaweedFS env vars already set
# on this service (SEAWEEDFS_INTERNAL_HOST etc. -- see
# banking_demo_lakehouse_spike.py for where those come from), so the
# connection used for remote task-log storage never has to be hand-typed
# or duplicated on Render -- it's derived from the single source of truth
# already in place. Falls back to local logging if SEAWEEDFS_INTERNAL_HOST
# isn't set (e.g. a future non-SeaweedFS environment), rather than crashing
# the whole container over a missing optional feature.
set -e

if [ -n "$SEAWEEDFS_INTERNAL_HOST" ]; then
  export AIRFLOW_CONN_SEAWEEDFS_S3=$(python3 -c "
import json, os
conn = {
    'conn_type': 'aws',
    'login': os.environ.get('SEAWEEDFS_ACCESS_KEY', 'any'),
    'password': os.environ.get('SEAWEEDFS_SECRET_KEY', 'any'),
    'extra': {
        'endpoint_url': 'http://{}:{}'.format(
            os.environ['SEAWEEDFS_INTERNAL_HOST'],
            os.environ.get('SEAWEEDFS_S3_PORT', '8333'),
        ),
        'region_name': os.environ.get('SEAWEEDFS_S3_REGION', 'us-east-1'),
    },
}
print(json.dumps(conn))
")
  echo "Remote task logging: SeaweedFS connection configured (s3://dataos-spike/airflow-logs)"
else
  export AIRFLOW__LOGGING__REMOTE_LOGGING=False
  echo "Remote task logging: SEAWEEDFS_INTERNAL_HOST not set, falling back to local logging"
fi

exec airflow standalone
