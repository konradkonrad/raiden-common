# pylint: disable=wrong-import-position,redefined-outer-name,unused-wildcard-import,wildcard-import
# type: ignore

from gevent import monkey

monkey.patch_all(subprocess=False, thread=False)

# isort:split

import aiortc_pyav_stub

# Install the av replacement stub to make sure we catch possible version
# upgrade breakages
aiortc_pyav_stub.install_as_av()

# isort: split

import pkgutil
import pytest

# Register pytest assert rewriting on all submodules of `raiden/tests/utils`.
# This is necessary due to our split fixture setup since pytest doesn't detect these
# imports automatically.
for module_info in pkgutil.iter_modules(["raiden/tests/utils"]):
    pytest.register_assert_rewrite(f"raiden.tests.utils.{module_info.name}")

# isort:split

import asyncio
import contextlib
import datetime
import os
import signal
import subprocess
import sys
import time

import gevent
import structlog
from gevent import Timeout

from raiden_common.constants import (
    HIGHEST_SUPPORTED_GETH_VERSION,
    HIGHEST_SUPPORTED_PARITY_VERSION,
    LOWEST_SUPPORTED_GETH_VERSION,
    LOWEST_SUPPORTED_PARITY_VERSION,
    EthClient,
)
from raiden_common.log_config import configure_logging
from raiden_common.network.transport.matrix.rtc.aiogevent import yield_future
from raiden_common.network.transport.matrix.rtc.utils import (
    ASYNCIO_LOOP_RUNNING_TIMEOUT,
    setup_asyncio_event_loop,
)
from raiden_common.tests.fixtures.blockchain import *  # noqa: F401,F403
from raiden_common.tests.fixtures.variables import *  # noqa: F401,F403
from raiden_common.tests.utils.transport import make_requests_insecure
from raiden_common.utils.cli import LogLevelConfigType
from raiden_common.utils.debugging import enable_monitoring_signal
from raiden_common.utils.ethereum_clients import VersionSupport, is_supported_client

log = structlog.get_logger()


def pytest_addoption(parser):
    parser.addoption(
        "--blockchain-type", choices=[client.value for client in EthClient], default="geth"
    )

    parser.addoption(
        "--log-config", action="store", default=None, help="Configure tests log output"
    )

    parser.addoption(
        "--plain-log",
        action="store_true",
        default=False,
        help="Do not colorize console log output",
    )

    parser.addoption(
        "--base-port",
        action="store",
        default=8500,
        type=int,
        help="Base port number to use for tests.",
    )

    parser.addoption("--profiler", default=None, choices=["flamegraph-trace"])

    # The goal here is to ensure the test runner will print something to the
    # stdout, this should be done frequently enough for the runner to /not/ get
    # killed by the CI. The settings below are defined in such a way to
    # guarantee that the test fails before the CI kill the runner.
    #
    # When something is printed depends on the verbosity used. If the tests are
    # executed with verbosity zero (the default), the only phase that prints to
    # the stdout is pytest_runtest_call.
    #
    # Consider the following:
    #
    # 1. test1.setup
    # 2. test1.call
    # 3. test1.teardown
    # 4. test2.setup
    # 5. test2.call
    # 6. test2.teardown
    #
    # From the start of step 3 until the end of step 5 there will be no output,
    # which is a full test cycle. Because of this, the settings below are
    # define in terms of their addition being smaller than the CI settings.
    #
    # Higher verbosities change the analysis above, however this is set for the
    # worst case.

    timeout_limit_setup_and_call_help = (
        "This setting defines the timeout in seconds for the setup *and* call "
        "phases of a test. Every test will be allowed to use at most "
        "`timeout_limit_setup_and_call` seconds to complete these phases. This "
        "setting together with the timeout_limit_teardown defines the total "
        "runtime for a single test. The total timeout must be lower than the no "
        "output timeout of the continuous integration."
    )
    parser.addini("timeout_limit_for_setup_and_call", timeout_limit_setup_and_call_help)

    timeout_limit_teardown_help = (
        "This setting defines the timeout in seconds for the teardown phase. It "
        "must be a non-zero value to allow for proper cleanup of fixtures. This "
        "setting together with the timeout_limit_setup_and_call defines the "
        "total runtime for a single test. The total timeout must be lower than "
        "the no output timeout of the continuous integration."
    )
    parser.addini("timeout_limit_teardown", timeout_limit_teardown_help)


