from __future__ import annotations

import textwrap
from pathlib import Path

from onebot_adapter.config import (
    Config,
    LogConfig,
    OneBotConfig,
    ServerConfig,
)


def _write_config(path: Path, content: str) -> Path:
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


class TestConfigDefaults:
    def test_onebot_defaults(self) -> None:
        c = OneBotConfig()
        assert c.ws_url == "ws://127.0.0.1:3001"
        assert c.access_token == ""
        assert c.reconnect_interval == 3.0

    def test_server_defaults(self) -> None:
        c = ServerConfig()
        assert c.host == "127.0.0.1"
        assert c.port == 8080
        assert c.ws_path == "/"

    def test_log_defaults(self) -> None:
        c = LogConfig()
        assert c.level == "INFO"

    def test_config_is_mutable(self) -> None:
        c = OneBotConfig()
        c.ws_url = "x"
        assert c.ws_url == "x"


class TestConfigLoad:
    def test_no_file_returns_defaults(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ONEBOT_ADAPTER_CONFIG", raising=False)
        config = Config.load(None)
        assert config.onebot.ws_url == "ws://127.0.0.1:3001"
        assert config.server.port == 8080
        assert config.log.level == "INFO"

    def test_load_full_config(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path / "config.toml",
            """
            [onebot]
            ws_url = "ws://10.0.0.1:6700"
            access_token = "secret"
            reconnect_interval = 5.0

            [server]
            host = "0.0.0.0"
            port = 9090
            ws_path = "/w"

            [log]
            level = "DEBUG"
            """,
        )
        config = Config.load(str(path))
        assert config.onebot.ws_url == "ws://10.0.0.1:6700"
        assert config.onebot.access_token == "secret"
        assert config.onebot.reconnect_interval == 5.0
        assert config.server.host == "0.0.0.0"
        assert config.server.port == 9090
        assert config.server.ws_path == "/w"
        assert config.log.level == "DEBUG"

    def test_partial_config_uses_defaults_for_missing(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path / "config.toml",
            """
            [onebot]
            ws_url = "ws://1.2.3.4:5"
            """,
        )
        config = Config.load(str(path))
        assert config.onebot.ws_url == "ws://1.2.3.4:5"
        assert config.onebot.access_token == ""
        assert config.server.port == 8080
        assert config.log.level == "INFO"

    def test_empty_file_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "config.toml"
        path.write_text("", encoding="utf-8")
        config = Config.load(str(path))
        assert config.onebot.ws_url == "ws://127.0.0.1:3001"
        assert config.server.port == 8080

    def test_env_var_resolves_path(self, tmp_path: Path, monkeypatch) -> None:
        path = _write_config(
            tmp_path / "my_config.toml",
            """
            [server]
            port = 3000
            """,
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ONEBOT_ADAPTER_CONFIG", str(path))
        config = Config.load(None)
        assert config.server.port == 3000

    def test_explicit_path_overrides_env(self, tmp_path: Path, monkeypatch) -> None:
        env_path = _write_config(
            tmp_path / "env_config.toml",
            """
            [server]
            port = 1111
            """,
        )
        explicit_path = _write_config(
            tmp_path / "explicit_config.toml",
            """
            [server]
            port = 2222
            """,
        )
        monkeypatch.setenv("ONEBOT_ADAPTER_CONFIG", str(env_path))
        config = Config.load(str(explicit_path))
        assert config.server.port == 2222

    def test_default_config_toml_in_cwd(self, tmp_path: Path, monkeypatch) -> None:
        _write_config(
            tmp_path / "config.toml",
            """
            [server]
            port = 7777
            """,
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("ONEBOT_ADAPTER_CONFIG", raising=False)
        config = Config.load(None)
        assert config.server.port == 7777

    def test_path_object_accepted(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path / "config.toml",
            """
            [server]
            port = 4444
            """,
        )
        config = Config.load(path)
        assert config.server.port == 4444

    def test_nonexistent_explicit_path_returns_defaults(self, tmp_path: Path) -> None:
        config = Config.load(str(tmp_path / "nonexistent.toml"))
        assert config.server.port == 8080
        assert config.onebot.ws_url == "ws://127.0.0.1:3001"

    def test_nonexistent_env_path_returns_defaults(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ONEBOT_ADAPTER_CONFIG", str(tmp_path / "nope.toml"))
        config = Config.load(None)
        assert config.server.port == 8080

    def test_extra_keys_in_section_ignored(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path / "config.toml",
            """
            [onebot]
            ws_url = "ws://x:1"
            unknown_field = "ignored"

            [server]
            port = 99
            extra = true
            """,
        )
        config = Config.load(str(path))
        assert config.onebot.ws_url == "ws://x:1"
        assert config.server.port == 99

    def test_extra_top_level_section_ignored(self, tmp_path: Path) -> None:
        path = _write_config(
            tmp_path / "config.toml",
            """
            [onebot]
            ws_url = "ws://x:1"

            [unknown_section]
            foo = "bar"
            """,
        )
        config = Config.load(str(path))
        assert config.onebot.ws_url == "ws://x:1"


class TestConfigResolutionPriority:
    def test_explicit_arg_beats_env_beats_cwd(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _write_config(
            tmp_path / "config.toml",
            """
            [server]
            port = 100
            """,
        )
        env_path = _write_config(
            tmp_path / "env.toml",
            """
            [server]
            port = 200
            """,
        )
        explicit_path = _write_config(
            tmp_path / "explicit.toml",
            """
            [server]
            port = 300
            """,
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ONEBOT_ADAPTER_CONFIG", str(env_path))

        # explicit wins
        assert Config.load(str(explicit_path)).server.port == 300
        # env wins over cwd
        assert Config.load(None).server.port == 200
        # cwd when no env
        monkeypatch.delenv("ONEBOT_ADAPTER_CONFIG", raising=False)
        assert Config.load(None).server.port == 100
