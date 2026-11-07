import unittest

from reset_task_mount import TARGET, task_mounts


class TaskMountTest(unittest.TestCase):
    def line(self, target=TARGET, device='/dev/loop0', number='7:0', root='/'):
        return f'292 80 {number} {root} {target} rw,noatime - ext4 {device} rw'

    def test_exact_task_mount(self):
        row = self.line()
        self.assertEqual(task_mounts(row, '/dev/loop0', '7:0'), [row])

    def test_unrelated_mount_is_ignored(self):
        self.assertEqual(task_mounts(self.line('/other', '/dev/sdb', '8:16'),
                                     '/dev/loop0', '7:0'), [])

    def test_task_device_elsewhere_is_rejected(self):
        with self.assertRaises(RuntimeError):
            task_mounts(self.line('/other'), '/dev/loop0', '7:0')

    def test_wrong_source_or_bind_root_is_rejected(self):
        for row in (self.line(device='/dev/sdb'), self.line(number='8:16'),
                    self.line(root='/subdir')):
            with self.subTest(row=row), self.assertRaises(RuntimeError):
                task_mounts(row, '/dev/loop0', '7:0')

    def test_stacked_mount_is_rejected(self):
        with self.assertRaises(RuntimeError):
            task_mounts(self.line() + '\n' + self.line(), '/dev/loop0', '7:0')


if __name__ == '__main__':
    unittest.main()
