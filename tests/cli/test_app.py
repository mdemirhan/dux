from __future__ import annotations

import pytest

from diskanalysis.cli import app as cli_app


def test_windows_platform_exits_with_not_implemented(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_app.sys, "platform", "win32")

    with pytest.raises(cli_app.typer.Exit) as exc_info:
        cli_app.run(sample_config=True)

    assert exc_info.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Windows support is not implemented yet." in out
