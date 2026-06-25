FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
    curl git wget nano vim python3 python3-pip inotify-tools \
    && rm -rf /var/lib/apt/lists/*

RUN rm -f /usr/bin/python && ln -s /usr/bin/python3 /usr/bin/python
RUN rm -f /usr/bin/pip && ln -s /usr/bin/pip3 /usr/bin/pip

RUN wget https://github.com/coder/code-server/releases/download/v4.89.0/code-server_4.89.0_amd64.deb \
    && dpkg -i code-server_4.89.0_amd64.deb && rm code-server_4.89.0_amd64.deb

RUN mkdir -p /workspace
WORKDIR /workspace
EXPOSE 8080

RUN echo '#!/bin/bash\n\
echo "Starting CodeForge IDE with REAL file watcher..."\n\
\n\
# Restore pending changes\n\
if [ ! -z "$CODEFORGE_CHANGES" ]; then\n\
    echo "$CODEFORGE_CHANGES" | base64 -d > /tmp/changes.json\n\
    if [ -f /tmp/changes.json ]; then\n\
        python3 -c "\n\
import json, os\n\
with open(\"/tmp/changes.json\") as f:\n\
    changes = json.load(f)\n\
for file_path, content in changes.get(\"files\", {}).items():\n\
    os.makedirs(os.path.dirname(file_path), exist_ok=True)\n\
    with open(file_path, \"w\") as f:\n\
        f.write(content)\n\
    print(f\"Restored: {file_path}\")\n\
"\n\
    fi\n\
fi\n\
\n\
if [ ! -z "$REPO_URL" ] && [ ! -z "$REPO_NAME" ]; then\n\
    git clone $REPO_URL /workspace/$REPO_NAME\n\
    cd /workspace/$REPO_NAME\n\
fi\n\
\n\
# REAL-TIME FILE WATCHER using inotify\n\
cat > /tmp/file_watcher.py << "PYSCRIPT"\n\
import os, json, time, hashlib, requests, threading, sys\n\
\n\
WORKSPACE_ID = os.environ.get("WORKSPACE_ID", "")\n\
WATCH_DIR = f"/workspace/{os.environ.get('REPO_NAME', 'workspace')}"\n\
TRACKED_FILES = {}\n\
CHANGE_BUFFER = {"files": {}, "deleted_files": []}\n\
\n\
def get_file_hash(path):\n\
    try:\n\
        with open(path, "rb") as f:\n\
            return hashlib.md5(f.read()).hexdigest()\n\
    except:\n\
        return None\n\
\n\
def scan_directory():\n\
    global TRACKED_FILES, CHANGE_BUFFER\n\
    current_files = {}\n\
    for root, dirs, files in os.walk(WATCH_DIR):\n\
        if ".git" in dirs:\n\
            dirs.remove(".git")\n\
        for file in files:\n\
            path = os.path.join(root, file)\n\
            relpath = os.path.relpath(path, WATCH_DIR)\n\
            file_hash = get_file_hash(path)\n\
            if file_hash:\n\
                current_files[relpath] = file_hash\n\
                if relpath not in TRACKED_FILES:\n\
                    # New file created\n\
                    with open(path, "r") as f:\n\
                        CHANGE_BUFFER["files"][relpath] = f.read()\n\
                    print(f"[WATCHER] New file: {relpath}")\n\
                elif TRACKED_FILES[relpath] != file_hash:\n\
                    # File modified\n\
                    with open(path, "r") as f:\n\
                        CHANGE_BUFFER["files"][relpath] = f.read()\n\
                    print(f"[WATCHER] Modified: {relpath}")\n\
    \n\
    for relpath in TRACKED_FILES:\n\
        if relpath not in current_files:\n\
            CHANGE_BUFFER["deleted_files"].append(relpath)\n\
            print(f"[WATCHER] Deleted: {relpath}")\n\
    \n\
    TRACKED_FILES = current_files\n\
    \n\
    if CHANGE_BUFFER["files"] or CHANGE_BUFFER["deleted_files"]:\n\
        try:\n\
            requests.post(\n\
                f"http://host.docker.internal:5000/api/workspace/sync/{WORKSPACE_ID}",\n\
                json=CHANGE_BUFFER,\n\
                timeout=5\n\
            )\n\
            print(f"[WATCHER] Synced {len(CHANGE_BUFFER['files'])} files, {len(CHANGE_BUFFER['deleted_files'])} deletes")\n\
            CHANGE_BUFFER = {"files": {}, "deleted_files": []}\n\
        except Exception as e:\n\
            print(f"[WATCHER] Sync error: {e}")\n\
\n\
def watch_loop():\n\
    print(f"[WATCHER] Watching directory: {WATCH_DIR}")\n\
    while True:\n\
        time.sleep(3)\n\
        scan_directory()\n\
\n\
if __name__ == "__main__" and WORKSPACE_ID:\n\
    print(f"[WATCHER] Started for workspace: {WORKSPACE_ID}")\n\
    # Initial scan\n\
    scan_directory()\n\
    # Start watching\n\
    watch_loop()\n\
PYSCRIPT\n\
\n\
python3 /tmp/file_watcher.py &\n\
\n\
echo "Starting code-server..."\n\
code-server --bind-addr 0.0.0.0:8080 --auth none /workspace/${REPO_NAME:-workspace}\n\
' > /start.sh && chmod +x /start.sh

CMD ["/start.sh"]
