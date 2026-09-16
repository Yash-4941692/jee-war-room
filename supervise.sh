#!/bin/bash
# Always-alive launcher (used by the workspace process manager).
# The guardian itself dedupes with a lock; this wrapper guarantees relaunch.
while true; do
  bash /home/user/jee-war-room/guardian.sh
  sleep 3
done
