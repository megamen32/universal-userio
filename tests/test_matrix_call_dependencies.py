"""Matrix call extras must install dependencies absent from LiveKit metadata."""
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, supported by the project.
    from pip._vendor import tomli as tomllib

from packaging.requirements import Requirement


def test_matrix_call_extra_installs_verified_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    requirements = {
        item.name: item
        for item in map(Requirement, metadata["project"]["optional-dependencies"]["matrix-calls"])
    }
    verified = {"livekit": "1.1.20", "aiohttp": "3.14.4",
                "websockets": "15.0.1", "pydantic": "2.13.5"}
    missing = sorted(verified.keys() - requirements.keys())
    assert not missing, f"Matrix release environment would miss: {missing}"
    container = (root / "deploy/containers/requirements.txt").read_text().splitlines()
    for name, version in verified.items():
        assert requirements[name].marker is None
        assert version in requirements[name].specifier
        assert f"{name}=={version}" in container
