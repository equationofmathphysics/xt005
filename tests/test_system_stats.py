import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codexws_server import system_stats


class ServiceStatsTests(unittest.TestCase):
    def setUp(self):
        system_stats.last_service_cpu_sample = None

    def test_service_memory_uses_cgroup_current_value(self):
        with tempfile.TemporaryDirectory() as directory:
            cgroup = Path(directory)
            (cgroup / "memory.current").write_text("1610612736\n", encoding="utf-8")
            with patch("codexws_server.system_stats._service_cgroup_path", return_value=cgroup):
                self.assertEqual(system_stats.service_memory_bytes(), 1610612736)

    def test_service_memory_snapshot_separates_processes_and_file_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cgroup = Path(directory)
            (cgroup / "memory.current").write_text("2147483648\n", encoding="utf-8")
            (cgroup / "memory.stat").write_text(
                "anon 268435456\nfile 1879048192\ninactive_file 1073741824\n"
                "active_file 805306368\nslab_reclaimable 67108864\n",
                encoding="utf-8",
            )
            with (
                patch("codexws_server.system_stats._service_cgroup_path", return_value=cgroup),
                patch("codexws_server.system_stats.service_process_pss_bytes", return_value=314572800),
            ):
                snapshot = system_stats.service_memory_snapshot()
            self.assertEqual(snapshot["memory_bytes"], 2147483648)
            self.assertEqual(snapshot["working_set_bytes"], 314572800)
            self.assertEqual(snapshot["process_pss_bytes"], 314572800)
            self.assertEqual(snapshot["file_cache_bytes"], 1879048192)
            self.assertEqual(snapshot["reclaimable_bytes"], 1140850688)

    def test_service_working_set_falls_back_to_process_rss_when_stats_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            cgroup = Path(directory)
            (cgroup / "memory.current").write_text("536870912\n", encoding="utf-8")
            with (
                patch("codexws_server.system_stats._service_cgroup_path", return_value=cgroup),
                patch("codexws_server.system_stats.process_rss_bytes", return_value=33554432),
            ):
                self.assertEqual(system_stats.service_working_set_bytes(), 33554432)

    def test_service_cpu_is_reported_as_single_core_percent(self):
        samples = iter((1_000_000, 1_250_000))
        clocks = iter((10.0, 12.0))
        with (
            patch("codexws_server.system_stats._service_cpu_usage_usec", side_effect=samples),
            patch("codexws_server.system_stats.time.monotonic", side_effect=clocks),
        ):
            self.assertEqual(system_stats.current_service_cpu_percent(), 0.0)
            self.assertEqual(system_stats.current_service_cpu_percent(), 12.5)


if __name__ == "__main__":
    unittest.main()
