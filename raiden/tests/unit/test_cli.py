import json
from functools import partial

import pytest
from click.testing import CliRunner

from raiden.constants import EthClient
from raiden.ui.cli import run
from raiden.utils.ethereum_clients import is_supported_client


@pytest.fixture
def cli_runner(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem():
        yield partial(runner.invoke, env={"HOME": str(tmp_path)})


def test_cli_version(cli_runner):
    result = cli_runner(run, ["version"])
    result_json = json.loads(result.output)
    result_expected_keys = {
        "raiden",
        "python_implementation",
        "python_version",
        "system",
        "architecture",
        "distribution",
    }
    assert result_expected_keys == result_json.keys()
    assert result.exit_code == 0


def test_check_json_rpc_geth():
    g1, client = is_supported_client("Geth/v1.7.3-unstable-e9295163/linux-amd64/go1.9.1")
    g2, _ = is_supported_client("Geth/v1.7.2-unstable-e9295163/linux-amd64/go1.9.1")
    g3, _ = is_supported_client("Geth/v1.8.2-unstable-e9295163/linux-amd64/go1.9.1")
    g4, _ = is_supported_client("Geth/v2.0.3-unstable-e9295163/linux-amd64/go1.9.1")
    g5, _ = is_supported_client("Geth/v11.55.86-unstable-e9295163/linux-amd64/go1.9.1")
    g6, _ = is_supported_client("Geth/v999.999.999-unstable-e9295163/linux-amd64/go1.9.1")
    assert client is EthClient.GETH
    assert all([g1, g2, g3, g4, g5, g6])

    b1, client = is_supported_client("Geth/v1.7.1-unstable-e9295163/linux-amd64/go1.9.1")
    b2, _ = is_supported_client("Geth/v0.7.1-unstable-e9295163/linux-amd64/go1.9.1")
    b3, _ = is_supported_client("Geth/v0.0.0-unstable-e9295163/linux-amd64/go1.9.1")
    b4, _ = is_supported_client("Geth/v0.0.0-unstable-e9295163/linux-amd64/go1.9.1")
    assert not client
    assert not any([b1, b2, b3, b4])


def test_check_json_rpc_parity():
    g1, client = is_supported_client(
        "Parity//v1.7.6-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    g2, _ = is_supported_client(
        "Parity//v1.7.7-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    g3, _ = is_supported_client(
        "Parity//v1.8.7-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    g4, _ = is_supported_client(
        "Parity//v2.9.7-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    g5, _ = is_supported_client(
        "Parity//v23.94.75-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    g6, _ = is_supported_client(
        "Parity//v99.994.975-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    assert client is EthClient.PARITY
    assert all([g1, g2, g3, g4, g5, g6])

    b1, client = is_supported_client(
        "Parity//v1.7.5-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    b2, _ = is_supported_client(
        "Parity//v1.5.1-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    b3, _ = is_supported_client(
        "Parity//v0.7.1-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    b4, _ = is_supported_client(
        "Parity//v0.8.7-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    b5, _ = is_supported_client(
        "Parity//v0.0.0-stable-19535333c-20171013/x86_64-linux-gnu/rustc1.20.0"
    )
    assert not client
    assert not any([b1, b2, b3, b4, b5])
