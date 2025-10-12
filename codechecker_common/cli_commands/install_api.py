import argparse
import os
import subprocess
import sys


def get_argparser_ctor_args():
    return {
        'prog': 'CodeChecker install-api',
        'description': 'Install bundled CodeChecker Thrift API packages '
                       '(codechecker_api, codechecker_api_shared).'
    }


def add_arguments_to_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--upgrade', action='store_true',
        help='Force reinstallation if packages are already installed.')
    parser.set_defaults(func=main)


def _find_packaged_api_tarballs() -> tuple[str | None, str | None]:
    data_dir = os.environ.get('CC_DATA_FILES_DIR')
    if not data_dir:
        return None, None
    base_py = os.path.join(data_dir, 'web', 'api', 'py')
    shared = os.path.join(
        base_py, 'codechecker_api_shared', 'dist', 'codechecker_api_shared.tar.gz')
    api = os.path.join(base_py, 'codechecker_api', 'dist', 'codechecker_api.tar.gz')
    return (shared if os.path.exists(shared) else None,
            api if os.path.exists(api) else None)


def run(args: argparse.Namespace) -> int:
    shared, api = _find_packaged_api_tarballs()
    if not shared or not api:
        print('Packaged API tarballs not found in data directory.\n'
              'Make sure you built the distribution with bundled API artifacts.')
        return 1

    cmd_base = [sys.executable, '-m', 'pip', 'install']
    if args.upgrade:
        cmd_base.append('--force-reinstall')

    try:
        print(f'Installing {shared} ...')
        subprocess.check_call(cmd_base + [shared])
        print(f'Installing {api} ...')
        subprocess.check_call(cmd_base + [api])
        print('API packages installed successfully.')
        return 0
    except subprocess.CalledProcessError as e:
        print(f'Failed to install API packages: {e}')
        return e.returncode or 1


def main(args: argparse.Namespace) -> int:
    return run(args)


