from __future__ import annotations

import pytest

from gh_pr_dashboard.config import ConfigError, TokenSource, load, owner_env_key


def write(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return {"PRD_CONFIG": str(path)}


def test_owner_env_key():
    assert owner_env_key("APRS-NET") == "GITHUB_TOKEN_APRS_NET"
    assert owner_env_key("gx1400") == "GITHUB_TOKEN_GX1400"


def test_owners_array_with_token_sources(tmp_path):
    secret = tmp_path / "wa0rc"
    secret.write_text("file-token\n")
    env = write(
        tmp_path,
        f"""
        refresh_seconds = 900
        exclude_repos = ["Gx1400/Old"]

        [[owners]]
        name = "gx1400"

        [[owners]]
        name = "APRS-NET"
        token_env = "APRS_TOKEN"

        [[owners]]
        name = "WA0RC"
        token_file = "{secret}"
        """,
    )
    env |= {"GITHUB_TOKEN": "default", "APRS_TOKEN": "aprs"}
    cfg = load(env)
    assert [o.name for o in cfg.owners] == ["gx1400", "APRS-NET", "WA0RC"]
    assert [o.token.read(env) for o in cfg.owners] == ["default", "aprs", "file-token"]
    assert cfg.refresh_seconds == 900
    assert cfg.exclude_repos == {"gx1400/old"}
    assert cfg.source.endswith("config.toml")


def test_per_owner_env_convention_beats_default(tmp_path):
    env = write(tmp_path, '[[owners]]\nname = "APRS-NET"\n')
    env |= {"GITHUB_TOKEN": "default", "GITHUB_TOKEN_APRS_NET": "own"}
    (owner,) = load(env).owners
    assert owner.token.read(env) == "own"


def test_default_token_file(tmp_path):
    tok = tmp_path / "tok"
    tok.write_text("from-file")
    env = write(tmp_path, f'default_token_file = "{tok}"\n[[owners]]\nname = "x"\n')
    assert load(env).owners[0].token.read(env) == "from-file"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('[[owners]]\nname = "x"\ntoken = "ghp_secret"\n', "not in the config"),
        ('[[owners]]\nname = "x"\ntoken_env = "A"\ntoken_file = "/b"\n', "not both"),
        ("refresh_seconds = 60\n", "at least one"),
        ('[[owners]]\nname = "x"\n[[owners]]\nname = "X"\n', "listed twice"),
        ("[[owners]]\ntoken_env = 'A'\n", "needs a name"),
        ('refresh_seconds = "soon"\n[[owners]]\nname = "x"\n', "integer"),
        ("owners = [", "config.toml"),
    ],
)
def test_invalid_configs(tmp_path, body, message):
    with pytest.raises(ConfigError, match=message):
        load(write(tmp_path, body))


def test_missing_explicit_config(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load({"PRD_CONFIG": str(tmp_path / "nope.toml")})


def test_env_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("gh_pr_dashboard.config.DEFAULT_CONFIG", str(tmp_path / "absent.toml"))
    env = {
        "OWNERS": "gx1400, weftmark ,",
        "GITHUB_TOKEN": "default",
        "GITHUB_TOKEN_WEFTMARK": "wm",
        "EXCLUDE_REPOS": "a/b",
        "REFRESH_SECONDS": "5",
        "PORT": "9000",
    }
    cfg = load(env)
    assert [o.token.read(env) for o in cfg.owners] == ["default", "wm"]
    assert cfg.refresh_seconds == 60  # clamped to the minimum
    assert cfg.port == 9000
    assert cfg.exclude_repos == {"a/b"}
    assert cfg.source == "env"


def test_env_mode_needs_owners(tmp_path, monkeypatch):
    monkeypatch.setattr("gh_pr_dashboard.config.DEFAULT_CONFIG", str(tmp_path / "absent.toml"))
    with pytest.raises(ConfigError, match="no owners"):
        load({})


def test_token_source_errors(tmp_path):
    with pytest.raises(ConfigError, match="empty or unset"):
        TokenSource(env="NOPE").read({})
    with pytest.raises(ConfigError, match="cannot read token file"):
        TokenSource(file=str(tmp_path / "missing")).read({})
    assert TokenSource(file="/x").describe() == "file /x"
    assert TokenSource(env="Y").describe() == "env Y"
