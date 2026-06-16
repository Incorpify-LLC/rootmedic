"""Tests for demo.py scenarios — unit tests with mocked Loki."""

from __future__ import annotations

from unittest import mock

import pytest

import demo


class TestScenariosDict:
    """Verify SCENARIOS dict has all required scenarios with correct structure."""

    def test_all_four_scenarios_exist(self):
        expected_keys = {"service_crash", "oom_kill", "disk_full", "connection_refused"}
        assert set(demo.SCENARIOS.keys()) == expected_keys

    def test_each_scenario_has_required_fields(self):
        for key, scenario in demo.SCENARIOS.items():
            assert "name" in scenario
            assert "log_message" in scenario
            assert "unit" in scenario
            assert "expected_commands" in scenario
            assert "description" in scenario
            assert isinstance(scenario["expected_commands"], list)
            assert len(scenario["expected_commands"]) > 0

    def test_service_crash_scenario(self):
        sc = demo.SCENARIOS["service_crash"]
        assert sc["unit"] == "nginx.service"
        assert "systemctl restart nginx" in sc["expected_commands"]
        assert "signal 9" in sc["log_message"] or "exited" in sc["log_message"]

    def test_oom_kill_scenario(self):
        sc = demo.SCENARIOS["oom_kill"]
        assert sc["unit"] == "java.service"
        assert "systemctl restart java.service" in sc["expected_commands"]
        assert "sync && echo 3 > /proc/sys/vm/drop_caches" in sc["expected_commands"]
        assert "Out of memory" in sc["log_message"] or "Killed process" in sc["log_message"]

    def test_disk_full_scenario(self):
        sc = demo.SCENARIOS["disk_full"]
        assert sc["unit"] == "systemd-journald.service"
        assert "journalctl --vacuum-size=200M" in sc["expected_commands"]
        assert "apt-get clean" in sc["expected_commands"]
        assert "no space left" in sc["log_message"].lower()

    def test_connection_refused_scenario(self):
        sc = demo.SCENARIOS["connection_refused"]
        assert sc["unit"] == "nginx.service"
        assert "systemctl restart nginx" in sc["expected_commands"]
        assert "Connection refused" in sc["log_message"]


class TestVerifyHealing:
    """Test verify_healing function."""

    def test_exact_match_passes(self):
        ok, msg = demo.verify_healing("service_crash", ["systemctl restart nginx"])
        assert ok is True
        assert "All expected commands executed" in msg

    def test_extra_commands_still_passes(self):
        ok, msg = demo.verify_healing(
            "oom_kill",
            [
                "systemctl restart java.service",
                "sync && echo 3 > /proc/sys/vm/drop_caches",
                "extra command",
            ],
        )
        assert ok is True

    def test_missing_command_fails(self):
        ok, msg = demo.verify_healing("oom_kill", ["systemctl restart java.service"])
        assert ok is False
        assert "Missing expected commands" in msg
        assert "drop_caches" in msg

    def test_wrong_command_fails(self):
        ok, msg = demo.verify_healing("disk_full", ["wrong command"])
        assert ok is False
        assert "Missing expected commands" in msg

    def test_empty_commands_fails(self):
        ok, msg = demo.verify_healing("disk_full", [])
        assert ok is False


class TestPushLogToLoki:
    """Test push_log_to_loki with mocked requests."""

    @mock.patch("demo.requests.post")
    def test_push_log_to_loki_success(self, mock_post):
        mock_post.return_value = mock.MagicMock(status_code=204, raise_for_status=mock.Mock())

        result = demo.push_log_to_loki("test message", "nginx.service", "test-host")

        assert result is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == demo.LOKI_PUSH_URL
        assert kwargs["json"]["streams"][0]["stream"]["job"] == "systemd-journal"
        assert kwargs["json"]["streams"][0]["stream"]["host"] == "test-host"
        assert kwargs["json"]["streams"][0]["stream"]["systemd_unit"] == "nginx.service"
        assert kwargs["json"]["streams"][0]["values"][0][1] == "test message"

    @mock.patch("demo.requests.post")
    def test_push_log_to_loki_failure(self, mock_post):
        import requests
        mock_post.side_effect = requests.RequestException("Connection refused")

        result = demo.push_log_to_loki("test message", "nginx.service")

        assert result is False


