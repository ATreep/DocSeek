from pathlib import Path


def test_start_script_records_processes_and_prints_frontend_address():
    script = Path(__file__).parents[2].joinpath("start.sh").read_text(encoding="utf-8")
    assert "PID_FILE=\"$ROOT_DIR/.docseek-pids\"" in script
    assert "kill \"$pid\"" in script
    assert "uv run uvicorn backend.app.main:app" in script
    assert "http://localhost:$FRONTEND_PORT" in script
    assert 'cd "$ROOT_DIR"' in script
    assert 'npm run dev -- --host "$FRONTEND_HOST"' in script
    assert "wait_for_api" in script
    assert script.index("wait_for_api\n") < script.index('npm run dev -- --host "$FRONTEND_HOST"')
