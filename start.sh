#!/bin/bash
cd "$(dirname "$0")"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     🚀 CodeForge - Starting System           ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════╝${NC}"

# Load environment variables
if [ -f .env ]; then
    echo -e "${GREEN}✅ Loading environment variables...${NC}"
    set -a
    source .env
    set +a
else
    echo -e "${RED}❌ .env file not found! Please create one.${NC}"
    exit 1
fi

# Create directories
echo -e "${YELLOW}📁 Creating directories...${NC}"
mkdir -p data .codeforge_changes logs backups

# Check Docker
echo -e "${YELLOW}🐳 Checking Docker...${NC}"
if ! docker info > /dev/null 2>&1; then
    echo -e "${RED}❌ Docker is not running. Please start Docker first.${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker is running${NC}"

# Build base image
echo -e "${YELLOW}🔍 Checking base image...${NC}"
if ! docker image inspect codeforge-base:latest > /dev/null 2>&1; then
    echo -e "${YELLOW}📦 Building base image...${NC}"
    cd base-image
    docker build -t codeforge-base:latest .
    cd ..
    echo -e "${GREEN}✅ Base image built${NC}"
else
    echo -e "${GREEN}✅ Base image found${NC}"
fi

# Setup Python
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}📦 Creating virtual environment...${NC}"
    python3 -m venv venv
fi

source venv/bin/activate

echo -e "${YELLOW}📦 Installing dependencies...${NC}"
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt

echo -e "${GREEN}✅ All dependencies installed${NC}"

# Display startup info
PORT=${BACKEND_PORT:-5000}
echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║                                             ║${NC}"
echo -e "${BLUE}║    ⚡ CodeForge is ready!                   ║${NC}"
echo -e "${BLUE}║                                             ║${NC}"
echo -e "${BLUE}║    🌐 http://localhost:${PORT}              ║${NC}"
echo -e "${BLUE}║                                             ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════════╝${NC}"
echo ""

# Start application
python3 backend/app.py