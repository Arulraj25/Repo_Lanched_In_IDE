#!/bin/bash
cd "$(dirname "$0")"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}🚀 Starting CodeForge...${NC}"

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Create necessary directories
mkdir -p data
mkdir -p .codeforge_changes
mkdir -p logs

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}📦 Creating virtual environment...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✅ Virtual environment created${NC}"
fi

# Activate virtual environment
echo -e "${YELLOW}🔧 Activating virtual environment...${NC}"
source venv/bin/activate

# Install/update requirements
echo -e "${YELLOW}📦 Installing dependencies...${NC}"
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt

echo -e "${GREEN}✅ All dependencies installed${NC}"
echo ""

# Get port from environment or use default
PORT=${BACKEND_PORT:-5000}
HOST=${BACKEND_HOST:-0.0.0.0}

echo -e "${GREEN}📍 CodeForge running at http://localhost:${PORT}${NC}"
echo ""
echo "✅ ALL FEATURES IMPLEMENTED:"
echo "   • Real-time file tracking with inotify"
echo "   • Before close: Push to GitHub or save locally"
echo "   • Custom commit messages"
echo "   • Pending changes badge with count"
echo "   • Commit button from dashboard"
echo ""

# Run the application
python3 backend/app.py