# pylint: disable=g-backslash-continuation
# Copyright 2024 The Bazel Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# pylint: disable=g-long-ternary

import os
import tempfile
from absl.testing import absltest
from src.test.py.bazel import test_base
from src.test.py.bazel.bzlmod.test_utils import BazelRegistry


class BazelVendorTest(test_base.TestBase):

  def setUp(self):
    test_base.TestBase.setUp(self)
    self.registries_work_dir = tempfile.mkdtemp(dir=self._test_cwd)
    self.main_registry = BazelRegistry(
        os.path.join(self.registries_work_dir, 'main')
    )
    self.ScratchFile(
        '.bazelrc',
        [
            # In ipv6 only network, this has to be enabled.
            # 'startup --host_jvm_args=-Djava.net.preferIPv6Addresses=true',
            'common --noenable_workspace',
            'common --experimental_isolated_extension_usages',
            'common --registry=' + self.main_registry.getURL(),
            'common --registry=https://bcr.bazel.build',
            'common --verbose_failures',
            # Set an explicit Java language version
            'common --java_language_version=8',
            'common --tool_java_language_version=8',
            'common --lockfile_mode=update',
            'startup --windows_enable_symlinks' if self.IsWindows() else '',
        ],
    )
    self.ScratchFile('MODULE.bazel')
    self.generateBuiltinModules()

  def generateBuiltinModules(self):
    self.ScratchFile('platforms_mock/BUILD')
    self.ScratchFile(
        'platforms_mock/MODULE.bazel', ['module(name="local_config_platform")']
    )

    self.ScratchFile('tools_mock/BUILD')
    self.ScratchFile('tools_mock/MODULE.bazel', ['module(name="bazel_tools")'])
    self.ScratchFile('tools_mock/tools/build_defs/repo/BUILD')
    self.CopyFile(
        self.Rlocation('io_bazel/tools/build_defs/repo/cache.bzl'),
        'tools_mock/tools/build_defs/repo/cache.bzl',
    )
    self.CopyFile(
        self.Rlocation('io_bazel/tools/build_defs/repo/http.bzl'),
        'tools_mock/tools/build_defs/repo/http.bzl',
    )
    self.CopyFile(
        self.Rlocation('io_bazel/tools/build_defs/repo/utils.bzl'),
        'tools_mock/tools/build_defs/repo/utils.bzl',
    )

  def testBasicVendoring(self):
    self.main_registry.createCcModule('aaa', '1.0').createCcModule(
        'bbb', '1.0', {'aaa': '1.0'}
    )
    self.ScratchFile(
        'MODULE.bazel',
        [
            'bazel_dep(name = "bbb", version = "1.0")',
            'local_path_override(module_name="bazel_tools", path="tools_mock")',
            'local_path_override(module_name="local_config_platform", ',
            'path="platforms_mock")',
        ],
    )
    self.ScratchFile('BUILD')

    self.RunBazel(['vendor', '--vendor_dir=vendor'])

    # Assert repos are vendored with marker files and .vendorignore is created
    repos_vendored = os.listdir(self._test_cwd + '/vendor')
    self.assertIn('aaa~1.0', repos_vendored)
    self.assertIn('bbb~1.0', repos_vendored)
    self.assertIn('@aaa~1.0.marker', repos_vendored)
    self.assertIn('@bbb~1.0.marker', repos_vendored)
    self.assertIn('.vendorignore', repos_vendored)

  def testVendorFailsWithNofetch(self):
    self.ScratchFile('MODULE.bazel')
    self.ScratchFile('BUILD')
    _, _, stderr = self.RunBazel(
        ['vendor', '--vendor_dir=vendor', '--nofetch'], allow_failure=True
    )
    self.assertIn('ERROR: You cannot run vendor with --nofetch', stderr)

  def testVendoringMultipleTimes(self):
    self.main_registry.createCcModule('aaa', '1.0')
    self.ScratchFile(
        'MODULE.bazel',
        [
            'bazel_dep(name = "aaa", version = "1.0")',
            'local_path_override(module_name="bazel_tools", path="tools_mock")',
            'local_path_override(module_name="local_config_platform", ',
            'path="platforms_mock")',
        ],
    )
    self.ScratchFile('BUILD')

    self.RunBazel(['vendor', '--vendor_dir=vendor'])
    # Clean the external cache
    self.RunBazel(['clean', '--expunge'])
    # Re-vendoring should NOT re-fetch, but only create symlinks
    # We need to check this because the vendor logic depends on the fetch logic,
    # but we don't want to re-fetch if our vendored repo is already up-to-date!
    self.RunBazel(['vendor', '--vendor_dir=vendor'])

    _, stdout, _ = self.RunBazel(['info', 'output_base'])
    repo_path = stdout[0] + '/external/aaa~1.0'
    if self.IsWindows():
      self.assertTrue(self.IsJunction(repo_path))
    else:
      self.assertTrue(os.path.islink(repo_path))

  # Remove this test when workspace is removed
  def testVendorDirIsNotCheckedForWorkspaceRepos(self):
    self.ScratchFile(
        'MODULE.bazel',
        [
            'local_path_override(module_name="bazel_tools", path="tools_mock")',
            'local_path_override(module_name="local_config_platform", ',
            'path="platforms_mock")',
        ],
    )
    self.ScratchFile(
        'WORKSPACE.bzlmod',
        ['load("//:main.bzl", "dump_env")', 'dump_env(name = "dummyRepo")'],
    )
    self.ScratchFile('BUILD')
    self.ScratchFile(
        'main.bzl',
        [
            'def _dump_env(ctx):',
            '    ctx.file("BUILD")',
            'dump_env = repository_rule(implementation = _dump_env)',
        ],
    )
    _, _, stderr = self.RunBazel([
        'fetch',
        '@@dummyRepo//:all',
        '--enable_workspace=true',
        '--vendor_dir=blabla',
    ])
    self.assertNotIn(
        "Vendored repository 'dummyRepo' is out-of-date.", '\n'.join(stderr)
    )

  def testBuildingWithVendoredRepos(self):
    self.main_registry.createCcModule('aaa', '1.0')
    self.ScratchFile(
        'MODULE.bazel',
        [
            'bazel_dep(name = "aaa", version = "1.0")',
        ],
    )
    self.ScratchFile('BUILD')
    self.RunBazel(['vendor', '--vendor_dir=vendor'])
    self.assertIn('aaa~1.0', os.listdir(self._test_cwd + '/vendor'))

    # Empty external & build with vendor
    self.RunBazel(['clean', '--expunge'])
    self.RunBazel(['build', '@aaa//:all', '--vendor_dir=vendor'])
    # Assert repo aaa in {OUTPUT_BASE}/external is a symlink (junction on
    # windows, this validates it was created from vendor and not fetched)=
    _, stdout, _ = self.RunBazel(['info', 'output_base'])
    repo_path = stdout[0] + '/external/aaa~1.0'
    if self.IsWindows():
      self.assertTrue(self.IsJunction(repo_path))
    else:
      self.assertTrue(os.path.islink(repo_path))

  def testIgnoreFromVendoring(self):
    # Repos should be excluded from vendoring:
    # 1.Local Repos, 2.Config Repos, 3.Repos declared in .vendorignore file
    self.main_registry.createCcModule('aaa', '1.0').createCcModule(
        'bbb', '1.0', {'aaa': '1.0'}
    )
    self.ScratchFile(
        'MODULE.bazel',
        [
            'bazel_dep(name = "bbb", version = "1.0")',
            'ext = use_extension("extension.bzl", "ext")',
            'use_repo(ext, "regularRepo")',
            'use_repo(ext, "localRepo")',
            'use_repo(ext, "configRepo")',
            'local_path_override(module_name="bazel_tools", path="tools_mock")',
            'local_path_override(module_name="local_config_platform", ',
            'path="platforms_mock")',
        ],
    )
    self.ScratchFile('BUILD')
    self.ScratchFile(
        'extension.bzl',
        [
            'def _repo_rule_impl(ctx):',
            '    ctx.file("WORKSPACE")',
            '    ctx.file("BUILD")',
            '',
            'repo_rule1 = repository_rule(implementation=_repo_rule_impl)',
            'repo_rule2 = repository_rule(implementation=_repo_rule_impl, ',
            'local=True)',
            'repo_rule3 = repository_rule(implementation=_repo_rule_impl, ',
            'configure=True)',
            '',
            'def _ext_impl(ctx):',
            '    repo_rule1(name="regularRepo")',
            '    repo_rule2(name="localRepo")',
            '    repo_rule3(name="configRepo")',
            'ext = module_extension(implementation=_ext_impl)',
        ],
    )

    os.makedirs(self._test_cwd + '/vendor', exist_ok=True)
    with open(self._test_cwd + '/vendor/.vendorignore', 'w') as f:
      f.write('aaa~1.0\n')

    self.RunBazel(['vendor', '--vendor_dir=vendor'])
    repos_vendored = os.listdir(self._test_cwd + '/vendor')

    # Assert bbb and the regularRepo are vendored with marker files
    self.assertIn('bbb~1.0', repos_vendored)
    self.assertIn('@bbb~1.0.marker', repos_vendored)
    self.assertIn('_main~ext~regularRepo', repos_vendored)
    self.assertIn('@_main~ext~regularRepo.marker', repos_vendored)

    # Assert aaa (from .vendorignore), local and config repos are not vendored
    self.assertNotIn('aaa~1.0', repos_vendored)
    self.assertNotIn('bazel_tools', repos_vendored)
    self.assertNotIn('local_config_platform', repos_vendored)
    self.assertNotIn('_main~ext~localRepo', repos_vendored)
    self.assertNotIn('_main~ext~configRepo', repos_vendored)

  def testOutOfDateVendoredRepo(self):
    self.ScratchFile(
        'MODULE.bazel',
        [
            'ext = use_extension("extension.bzl", "ext")',
            'use_repo(ext, "justRepo")',
        ],
    )
    self.ScratchFile('BUILD')
    self.ScratchFile(
        'extension.bzl',
        [
            'def _repo_rule_impl(ctx):',
            '    ctx.file("WORKSPACE")',
            '    ctx.file("BUILD", "filegroup(name=\'lala\')")',
            'repo_rule = repository_rule(implementation=_repo_rule_impl)',
            '',
            'def _ext_impl(ctx):',
            '    repo_rule(name="justRepo")',
            'ext = module_extension(implementation=_ext_impl)',
        ],
    )

    # Vendor, assert and build with no problems
    self.RunBazel(['vendor', '--vendor_dir=vendor'])
    self.assertIn('_main~ext~justRepo', os.listdir(self._test_cwd + '/vendor'))
    _, _, stderr = self.RunBazel(
        ['build', '@justRepo//:all', '--vendor_dir=vendor']
    )
    self.assertNotIn(
        "WARNING: <builtin>: Vendored repository '_main~ext~justRepo' is"
        ' out-of-date. The up-to-date version will be fetched into the external'
        ' cache and used. To update the repo in the  vendor directory, run'
        " 'bazel vendor' with the directory flag",
        stderr,
    )

    # Make updates in repo definition
    self.ScratchFile(
        'extension.bzl',
        [
            'def _repo_rule_impl(ctx):',
            '    ctx.file("WORKSPACE")',
            '    ctx.file("BUILD", "filegroup(name=\'haha\')")',
            'repo_rule = repository_rule(implementation=_repo_rule_impl)',
            '',
            'def _ext_impl(ctx):',
            '    repo_rule(name="justRepo")',
            'ext = module_extension(implementation=_ext_impl)',
        ],
    )

    # Clean cache, and re-build with vendor
    self.RunBazel(['clean', '--expunge'])
    _, _, stderr = self.RunBazel(
        ['build', '@justRepo//:all', '--vendor_dir=vendor']
    )
    # Assert repo in vendor is out-of-date, and the new one is fetched into
    # external and not a symlink
    self.assertIn(
        "WARNING: <builtin>: Vendored repository '_main~ext~justRepo' is"
        ' out-of-date. The up-to-date version will be fetched into the external'
        ' cache and used. To update the repo in the  vendor directory, run'
        " 'bazel vendor' with the directory flag",
        stderr,
    )
    _, stdout, _ = self.RunBazel(['info', 'output_base'])
    self.assertFalse(os.path.islink(stdout[0] + '/external/bbb~1.0'))

    # Assert vendoring again solves the problem
    self.RunBazel(['vendor', '--vendor_dir=vendor'])
    self.RunBazel(['clean', '--expunge'])
    _, _, stderr = self.RunBazel(
        ['build', '@justRepo//:all', '--vendor_dir=vendor']
    )
    self.assertNotIn(
        "WARNING: <builtin>: Vendored repository '_main~ext~justRepo' is"
        ' out-of-date. The up-to-date version will be fetched into the external'
        ' cache and used. To update the repo in the  vendor directory, run'
        " 'bazel vendor' with the directory flag",
        stderr,
    )


if __name__ == '__main__':
  absltest.main()
