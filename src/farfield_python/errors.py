"""Error classes translated from @farfield/api and @farfield/protocol."""


class ProtocolValidationError(ValueError):
    def __init__(self, context: str, issues: list[str]):
        self.issues = issues
        super().__init__(f"{context} did not match expected schema. {'; '.join(issues)}")


class AppServerError(Exception):
    pass


class AppServerTransportError(AppServerError):
    pass


class AppServerRpcError(AppServerError):
    def __init__(self, code: int, message: str, data: object = None):
        super().__init__(f"app-server error {code}: {message}")
        self.code = code
        self.data = data


class DesktopIpcError(Exception):
    pass
