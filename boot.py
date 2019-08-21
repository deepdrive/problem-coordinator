import os
from utils import pip_install

ROOT_DIR = os.path.dirname(os.path.realpath(__file__))
PROBLEM_CONSTANTS_GIT = \
    'git+git://github.com/deepdrive/problem-constants#egg=problem-constants'


def ensure_shared_libs():
    try:
        import problem_constants
    except ImportError:

        pip_install(
            '--upgrade', '--force-reinstall', '--ignore-installed',
            PROBLEM_CONSTANTS_GIT)


ensure_shared_libs()