class TestRunScenario:
    """Test run_scenario with mocked dependencies."""

    @mock.patch("demo.push_log_to_loki")
    @mock.patch("demo.run_agent_auto_apply")
    def test_run_scenario_success(self, mock_agent, mock_push):
        mock_push.return_value = True
        mock_agent.return_value = [
            {
                "apply_result": {
                    "status": "applied",
                    "applied_commands": ["systemctl restart nginx"],
                }
            }
        ]

        result = demo.run_scenario("service_crash", force_apply=True)

        assert result["passed"] is True
        assert result["scenario"] == "service_crash"
        assert len(result["results"]) == 1

    @mock.patch("demo.push_log_to_loki")
    @mock.patch("demo.run_agent_auto_apply")
    def test_run_scenario_push_fails(self, mock_agent, mock_push):
        mock_push.return_value = False

        result = demo.run_scenario("service_crash", force_apply=True)

        assert result["passed"] is False
        assert "Failed to inject log" in result["error"]

    @mock.patch("demo.push_log_to_loki")
    @mock.patch("demo.run_agent_auto_apply")
    def test_run_scenario_apply_fails(self, mock_agent, mock_push):
        mock_push.return_value = True
        mock_agent.return_value = [
            {
                "apply_result": {
                    "status": "rolled_back",
                    "failed_command": "systemctl restart nginx",
                }
            }
        ]

        result = demo.run_scenario("service_crash", force_apply=True)

        assert result["passed"] is False
        assert "Apply failed" in result["errors"][0]


class TestRunAllScenarios:
    """Test run_all_scenarios orchestration."""

    @mock.patch("demo.start_stack")
    @mock.patch("demo.stop_stack")
    @mock.patch("demo.run_scenario")
    def test_run_all_scenarios_calls_each(self, mock_run_scenario, mock_stop, mock_start):
        mock_start.return_value = True
        mock_stop.return_value = True
        mock_run_scenario.side_effect = [
            {"scenario": "service_crash", "passed": True, "errors": []},
            {"scenario": "oom_kill", "passed": True, "errors": []},
            {"scenario": "disk_full", "passed": True, "errors": []},
            {"scenario": "connection_refused", "passed": True, "errors": []},
        ]

        result = demo.run_all_scenarios(force_apply=True)

        assert result["overall"] is True
        assert len(result["scenarios"]) == 4
        assert mock_run_scenario.call_count == 4

    @mock.patch("demo.start_stack")
    @mock.patch("demo.stop_stack")
    @mock.patch("demo.run_scenario")
    def test_run_all_scenarios_fails_fast_on_stack_failure(self, mock_run_scenario, mock_stop, mock_start):
        mock_start.return_value = False

        result = demo.run_all_scenarios(force_apply=True)

        assert result["overall"] is False
        assert "Failed to start stack" in result["error"]
        mock_run_scenario.assert_not_called()


class TestMainCLI:
    """Test CLI argument parsing."""

    def test_dry_run_lists_scenarios(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["demo.py", "--dry-run"])
        # main() returns 0 for --dry-run, doesn't raise SystemExit
        result = demo.main()
        assert result == 0
        out = capsys.readouterr().out
        assert "service_crash" in out
        assert "oom_kill" in out
        assert "disk_full" in out
        assert "connection_refused" in out

    def test_help_shows_options(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.argv", ["demo.py", "--help"])
        with pytest.raises(SystemExit) as exc_info:
            demo.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "--scenario" in out
        assert "--force-apply" in out
        assert "--dry-run" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])