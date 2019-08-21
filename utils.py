import json
import os
import os.path as p
from datetime import datetime
import pip

from box import Box


def get_str_or_box(content_str, filename):
    if filename.endswith('.json') and content_str:
        ret = Box(json.loads(content_str))
    else:
        ret = content_str
    return ret


def write_json(obj, path):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2)


def read_json(filename):
    with open(filename) as file:
        results = json.load(file)
    return results


def write_file(content, path):
    with open(path, 'w') as f:
        f.write(content)


def read_file(path):
    with open(path) as f:
        ret = f.read()
    return ret


def read_lines(path):
    content = read_file(path)
    lines = content.split()
    return lines


def append_file(path, strings):
    with open(path, 'a') as f:
        f.write('\n'.join(strings) + '\n')


def exists_and_unempty(problem_filename):
    return p.exists(problem_filename) and os.stat(problem_filename).st_size != 0


def is_docker():
    path = '/proc/self/cgroup'
    return (
        os.path.exists('/.dockerenv') or
        os.path.isfile(path) and any('docker' in line for line in open(path))
    )


def generate_rand_alphanumeric(num_chars):
    from secrets import choice
    import string
    alphabet = string.ascii_lowercase + string.digits
    ret = ''.join(choice(alphabet) for _ in range(num_chars))
    return ret


def modification_date(filename):
    t = os.path.getmtime(filename)
    return datetime.fromtimestamp(t)


def pip_install(*args):
    if hasattr(pip, 'main'):
        pip_main = pip.main
    else:
        from pip._internal import main as pip_main

    args = ['install', '-vvv'] + list(args)
    try:
        pip_main(args)
    except Exception as e:
        try:
            # If at first you don't succeed!!!
            pip_main(args)
        except Exception as e:
            # Swallow exceptions here to ignore pip-req-tracker tmp deletion errors.
            # Attempting to import said module will hopefully keep this from being hard to track root cause for.
            # TODO: Be more specific about exceptions we swallow.
            print('Error installing %s - error was: %s' % (args, str(e)))


def pip_install2(package, req_path=None):
    if hasattr(pip, 'main'):
        pip_main = pip.main
    else:
        from pip._internal import main as pip_main

    args = ['install']
    if req_path is not None:
        args += ['--target', req_path]
        # Ubuntu bug workaround - https://github.com/pypa/pip/issues/3826#issuecomment-427622702
        args.append('--system')

    args.append(package)
    pip_main(args)