@pytest.fixture(autouse=True, scope="session")
def check_geth_version_for_tests(blockchain_type):
    if blockchain_type != "geth":
        return

    geth_version_string, _ = subprocess.Popen(
        ["geth", "version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).communicate()
    supported, _, our_version = is_supported_client(geth_version_string.decode())
    if supported is VersionSupport.UNSUPPORTED:
        pytest.exit(
            f"You are trying to run tests with an unsupported GETH version. "
            f"Your Version: {our_version} "
            f"Min Supported Version {LOWEST_SUPPORTED_GETH_VERSION} "
            f"Max Supported Version {HIGHEST_SUPPORTED_GETH_VERSION}"
        )


@pytest.fixture(autouse=True, scope="session")
def check_parity_version_for_tests(blockchain_type):
    if blockchain_type != "parity":
        return

    parity_version_string, _ = subprocess.Popen(
        ["openethereum", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).communicate()
    supported, _, our_version = is_supported_client(parity_version_string.decode())
    if supported is VersionSupport.UNSUPPORTED:
        pytest.exit(
            f"You are trying to run tests with an unsupported PARITY version. "
            f"Your Version: {our_version} "
            f"Min Supported Version {LOWEST_SUPPORTED_PARITY_VERSION} "
            f"Max Supported Version {HIGHEST_SUPPORTED_PARITY_VERSION}"
        )


@pytest.fixture(scope="session", autouse=True)
def auto_enable_monitoring_signal():
    enable_monitoring_signal()


@pytest.fixture(scope="session", autouse=True)
def enable_greenlet_debugger(request):
    """Enable the pdb debugger for gevent's greenlets.

    This extends the flag `--pdb` from pytest to enable debugging of greenlets
    which have raised an exception to the top-level. Without this hook the
    exception raised in a greenlet is printed, and the thread state is
    discarded. Making it impossible to execute a post_mortem
    """
    if request.config.option.usepdb:
        import bdb
        import pdb

        # Do not run pdb again if an exception hits top-level for a second
        # greenlet and the previous pdb session is still running
        enabled = False
        hub = gevent.get_hub()

        def debugger(context, type_, value, tb):
            # Always print the exception, because once the pdb REPL is started
            # we cannot retrieve it with `sys.exc_info()`.
            #
            # Using gevent's hub print_exception because it properly handles
            # corner cases.
            hub.print_exception(context, type_, value, tb)

            # Don't enter nested sessions
            # Ignore exceptions used to quit the debugger / interpreter
            nonlocal enabled
            if not enabled and type_ not in (bdb.BdbQuit, KeyboardInterrupt):
                enabled = True
                pdb.post_mortem()  # pylint: disable=no-member
                enabled = False

        # Hooking the debugger on the hub error handler. Exceptions that are
        # not handled on a given greenlet are forwarded to the
        # parent.handle_error, until the hub is reached.
        #
        # Note: for this to work properly, it's really important to use
        # gevent's spawn function.
        hub.handle_error = debugger


@pytest.fixture(autouse=True, scope="session")
def profiler(request):
    profiler = None

    if request.config.option.profiler == "flamegraph-trace":
        from raiden_common.utils.profiling.sampler import FlameGraphCollector, TraceSampler

        now = datetime.datetime.now()
        stack_path = os.path.join("/tmp", f"{now:%Y%m%d_%H%M}_stack.data")
        stack_stream = open(stack_path, "w")
        flame = FlameGraphCollector(stack_stream)
        profiler = TraceSampler(flame)

    yield

    if profiler is not None:
        profiler.stop()


@pytest.fixture(autouse=True)
def logging_level(request, logs_storage):
    """Configure the structlog level.

    For integration tests this also sets the geth verbosity.
    """
    # disable pytest's built in log capture, otherwise logs are printed twice
    request.config.option.showcapture = "stdout"

    if request.config.option.log_cli_level:
        level = request.config.option.log_cli_level
    elif request.config.option.verbose > 3:
        level = "DEBUG"
    elif request.config.option.verbose > 1:
        level = "INFO"
    else:
        level = "WARNING"

    if request.config.option.log_config:
        config_converter = LogLevelConfigType()
        logging_levels = config_converter.convert(
            value=request.config.option.log_config, param=None, ctx=None
        )
    else:
        logging_levels = {"": level}

    # configure_logging requires the path to exist
    os.makedirs(logs_storage, exist_ok=True)

    time = datetime.datetime.utcnow().isoformat()
    debug_path = os.path.join(logs_storage, f"raiden-debug_{time}.log")

    configure_logging(
        logging_levels,
        colorize=not request.config.option.plain_log,
        log_file=request.config.option.log_file,
        cache_logger_on_first_use=False,
        debug_log_file_path=debug_path,
    )
    log.info("Running test", nodeid=request.node.nodeid)


@pytest.fixture(scope="session", autouse=True)
def dont_exit_pytest():
    """Raiden will quit on any unhandled exception.

    This allows the test suite to finish in case an exception is unhandled.
    """
    gevent.get_hub().NOT_ERROR = (gevent.GreenletExit, SystemExit)


@pytest.fixture(scope="session", autouse=True)
def insecure_tls():
    make_requests_insecure()


@contextlib.contextmanager
def timeout_for_setup_and_call(item):
    """Sets a timeout up to `item.remaining_timeout`, if the timeout is reached
    an exception is raised, otherwise the amount of time used by the run is
    deducted from the `item.remaining_timeout`.

    This function is only used for setup and call, which share the same
    timeout. The teardown must have a separate timeout, because even if either
    the setup or the call timedout the teardown must still be called to do
    fixture clean up.
    """

    def report():
        gevent.util.print_run_info()
        raise Exception(f"Setup and Call timeout >{item.timeout_setup_and_call}s")

    def handler(signum, frame):  # pylint: disable=unused-argument
        report()

    # The handler must be installed before the timer is set, otherwise it is
    # possible for the default handler to be used, which would not raise our
    # exception. This can happen if the setup phase uses most of the available
    # time, leaving just enough for the call to install the new timer and get
    # the event.
    signal.signal(signal.SIGALRM, handler)

    # Negative values are invalid and will raise an exception.
    #
    # This is not a problem because:
    # - pytest_runtest_setup is the first called, it follows the call to
    # pytest_runtest_protocol, which validates and sets the timeout values.
    # - pytest_runtest_call is the second call, and it will only run if the
    # setup was succesful, i.e. a timeout did not happen. This implies that
    # the remaining_timeout is positive.
    item.remaining_timeout = item.timeout_setup_and_call

    started_at = time.time()
    signal.setitimer(signal.ITIMER_REAL, item.remaining_timeout)

    yield

    # The timer must be disabled *before* the handler is unset, otherwise it is
    # possible for a timeout event to be handled by the default handler.
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)

    elapsed = time.time() - started_at

    # It is possible for elapsed to be negative, this can happen if the
    # time.time clock and the clock used by the signal are different. To
    # guarantee the next iteration will only have positive values, raise an
    # exception, failing the setup and skipping the call.
    item.remaining_timeout -= elapsed
    if item.remaining_timeout < 0:
        report()


def timeout_from_marker(marker):
    """Return None or the value of the timeout."""
    timeout = None

    if marker is not None:
        if len(marker.args) == 1 and len(marker.kwargs) == 0:
            timeout = marker.args[0]
        elif len(marker.args) == 0 and len(marker.kwargs) == 1 and "timeout" in marker.kwargs:
            timeout = marker.kwargs["timeout"]
        else:
            raise Exception(
                "Invalid marker. It must have only one argument for the "
                "timeout, which may be named or not."
            )

    return timeout


def set_item_timeouts(item):
    """Limit the tests runtime

    The timeout is read from the following places (last one takes precedence):
    * setup.cfg (ini).
    * pytest timeout marker at the specific test.
    """
    timeout_limit_setup_and_call = item.config.getini("timeout_limit_for_setup_and_call")

    if timeout_limit_setup_and_call == "":
        raise RuntimeError(
            "timeout_limit_for_setup_and_call must be set in section "
            "'tool.pytest.ini_options' in pyproject.toml"
        )

    timeout_limit_setup_and_call = float(timeout_limit_setup_and_call)

    timeout_limit_teardown = item.config.getini("timeout_limit_teardown")

    if timeout_limit_teardown == "":
        raise RuntimeError(
            "timeout_limit_teardown must be set in section "
            "'tool.pytest.ini_options' in pyproject.toml"
        )

    timeout_limit_teardown = float(timeout_limit_teardown)

    timeout_teardown = timeout_limit_teardown

    # There is no marker to configure the teardown timeout
    marker = item.get_closest_marker("timeout")
    timeout_setup_and_call = timeout_from_marker(marker) or timeout_limit_setup_and_call

    if timeout_setup_and_call > timeout_limit_setup_and_call:
        raise RuntimeError(
            f"Invalid value for the timeout marker {timeout_setup_and_call}. This "
            f"value must be smaller than {timeout_limit_setup_and_call}. This is "
            f"necessary because the runtime of a test has to be synchronized with "
            f"the continuous integration output timeout, e.g. no_output_timeout "
            f"in CircleCI. If the timeout is larger than that value the whole "
            f"build will be killed because of the lack of output, this will not "
            f"produce a failure report nor log files, which makes the build run "
            f"useless."
        )

    if timeout_setup_and_call <= 0:
        raise RuntimeError("timeout must not be negative")

    if timeout_teardown <= 0:
        raise RuntimeError("timeout_limit_teardown must not be negative")

    item.timeout_setup_and_call = timeout_setup_and_call
    item.timeout_teardown = timeout_teardown


@pytest.hookimpl()
def pytest_runtest_protocol(item, nextitem):  # pylint:disable=unused-argument
    # The timeouts cannot be configured in the pytest_runtest_setup, because if
    # the required value is not set, an exception is raised, but then it is
    # swallowed by the `CallInfo.from_call`
    set_item_timeouts(item)


# Pytest's test protocol is defined by `pytest.runner:pytest_runtest_protocol`,
# it has three steps where exceptions can safely be raised at:
#
# - setup
# - call
# - teardown
#
# Below one hook for each of the steps is used. This is necessary to guarantee
# that a Timeout exception will be raised only inside these steps that handle
# exceptions, otherwise the test executor could be killed by the timeout
# exception.


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_setup(item):

    with timeout_for_setup_and_call(item):
        yield


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_call(item):
    """More feedback for flaky tests.

    In verbose mode this outputs 'FLAKY' every time a test marked as flaky fails.

    This doesn't happen when:

    - Tests are executed under xdist.
    - The fixture setup fails.
    """

    # pytest_runtest_call is only called if the test setup finished
    # succesfully, this means the code below may not be executed if the fixture
    # setup has timedout already.
    with timeout_for_setup_and_call(item):
        outcome = yield

        did_fail = isinstance(outcome._excinfo, tuple) and isinstance(
            outcome._excinfo[1], BaseException
        )
        is_xdist = "PYTEST_XDIST_WORKER" in os.environ
        is_flaky_test = item.get_closest_marker("flaky") is not None

        should_print = (
            did_fail and item.config.option.verbose > 0 and is_flaky_test and not is_xdist
        )

        if should_print:
            capmanager = item.config.pluginmanager.getplugin("capturemanager")
            with capmanager.global_and_fixture_disabled():
                item.config.pluginmanager.get_plugin("terminalreporter")._tw.write(
                    "FLAKY ", yellow=True
                )


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_teardown(item):
    # The teardown must be executed to clear up the fixtures, even if the
    # fixture setup itself failed. Because of this the timeout for the teardown
    # is different than the timeout for the setup and call.

    def report():
        gevent.util.print_run_info()
        raise Exception(
            f"Teardown timeout >{item.timeout_setup_and_call}s. This must not happen, when "
            f"the teardown times out not all finalizers got a chance to run. This "
            f"means not all fixtures are cleaned up, which can make subsequent "
            f"tests flaky. This would be the case for pending greenlets which are "
            f"not cleared by previous run."
        )

    def handler(signum, frame):  # pylint: disable=unused-argument
        report()

    # The order of the signal setup/teardown is important, check
    # timeout_for_setup_and_call for details
    signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, item.timeout_teardown)

    yield

    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, signal.SIG_DFL)


