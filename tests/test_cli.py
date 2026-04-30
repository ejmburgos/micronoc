import json

from app import cli


def test_cli_health_prints_ok(capsys) -> None:
    exit_code = cli.main(["health"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {"status": "ok"}


def test_cli_without_command_prints_help(capsys) -> None:
    exit_code = cli.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "usage:" in captured.out


def test_cli_serve_invokes_uvicorn(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(app: str, host: str, port: int, reload: bool) -> None:
        calls.append(
            {
                "app": app,
                "host": host,
                "port": port,
                "reload": reload,
            }
        )

    monkeypatch.setattr("uvicorn.run", fake_run)

    exit_code = cli.main(["serve", "--host", "0.0.0.0", "--port", "9000", "--reload"])

    assert exit_code == 0
    assert calls == [
        {
            "app": "app.main:app",
            "host": "0.0.0.0",
            "port": 9000,
            "reload": True,
        }
    ]
