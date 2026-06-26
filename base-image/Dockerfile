FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV CODE_SERVER_VERSION=4.89.0
ENV WORKSPACE_DIR=/workspace

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl git wget nano vim python3 python3-pip \
    inotify-tools build-essential \
    && rm -rf /var/lib/apt/lists/*

# Python symlinks
RUN ln -sf /usr/bin/python3 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

# Install code-server - FIXED URL
RUN curl -fsSL https://github.com/coder/code-server/releases/download/v${CODE_SERVER_VERSION}/code-server_${CODE_SERVER_VERSION}_amd64.deb -o code-server.deb \
    && dpkg -i code-server.deb || true \
    && apt-get install -f -y \
    && rm code-server.deb

# Create workspace
RUN mkdir -p ${WORKSPACE_DIR}
WORKDIR ${WORKSPACE_DIR}
EXPOSE 8080

# Start script
RUN echo '#!/bin/bash\n\
set -e\n\
echo "🚀 Starting CodeForge IDE Container"\n\
echo "📁 Workspace: ${WORKSPACE_DIR}"\n\
\n\
# Restore changes\n\
if [ ! -z "$CODEFORGE_CHANGES" ]; then\n\
    echo "📦 Restoring pending changes..."\n\
    echo "$CODEFORGE_CHANGES" | base64 -d > /tmp/changes.json\n\
    if [ -f /tmp/changes.json ]; then\n\
        python3 -c "\n\
import json, os\n\
with open(\"/tmp/changes.json\") as f:\n\
    changes = json.load(f)\n\
for file_path, content in changes.get(\"files\", {}).items():\n\
    if file_path and content is not None:\n\
        os.makedirs(os.path.dirname(file_path), exist_ok=True)\n\
        with open(file_path, \"w\") as f:\n\
            f.write(content)\n\
        print(f\"   ✅ Restored: {file_path}\")\n\
"\n\
        rm -f /tmp/changes.json\n\
    fi\n\
fi\n\
\n\
# Clone repo\n\
if [ ! -z "$GITHUB_TOKEN" ] && [ ! -z "$REPO_FULL_NAME" ]; then\n\
    echo "📥 Cloning: $REPO_FULL_NAME"\n\
    REPO_URL="https://$GITHUB_TOKEN@github.com/$REPO_FULL_NAME.git"\n\
    REPO_PATH="${WORKSPACE_DIR}/${REPO_NAME}"\n\
    if [ ! -d "$REPO_PATH" ]; then\n\
        git clone $REPO_URL $REPO_PATH\n\
        echo "   ✅ Clone complete"\n\
    fi\n\
    cd $REPO_PATH\n\
else\n\
    echo "⚠️ Skipping clone"\n\
    mkdir -p ${WORKSPACE_DIR}/${REPO_NAME:-workspace}\n\
    cd ${WORKSPACE_DIR}/${REPO_NAME:-workspace}\n\
fi\n\
\n\
# Start code-server\n\
echo "💻 Starting code-server on port 8080..."\n\
exec code-server --bind-addr 0.0.0.0:8080 --auth none .\n\
' > /start.sh && chmod +x /start.sh

CMD ["/start.sh"]
