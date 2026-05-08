"""Exit code constants for the marivo CLI.

marivo uses exit codes exclusively to determine success/failure;
it does not parse stderr.
"""

EXIT_SUCCESS: int = 0
EXIT_FAILURE: int = 1
EXIT_CONFIG_INVALID: int = 2
EXIT_WORKSPACE_ROOT_UNAVAILABLE: int = 3
EXIT_RUNTIME_NOT_RUNNING: int = 4
EXIT_HEALTH_CHECK_FAILED: int = 5
EXIT_PORT_UNAVAILABLE: int = 6
EXIT_INVALID_USAGE: int = 10
