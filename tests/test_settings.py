from pathlib import Path

from todomate_mcp.settings import environment_path, load_environment, load_firebase_api_key


def test_local_dotenv_loads_firebase_api_key(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TODOMATE_FIREBASE_API_KEY=key\nTODOMATE_EMAIL=ignored\n")
    assert load_firebase_api_key(dotenv, {}) == "key"


def test_environment_overrides_dotenv(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("# local secret\nTODOMATE_FIREBASE_API_KEY=old\n")
    assert load_firebase_api_key(dotenv, {"TODOMATE_FIREBASE_API_KEY": "new"}) == "new"


def test_load_environment_reads_dotenv_and_environment_overrides(tmp_path: Path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("TODOMATE_MCP_ACCESS_TOKEN=from-file\nTODOMATE_MCP_PORT=8000\n")

    assert load_environment(dotenv, {"TODOMATE_MCP_ACCESS_TOKEN": "from-env"}) == {
        "TODOMATE_MCP_ACCESS_TOKEN": "from-env",
        "TODOMATE_MCP_PORT": "8000",
    }


def test_environment_file_can_be_set_at_runtime(tmp_path: Path):
    dotenv = tmp_path / "persisted.env"
    dotenv.write_text("TODOMATE_MCP_PORT=8000\n")
    environ = {"TODOMATE_ENV_FILE": str(dotenv)}

    assert environment_path(environ) == dotenv
    assert load_environment(environ=environ)["TODOMATE_MCP_PORT"] == "8000"
