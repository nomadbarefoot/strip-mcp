"""All error types for toolgate."""


class ToolGateError(Exception):
    """Base error for all toolgate errors."""


class ServerStartError(ToolGateError):
    """Subprocess failed to start or handshake failed."""


class ServerCrashedError(ToolGateError):
    """Subprocess died mid-session."""


class RemoteRPCError(ToolGateError):
    """JSON-RPC error returned by a remote MCP server."""

    def __init__(self, error: object) -> None:
        self.error = error
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            data = error.get("data")
            detail = f"RPC error {code}: {message}" if code is not None else f"RPC error: {message}"
            if data is not None:
                detail = f"{detail} (data={data!r})"
            super().__init__(detail)
            return
        super().__init__(f"RPC error: {error!r}")


class ToolNotFoundError(ToolGateError):
    """LLM requested a tool name that doesn't exist."""

    def __init__(self, name: str, suggestion: str | None = None) -> None:
        self.name = name
        self.suggestion = suggestion
        msg = f"Tool not found: {name!r}"
        if suggestion:
            msg += f". Did you mean {suggestion!r}?"
        super().__init__(msg)


class ToolCollisionError(ToolGateError):
    """Two servers registered the same tool name (namespace=False collision)."""


class ToolExecutionError(ToolGateError):
    """Tool call returned an error from the MCP server."""

    def __init__(self, tool_name: str, mcp_error: object) -> None:
        self.tool_name = tool_name
        self.mcp_error = mcp_error
        super().__init__(f"Tool {tool_name!r} returned error: {mcp_error}")


class ToolTimeoutError(ToolGateError):
    """Tool call exceeded configured timeout."""

    def __init__(self, tool_name: str, timeout: float) -> None:
        self.tool_name = tool_name
        self.timeout = timeout
        super().__init__(f"Tool {tool_name!r} timed out after {timeout}s")


class SchemaFetchError(ToolGateError):
    """get_schemas called for a non-existent tool."""
