#!/bin/bash
# Helpers for GitHub Actions deploy logs.

deployment_error() {
    local step=$1
    local error=$2
    local troubleshooting=$3
    echo
    echo "DEPLOYMENT FAILED: ${step}"
    echo "Error: ${error}"
    if [ -n "${troubleshooting}" ]; then
        echo
        echo "Troubleshooting:"
        echo "${troubleshooting}"
    fi
    echo
    echo "See docs/DEPLOYMENT.md"
    exit 1
}