if sys.platform == "darwin":
    # On macOS the default temp directory base path is very long (a privacy feature).
    # Since ipc path length is limited to 104/108 chars on macOS/linux and geth uses ipc sockets
    # that are located below the per-test tempdir we override the pytest basetemp dir (it it's not
    # set by the user) to point it to the public /tmp dir in order to produce shorter paths.

    def pytest_configure(config) -> None:
        if config.option.basetemp is None:
            config.option.basetemp = f"/tmp/pytest-of-{os.getlogin():.6s}-{os.getpid()}"


@pytest.fixture(autouse=True)
def asyncio_loop(request):
    if request.node.get_closest_marker("asyncio") is not None:
        event_loop = setup_asyncio_event_loop(RuntimeError)
        yield
        log.debug("Killing asyncio loop")
        if event_loop.is_running():
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            log.debug("Canceling outstanding tasks", tasks=tasks)
            for task in tasks:
                task.cancel()
            yield_future(asyncio.gather(*tasks, return_exceptions=True))

        event_loop.call_soon_threadsafe(event_loop.stop)
        with Timeout(ASYNCIO_LOOP_RUNNING_TIMEOUT, RuntimeError):
            while event_loop.is_running():
                gevent.sleep(0.05)
        event_loop.close()
        with Timeout(ASYNCIO_LOOP_RUNNING_TIMEOUT, RuntimeError):
            while not event_loop.is_closed():
                gevent.sleep(0.05)

    else:
        log.debug("NO ASYNC IO MARKER FOUND")
        yield
