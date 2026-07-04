#!/bin/bash

# Build the React UI for CLASP
set -e

echo "Building React UI..."
cd "$(dirname "$0")/clasp/ui/react"

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
fi

# Build the React app
npm run build

echo "React UI build complete!"

# Copy built files to the static directory
mkdir -p "../../static-react"
cp -r dist/* "../../static-react/"

echo "React UI files copied to static-react directory"
