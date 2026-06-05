#!/bin/bash
echo "Installing dependencies for macOS..."
pip3 install requests
echo ""
echo "Installation complete!"
echo ""
echo "IMPORTANT: MTG Arena must be running via CrossOver."
echo "Run the exporter with:"
echo "  sudo python3 mtg.py"
echo ""
echo "sudo is required so the script can read game memory."
