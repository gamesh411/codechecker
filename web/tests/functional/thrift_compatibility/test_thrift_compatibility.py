#
# -------------------------------------------------------------------------
#
#  Part of the CodeChecker project, under the Apache License v2.0 with
#  LLVM Exceptions. See LICENSE for license information.
#  SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# -------------------------------------------------------------------------
"""
Test Thrift compatibility between different versions of CodeChecker.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
import venv

from libtest import codechecker
from libtest import env
from libtest import project
import multiprocess

from codechecker_api.codeCheckerDBAccess_v6 import ttypes


class TestThriftCompatibility(unittest.TestCase):
    """
    Test Thrift compatibility between different versions of CodeChecker.
    """

    @classmethod
    def setUpClass(cls):
        """Setup the environment for testing."""
        global TEST_WORKSPACE
        TEST_WORKSPACE = env.get_workspace('thrift_compatibility')
        os.environ['TEST_WORKSPACE'] = TEST_WORKSPACE

        # Get a free port for the server
        cls.host_port_cfg = {
            'viewer_host': 'localhost',
            'viewer_port': env.get_free_port(),
            'viewer_product': 'default'
        }

        # Create test environment
        cls.test_env = env.test_env(TEST_WORKSPACE)

        # Setup CodeChecker server configuration
        cls.codechecker_cfg = {
            'check_env': cls.test_env,
            'workspace': TEST_WORKSPACE,
            'checkers': [],
            'analyzers': ['clangsa', 'clang-tidy'],
            'pg_db_config': env.get_postgresql_cfg(),
            'reportdir': os.path.join(TEST_WORKSPACE, 'reports'),
            'test_project': 'cpp',
        }

        # Merge host port and checker configuration
        cls.codechecker_cfg.update(cls.host_port_cfg)

        # Export test configuration to the workspace
        env.export_test_cfg(
            TEST_WORKSPACE,
            {'codechecker_cfg': cls.codechecker_cfg})

        # Start the CodeChecker server
        print("Starting server")
        server_data = codechecker.start_server(cls.codechecker_cfg)
        cls.server_process = server_data['server_process']
        cls.stop_event = server_data['stop_event']

        # Create virtual environment for old client
        cls.old_client_venv = os.path.join(
            TEST_WORKSPACE, 'venv_old_client')
        venv.create(cls.old_client_venv, with_pip=True)

        # Install old version of CodeChecker in the virtual environment
        pip_cmd = os.path.join(cls.old_client_venv, 'bin', 'pip')
        subprocess.check_call(
            [pip_cmd, 'install', 'codechecker==6.18.2'])

        # Create some test project data
        cls._create_test_project()

    @classmethod
    def tearDownClass(cls):
        """Clean up after the tests."""
        # Stop the CodeChecker server
        if hasattr(cls, 'stop_event'):
            cls.stop_event.set()
        if hasattr(cls, 'server_process'):
            cls.server_process.join(timeout=10)
            if cls.server_process.is_alive():
                cls.server_process.terminate()

        # Clean up workspace
        if hasattr(cls, 'TEST_WORKSPACE'):
            shutil.rmtree(TEST_WORKSPACE, ignore_errors=True)

    @classmethod
    def _create_test_project(cls):
        """Create a test project with some reports."""
        test_project_path = os.path.join(TEST_WORKSPACE, 'test_proj')
        os.makedirs(test_project_path)

        test_file = os.path.join(test_project_path, 'main.cpp')
        with open(test_file, 'w') as f:
            f.write('''
                int main() {
                    int* ptr = nullptr;
                    *ptr = 42;  // null dereference
                    return 0;
                }
            ''')

        # Analyze the test project
        analyze_cmd = os.path.join(
            os.environ['CODECHECKER_HOME'],
            'bin', 'CodeChecker')
        subprocess.check_call([
            analyze_cmd, 'analyze',
            '--clean',
            '--output', os.path.join(TEST_WORKSPACE, 'reports'),
            '--analyzers', 'clangsa',
            test_project_path
        ])

        # Store the results
        store_cmd = [
            analyze_cmd, 'store',
            '--url', f"localhost:{cls.host_port_cfg['viewer_port']}/Default",
            '--name', 'test_proj',
            os.path.join(TEST_WORKSPACE, 'reports')
        ]
        subprocess.check_call(store_cmd)

    def _run_old_client_test(self, test_code):
        """Run test code with the old client."""
        test_script = os.path.join(TEST_WORKSPACE, 'old_client_test.py')
        with open(test_script, 'w') as f:
            f.write(f'''
import os
import sys
import json
from codechecker_api.codeCheckerDBAccess_v6 import ttypes
from codechecker_api_shared.ttypes import RequestFailed
from codechecker_client.client import setup_client

def main():
    try:
        client = setup_client(host="{self.host_port_cfg['viewer_host']}",
                            port={self.host_port_cfg['viewer_port']})

        result = {test_code}

        # Print result as JSON for the test to parse
        print("TEST_RESULT_START")
        print(json.dumps(result))
        print("TEST_RESULT_END")
        return 0
    except RequestFailed as ex:
        print("TEST_RESULT_START")
        print(json.dumps({{"error": ex.message}}))
        print("TEST_RESULT_END")
        return 1
    except Exception as ex:
        print("TEST_RESULT_START")
        print(json.dumps({{"error": str(ex)}}))
        print("TEST_RESULT_END")
        return 1

if __name__ == "__main__":
    sys.exit(main())
''')

        old_client_python = \
            os.path.join(self.old_client_venv, 'bin', 'python3')
        process = subprocess.Popen(
            [old_client_python, test_script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate()

        # Extract test result from output
        result_lines = []
        in_result = False
        for line in stdout.splitlines():
            if line == "TEST_RESULT_START":
                in_result = True
            elif line == "TEST_RESULT_END":
                in_result = False
            elif in_result:
                result_lines.append(line)

        if not result_lines:
            self.fail(
                f"No test result found in output. Stdout: {stdout}, "
                f"Stderr: {stderr}")

        result = json.loads(''.join(result_lines))
        if "error" in result:
            self.fail(f"Test failed: {result['error']}")

        return result

    def test_01_connection(self):
        """Test that old client can connect to the new server."""
        result = self._run_old_client_test('''
            # Test basic connection
            client.getPackageVersion()
            return {"status": "connected"}
        ''')
        self.assertEqual(result["status"], "connected")

    def test_02_package_version(self):
        """Test that old client can get package version from new server."""
        result = self._run_old_client_test('''
            version = client.getPackageVersion()
            return {"version": version}
        ''')
        self.assertIn("version", result)
        self.assertIsInstance(result["version"], str)
        self.assertGreater(len(result["version"]), 0)

    def test_03_store_and_get_runs(self):
        """Test that old client can list runs from new server."""
        result = self._run_old_client_test('''
            runs = client.getRunData(None, None, 0, None)
            return {
                "run_count": len(runs),
                "first_run_name": runs[0].name if runs else None
            }
        ''')
        self.assertGreater(result["run_count"], 0, "No runs found on server")
        self.assertEqual(result["first_run_name"], "test_proj")

    def test_04_get_run_results(self):
        """Test that old client can get analysis results from new server."""
        result = self._run_old_client_test('''
            runs = client.getRunData(None, None, 0, None)
            run_id = runs[0].runId
            results = client.getRunResults(
                [run_id], 100, 0, None, None, None)
            return {
                "result_count": len(results),
                "first_result_checker": (results[0].checkerId
                                         if results else None)
            }
        ''')
        self.assertGreater(result["result_count"], 0,
                           "No analysis results found")
        self.assertEqual(result["first_result_checker"],
                         "core.NullDereference")

    def test_05_product_access(self):
        """Test that old client can access product information."""
        result = self._run_old_client_test('''
            products = client.getProducts(None, None)
            return {
                "product_count": len(products),
                "first_product_name": (products[0].displayedName_b64
                                     if products else None)
            }
        ''')
        self.assertGreater(result["product_count"], 0, "No products found")
        self.assertIsNotNone(result["first_product_name"])


if __name__ == '__main__':
    unittest.main()
