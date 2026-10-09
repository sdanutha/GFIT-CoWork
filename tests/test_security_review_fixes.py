from pathlib import Path


def test_docker_env_log_obfuscates_password_and_secret_names():
    src = Path("docker_init.bash").read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if l.startswith("export ENV_OBFUSCATE_PART="))

    assert "PASSWORD" in line
    assert "SECRET" in line
    assert "TOKEN" in line
    assert "API" in line
    assert "KEY" in line







