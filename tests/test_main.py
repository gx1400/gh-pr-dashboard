from __future__ import annotations

import pytest

from gh_pr_dashboard import __version__
from gh_pr_dashboard.__main__ import main


def test_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_check_config_never_prints_tokens(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[[owners]]\nname = "a"\n[[owners]]\nname = "b"\ntoken_env = "B_TOKEN"\n')
    monkeypatch.setenv("PRD_CONFIG", str(cfg))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecret")
    monkeypatch.delenv("B_TOKEN", raising=False)
    assert main(["--check-config"]) == 1
    out = capsys.readouterr().out
    assert "a: token from env GITHUB_TOKEN: ok" in out
    assert "b: token from env B_TOKEN: MISSING" in out
    assert "supersecret" not in out


def test_config_error_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PRD_CONFIG", str(tmp_path / "missing.toml"))
    assert main([]) == 2
    assert "config error" in capsys.readouterr().err
