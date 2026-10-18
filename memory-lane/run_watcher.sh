#!/bin/bash
# Start the conversation watcher service

cd /home/jmyles/projects/JustinHolmesMusic/arthel/arthel/magenta
source django-venv/bin/activate
WATCHER_ERA_NAME="${WATCHER_ERA_NAME:-Watcher Experiment}" django-venv/bin/python watcher/conversation_watcher.py
