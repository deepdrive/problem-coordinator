#!/usr/bin/env bash

set -e  # Abort script at first error, when a command exits with non-zero status (except in until or while loops, if-tests, list constructs)
set -u  # Attempt to use undefined variable outputs error message, and forces an exit
set -x  # Similar to verbose mode (-v), but expands commands
set -o pipefail  # Causes a pipeline to return the exit status of the last command in the pipe that returned a non-zero return value.

# For running on cloud VM in docker
DIR=`dirname "$0"`
cd ${DIR}/..

# Always get the latest for our shared libs
pip install --upgrade --force-reinstall --ignore-installed git+git://github.com/deepdrive/problem-constants#egg=problem-constants

python -u main.py
