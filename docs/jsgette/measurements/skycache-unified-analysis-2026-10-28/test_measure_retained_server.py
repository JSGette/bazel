import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import measure_retained_server as runner


class RetainedServerTest(unittest.TestCase):
    def command(self, run, arm='fork-on'):
        return runner.persistent_command(Path('/bazel'), Path('/owned/binary'), Path(run),
                                         Path('/owned/cache') if arm == 'fork-on' else None,
                                         ['//:root'], 2048, 6, arm, Path('/owned/arm/output-base'))

    def test_startup_is_stable_across_repeats_without_batch_mode(self):
        first = self.command('/owned/arm/build-00')
        later = self.command('/owned/arm/build-06')
        self.assertEqual(first[:first.index('build')], later[:later.index('build')])
        self.assertNotIn('--batch', first)
        self.assertIn('--max_idle_secs=120', first[:first.index('build')])
        self.assertIn('--output_base=' + str(Path('/owned/arm/output-base')), first)
        self.assertIn(f'--host_jvm_args=-Xlog:gc*:file={Path("/owned/arm/server-gc.log")}:time,uptime,level,tags', first)
        self.assertIn('--profile=' + str(Path('/owned/arm/build-06/build.profile.gz')), later)

    def test_vanilla_never_receives_fork_options(self):
        command = self.command('/owned/arm/build-00', 'vanilla')
        self.assertFalse(any(flag.startswith(runner.comparison.CUSTOM_PREFIXES) for flag in command))
        self.assertIn('--remote_cache=', command)
        self.assertIn('--disk_cache=', command)
        self.assertIn('--jobs=6', command)

    def test_pid_ownership_requires_complete_argument_not_substring(self):
        output = Path('/owned/output-base').resolve()
        self.assertTrue(runner.owns_output_base(['java', '--output_base=' + str(output)], output))
        self.assertTrue(runner.owns_output_base(['java', '--output_base', str(output)], output))
        for arguments in [['--output_base=/owned/output-base-other'], ['--output_base'],
                          ['prefix--output_base=/owned/output-base'], ['--other=/owned/output-base']]:
            with self.subTest(arguments=arguments):
                self.assertFalse(runner.owns_output_base(arguments, output))

    def test_proc_stat_handles_spaces_and_parentheses_in_comm(self):
        text = '123 (a process ) name) S ' + ' '.join(str(index) for index in range(4, 53))
        self.assertEqual(runner.parse_proc_stat(text),
                         {'state': 'S', 'user_ticks': 14, 'system_ticks': 15, 'start_ticks': 22})

    def test_cpu_delta_rejects_restarts_and_does_not_invent_initial_cpu(self):
        before = {'identity': {'pid': 12, 'start_ticks': 4}, 'clock_ticks_per_second': 100,
                  'cpu_ticks': {'user_ticks': 100, 'system_ticks': 30}}
        after = copy.deepcopy(before)
        after['cpu_ticks'] = {'user_ticks': 140, 'system_ticks': 40}
        self.assertEqual(runner.server_cpu_delta(before, after), 0.5)
        self.assertIsNone(runner.server_cpu_delta(None, after))
        after['identity']['start_ticks'] = 5
        with self.assertRaises(ValueError):
            runner.server_cpu_delta(before, after)
        after['identity']['start_ticks'] = 4
        after['cpu_ticks']['user_ticks'] = 0
        with self.assertRaises(ValueError):
            runner.server_cpu_delta(before, after)

    def test_rss_pss_and_missing_fields_remain_distinct(self):
        sample = runner.kib_fields('Rss:  123 kB\nPss: 100 kB\n', ('Rss', 'Pss', 'VmHWM'))
        self.assertEqual(sample, {'Rss_kib': 123, 'Pss_kib': 100, 'VmHWM_kib': None})

    def test_heap_info_is_not_inferred_from_unknown_output(self):
        self.assertEqual(runner.heap_usage('garbage-first heap   total 131072K, used 12345K [0x0]'),
                         {'committed_heap_bytes': 131072 * 1024, 'used_heap_bytes': 12345 * 1024})
        self.assertIn('unavailable', runner.heap_usage('Heap format was not recognized'))

    def make_producer_states(self, root):
        workspace = root / 'workspace'
        workspace.mkdir()
        for name, arm in [('vanilla', 'vanilla'), ('current', 'fork-on')]:
            state = root / 'binary-state' / name
            for directory in ('bazel-user', 'repository-cache'):
                (state / directory).mkdir(parents=True)
            command = root / 'runs' / ('producer-' + arm) / 'command.json'
            command.parent.mkdir(parents=True)
            command.write_text(json.dumps({'cwd': str(workspace), 'argv': [
                '/bazel', '--output_user_root=' + str(state / 'bazel-user'), 'build',
                '--repository_cache=' + str(state / 'repository-cache')]}))
        return workspace

    def test_reuses_exact_producer_install_and_repository_paths_with_owned_output_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = self.make_producer_states(root)
            states = runner.producer_binary_states(workspace)
            self.assertEqual(states, {name: root / 'binary-state' / name for name in ('vanilla', 'current')})
            output = root / 'new-series/runs/fork-on/output-base'
            argv = runner.persistent_command(Path('/bazel'), states['current'], output.parent / 'build-00',
                                             root / 'new-cache', ['//:root'], 2048, 6, 'fork-on', output)
            self.assertIn('--output_user_root=' + str(states['current'] / 'bazel-user'), argv)
            self.assertIn('--repository_cache=' + str(states['current'] / 'repository-cache'), argv)
            self.assertIn('--output_base=' + str(output), argv)
            self.assertIn('--symlink_prefix=' + str(output.parent / 'links' / 'build-00-'), argv)

    def test_rejects_changed_producer_repository_cache_instead_of_inventing_fresh_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = self.make_producer_states(root)
            path = root / 'runs/producer-fork-on/command.json'
            command = json.loads(path.read_text())
            command['argv'][-1] = '--repository_cache=' + str(root / 'unrelated-cache')
            path.write_text(json.dumps(command))
            with self.assertRaisesRegex(ValueError, 'producer command'):
                runner.producer_binary_states(workspace)

    def test_rejects_missing_producer_install_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = self.make_producer_states(root)
            (root / 'binary-state/current/bazel-user').rmdir()
            with self.assertRaisesRegex(ValueError, 'existing producer'):
                runner.producer_binary_states(workspace)
    def test_shutdown_refuses_unverified_pid_before_invoking_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'output-base'
            (output / 'server').mkdir(parents=True)
            (output / 'server/server.pid.txt').write_text('123')
            with patch.object(runner, 'owned_server', side_effect=ValueError('wrong output base')), \
                    patch.object(runner, 'run_process') as command:
                with self.assertRaisesRegex(ValueError, 'wrong output base'):
                    runner.shutdown_server(root, ['/bazel'], output, None, root, {})
                command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
